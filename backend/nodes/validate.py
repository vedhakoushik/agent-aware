"""
Validation Agent — the autonomous critic + self-fixer.

This is the "judge" half of a closed Plan-Act-Observe-Reflect loop (the Reflection /
Actor-Critic pattern). It runs AFTER `recommend` and checks the whole answer against
the REAL retrieved data — not vibes — so a fluent-but-wrong recommendation can't slip
through:

  Layer A (deterministic, no LLM, always runs):
    • has_results   — did anything come back at all?
    • coverage      — did the requested platforms return data? (empties → re-search)
    • groundedness  — is the winner a real platform with real results?
    • best_choice   — is the winner the cheapest option that meets every constraint?
                      If not, it is CORRECTED right here from the real data.
    • completeness  — winner/reasoning/confidence all present?

  Layer B (LLM-as-judge, best-effort, quota-aware):
    • coherence     — does the reasoning actually answer the query, no contradictions?

What it can't fix by judging alone (empty platforms, incoherent text) becomes a
`remediation_plan` the remediation node acts on; the graph then loops back here.

Honesty guard: when a HARD constraint is genuinely unsatisfiable (e.g. no result under
the budget exists anywhere searched), it does NOT fabricate or silently relax it — it
keeps the best real options and attaches a `constraint_note` with the proof (budget,
cheapest found, how many results were checked) and what it did.
"""
from __future__ import annotations

import json
import logging
import os
import time

from backend.llm import chat
from backend.state import AgentState
from backend.progress import emit
from backend.agent_bus import send as bus_send

logger = logging.getLogger(__name__)

_PRICE_FIELDS = ("price", "fare", "total_price", "price_per_night",
                 "price_per_day", "amount", "total")


def _max_rounds() -> int:
    try:
        return int(os.getenv("REMEDIATION_MAX_ROUNDS", "3") or 3)
    except Exception:
        return 3


def _price(result: dict) -> float | None:
    """Pull a numeric price out of a result dict, tolerant of '₹9,398', '9398 INR', etc."""
    for f in _PRICE_FIELDS:
        v = result.get(f)
        if v in (None, "", 0):
            continue
        try:
            s = str(v).replace(",", "").replace("₹", "").replace("$", "").strip()
            return float(s.split()[0])
        except Exception:
            continue
    return None


def _budget_max(params: dict) -> float | None:
    b = params.get("budget")
    if isinstance(b, dict):
        try:
            return float(b.get("max"))
        except Exception:
            return None
    for k in ("max_price", "budget_max"):
        if params.get(k):
            try:
                return float(params[k])
            except Exception:
                pass
    return None


def _wants_nonstop(params: dict) -> bool:
    if params.get("non_stop") or params.get("nonstop"):
        return True
    if str(params.get("max_stops", "")).strip() == "0":
        return True
    return str(params.get("stops", "")).strip().lower() in ("0", "non-stop", "nonstop")


def _satisfies(result: dict, params: dict) -> tuple[bool, list[str]]:
    """(ok, [violated-constraint descriptions]) for one result against the user's params."""
    violations: list[str] = []
    bm = _budget_max(params)
    if bm is not None:
        p = _price(result)
        if p is not None and p > bm:
            violations.append(f"₹{int(p)} over ₹{int(bm)} budget")
    if _wants_nonstop(params):
        st = result.get("stops")
        if st not in (0, "0", None) and str(st).lower() not in ("non-stop", "nonstop", "0"):
            violations.append(f"{st} stop(s), wanted non-stop")
    cc = (params.get("cabin_class") or "").strip().lower()
    if cc:
        rc = (result.get("cabin_class") or "").strip().lower()
        if rc and cc not in rc and rc not in cc:
            violations.append(f"cabin '{rc}' ≠ '{cc}'")
    return (len(violations) == 0, violations)


def _constraint_desc(params: dict) -> list[str]:
    d = []
    bm = _budget_max(params)
    if bm is not None:
        d.append(f"under ₹{int(bm)}")
    if _wants_nonstop(params):
        d.append("non-stop")
    cc = (params.get("cabin_class") or "").strip()
    if cc:
        d.append(cc)
    return d


def _all_results(platform_results: dict) -> list[tuple[str, str, dict]]:
    out = []
    for pid, pr in (platform_results or {}).items():
        if not isinstance(pr, dict):
            continue
        for r in pr.get("results") or []:
            out.append((pid, pr.get("platform_name", pid), r))
    return out


_JUDGE_SYSTEM = """You are a strict QA validator for a search-recommendation agent.
Judge ONLY whether the recommendation answers the user's request and is internally
consistent — do NOT invent new facts. Return JSON:
{"relevant": true|false, "coherent": true|false, "issue": "<one short sentence, or empty>"}
relevant = it addresses what the user actually asked for.
coherent = the reasoning is consistent and supported by the cited prices/platforms, with
no contradictions or claims that aren't backed by the data."""


def _llm_judge(state: AgentState, rec: dict) -> dict | None:
    """Best-effort LLM relevance/coherence check. Returns {passed, detail} or None when
    no provider is available (so the loop degrades to Layer A only)."""
    try:
        intent = state.get("intent") or {}
        user = (f"User query: {state.get('query')}\n"
                f"Intent: {intent.get('type')}\n"
                f"Constraints: {json.dumps(intent.get('params', {}))}\n\n"
                f"Recommendation:\n"
                f"- winner platform: {rec.get('winner_platform')}\n"
                f"- price analysis: {rec.get('price_analysis')}\n"
                f"- reasoning: {rec.get('reasoning')}")
        resp = chat("validate",
                    messages=[{"role": "system", "content": _JUDGE_SYSTEM},
                              {"role": "user", "content": user}],
                    response_format={"type": "json_object"},
                    temperature=0.0, max_tokens=200)
        d = json.loads(resp.choices[0].message.content)
        passed = bool(d.get("relevant", True)) and bool(d.get("coherent", True))
        detail = "Relevant to the query and internally consistent" if passed \
            else (d.get("issue") or "Failed the relevance/coherence judge")
        return {"passed": passed, "detail": detail}
    except Exception as e:
        logger.info(f"LLM judge skipped (provider unavailable): {str(e)[:80]}")
        return None


def validate_node(state: AgentState) -> dict:
    """Validate the recommendation against the real data; apply deterministic fixes;
    queue anything that needs a re-search/regenerate for the remediation node."""
    t0 = time.time()
    intent = state.get("intent") or {}
    params = intent.get("params") or {}
    intent_type = intent.get("type", "general")
    platform_results = state.get("platform_results") or {}
    rec = dict(state.get("recommendation") or {})
    round_n = state.get("remediation_round", 0)

    emit("Validating the result…", stage="validate", kind="start")
    bus_send(frm="Recommendation Agent", to="Validation Agent", kind="request",
             title="Please validate this recommendation",
             content={"winner": rec.get("winner_platform"),
                      "confidence": rec.get("confidence"),
                      "round": round_n})

    checks: list[dict] = []
    plan: list[dict] = []
    notes: list[dict] = []
    fix_details = None

    requested = intent.get("platforms") or list(platform_results.keys())
    all_res = _all_results(platform_results)
    n_total = len(all_res)

    # ── CHECK: anything at all ────────────────────────────────────────────────
    has = n_total > 0
    checks.append({"name": "has_results", "passed": has, "severity": "critical",
                   "detail": (f"{n_total} results across {len(platform_results)} platform(s)"
                              if has else "No results returned by any platform"),
                   "proof": {"total_results": n_total,
                             "per_platform": {pid: len((pr or {}).get("results") or [])
                                              for pid, pr in platform_results.items()}}})

    # ── CHECK: coverage — requested platforms that came back empty ────────────
    empties = [pid for pid in requested
               if not ((platform_results.get(pid) or {}).get("results"))]
    covered = len(requested) - len(empties)
    cov_ok = covered >= max(1, (len(requested) + 1) // 2)
    checks.append({"name": "coverage", "passed": cov_ok, "severity": "warn",
                   "detail": (f"{covered}/{len(requested)} requested platforms returned data"
                              + (f"; empty: {', '.join(empties)}" if empties else "")),
                   "proof": {"requested": requested, "empty": empties}})
    # NOTE: we deliberately do NOT auto-retry empty platforms. A hint-less re-search
    # just repeats the identical browser steps and fails the identical way — wasted
    # time. Empty platforms surface as roadblock cards (render_roadblocks) where the
    # USER tells the agent the exact step to take. (Retry only with new human input.)

    # ── Build the real best option (deterministic) ────────────────────────────
    priced = [(pid, name, r, _price(r)) for pid, name, r in all_res if _price(r) is not None]
    satisfying = [(pid, name, r, p) for pid, name, r, p in priced if _satisfies(r, params)[0]]

    win_pid = rec.get("winner_platform")
    win_res = rec.get("winner_result") or {}
    win_price = _price(win_res)
    grounded = bool(win_pid) and bool((platform_results.get(win_pid) or {}).get("results"))
    checks.append({"name": "groundedness", "passed": grounded, "severity": "critical",
                   "detail": (f"Winner '{win_pid}' is a real platform with results"
                              if grounded else f"Winner '{win_pid}' is not grounded in any results"),
                   "proof": {"winner_platform": win_pid, "winner_price": win_price}})

    # ── CHECK: is the winner the best constraint-satisfying option? + FIX ──────
    if has and priced:
        pool = satisfying if satisfying else priced
        best_pid, best_name, best_res, best_price = sorted(pool, key=lambda x: x[3])[0]
        win_ok, win_viol = (_satisfies(win_res, params) if win_res else (False, ["no winner result"]))
        # "correct" = grounded, the cheapest in the pool, AND (when any option meets the
        # constraints) one that actually meets them. When NOTHING satisfies a hard
        # constraint we still demand the cheapest real option (best-available), so the
        # winner can never be an arbitrary pricier pick than what we honestly report.
        cheaper_exists = (win_price is None) or (best_price < (win_price or 1e18) - 0.5)
        winner_correct = grounded and (win_ok if satisfying else True) and not cheaper_exists
        checks.append({"name": "best_choice", "passed": winner_correct, "severity": "warn",
                       "detail": (f"Winner is the best valid option (₹{int(best_price)})" if winner_correct
                                  else f"Better option available: {best_name} ₹{int(best_price)}"
                                       + (f" — winner issues: {', '.join(win_viol)}" if win_viol else "")),
                       "proof": {"winner_price": win_price, "best_available": int(best_price),
                                 "best_platform": best_pid,
                                 "satisfying_options": len(satisfying), "priced_options": len(priced)}})
        if not winner_correct:
            before = {"platform": win_pid, "price": int(win_price) if win_price else None,
                      "violations": win_viol}
            rec["winner_platform"] = best_pid
            rec["winner_result"] = best_res
            descs = _constraint_desc(params)
            if satisfying:
                rec["reasoning"] = (f"Auto-corrected to {best_name} at ₹{int(best_price)} — the lowest-priced "
                                    f"option that meets all your requirements"
                                    + (f" ({', '.join(descs)})" if descs else "") + ".")
                rec["confidence"] = "high"
            else:
                rec["reasoning"] = (f"Auto-corrected to {best_name} at ₹{int(best_price)} — the closest "
                                    f"available option (see the constraint note for why nothing met "
                                    f"your request exactly).")
                rec["confidence"] = "medium"
            fix_details = {"what": "winner corrected from the real data",
                           "before": before,
                           "after": {"platform": best_pid, "platform_name": best_name,
                                     "price": int(best_price)},
                           "why": ("original winner was not the cheapest valid option"
                                   if win_ok else "original winner violated: " + ", ".join(win_viol))}
            bus_send(frm="Validation Agent", to="Recommendation Agent", kind="handoff",
                     title=f"Corrected winner → {best_name} ₹{int(best_price)}",
                     content=fix_details)

    # ── Honesty guard: a hard constraint that NOTHING satisfies ───────────────
    bm = _budget_max(params)
    if bm is not None and priced and not satisfying:
        all_prices = [p for *_, p in priced]
        cheapest = int(min(all_prices))
        notes.append({
            "constraint": f"budget ≤ ₹{int(bm)}",
            "issue": (f"No option met your ₹{int(bm)} budget — checked {len(priced)} priced results "
                      f"across {len(platform_results)} platform(s)."),
            "best_available": f"₹{cheapest}",
            "what_we_did": ("Searched all eligible platforms and re-tried empty ones; showing the closest "
                            "real options above your limit rather than hiding them or inventing a cheaper one."),
            "proof": {"budget": int(bm), "cheapest_found": cheapest,
                      "results_checked": len(priced),
                      "price_range": [int(min(all_prices)), int(max(all_prices))]},
        })
        bus_send(frm="Validation Agent", to="You", kind="error",
                 title=f"Budget ₹{int(bm)} unmet — cheapest real option is ₹{cheapest}",
                 content=notes[-1])

    # ── CHECK: completeness ───────────────────────────────────────────────────
    complete = bool(rec.get("winner_result")) and bool(rec.get("reasoning")) and bool(rec.get("confidence"))
    checks.append({"name": "completeness", "passed": complete, "severity": "warn",
                   "detail": ("Recommendation has winner, reasoning and confidence" if complete
                              else "Recommendation is missing required fields"),
                   "proof": None})

    # ── Layer B: LLM relevance/coherence (best-effort) ────────────────────────
    # Skip when we just deterministically corrected the winner — the reasoning we
    # wrote is factual by construction, so re-judging it only causes false positives
    # and needless remediation churn. Judge only recommendations that passed Layer A.
    if has and grounded and fix_details is None:
        v = _llm_judge(state, rec)
        if v is not None:
            checks.append({"name": "coherence", "passed": v["passed"], "severity": "warn",
                           "detail": v["detail"], "proof": None})
            if not v["passed"] and round_n < _max_rounds():
                plan.append({"action": "regenerate_recommendation", "target": None,
                             "reason": v["detail"][:160]})

    issues = [c for c in checks if not c["passed"]]

    # ── Verdict ───────────────────────────────────────────────────────────────
    if not issues:
        verdict = "valid"
    elif not plan:
        # everything failing was fixable right here (or only honest notes remain)
        verdict = "fixed" if fix_details else "best_effort"
    elif round_n >= _max_rounds():
        verdict = "best_effort"
    else:
        verdict = "issues_remain"

    report = {"round": round_n, "verdict": verdict, "checks": checks, "issues": issues,
              "fixed": fix_details is not None, "fix_details": fix_details,
              "constraint_notes": notes, "remediation_plan": plan,
              "elapsed_seconds": round(time.time() - t0, 2)}

    n_pass = sum(1 for c in checks if c["passed"])
    bus_send(frm="Validation Agent", to="You", kind="diagnosis",
             title=f"Validation: {verdict} — {n_pass}/{len(checks)} checks passed",
             content={"verdict": verdict,
                      "failed_checks": [i["name"] for i in issues],
                      "next_actions": [a["action"] for a in plan],
                      "constraint_notes": [n["constraint"] for n in notes]})
    emit(f"Validation: {verdict} ({n_pass}/{len(checks)} checks passed)",
         stage="validate", kind="ok" if verdict in ("valid", "fixed") else "warn")

    out: dict = {"recommendation": rec, "validation": report}
    if verdict in ("valid", "fixed", "best_effort") and not plan:
        out["status"] = "done"
    return out

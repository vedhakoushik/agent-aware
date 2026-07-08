"""
Remediation Agent — the "act" half of the autonomous self-healing loop.

The validator (validate.py) judges the answer and applies the cheap deterministic
fixes (e.g. correcting the winner) itself. Anything that needs real WORK to fix —
re-fetching an empty platform, regenerating an incoherent recommendation — it leaves
as a `remediation_plan`. This node executes that plan, with NO user involvement:

  • research_platform        → re-run a platform that came back empty (reuses the
                               existing human-in-the-loop retry path, headless)
  • regenerate_recommendation→ flag a re-pipe so the recommend node runs again

It increments the bounded round counter and appends to `remediation_log` (the
timeline the frontend shows). The graph then routes back: to `aggregate` if new data
arrived, to `recommend` to regenerate, or straight back to `validate`.
"""
from __future__ import annotations

import logging

from backend.state import AgentState
from backend.progress import emit
from backend.agent_bus import send as bus_send

logger = logging.getLogger(__name__)


def remediate_node(state: AgentState) -> dict:
    validation = state.get("validation") or {}
    plan = validation.get("remediation_plan") or []
    round_n = state.get("remediation_round", 0) + 1
    intent = state.get("intent") or {}
    params = intent.get("params") or {}
    intent_type = intent.get("type", "general")
    platform_results = dict(state.get("platform_results") or {})
    log = list(state.get("remediation_log") or [])

    emit(f"Auto-fixing the result (round {round_n})…", stage="remediate", kind="start")
    bus_send(frm="Validation Agent", to="Remediation Agent", kind="dispatch",
             title=f"Execute {len(plan)} fix action(s) — round {round_n}",
             content={"actions": [a.get("action") for a in plan]})

    from backend.nodes.search import retry_platform_with_hint

    actions_done: list[dict] = []
    data_changed = False
    regen = False

    for act in plan:
        a = act.get("action")
        if a == "research_platform":
            pid = act.get("target")
            bus_send(frm="Remediation Agent", to="Search Coordinator", kind="dispatch",
                     title=f"Re-search {pid} (was empty)",
                     content={"platform": pid, "reason": act.get("reason")})
            try:
                pr = retry_platform_with_hint(pid, params, intent_type, hint="", headed=False)
                n = len((pr or {}).get("results") or [])
                if pr:
                    platform_results[pid] = pr
                if n > 0:
                    data_changed = True
                actions_done.append({"action": "research_platform", "target": pid,
                                     "outcome": f"{n} result(s)"})
                bus_send(frm=f"{(pr or {}).get('platform_name', pid)} Agent",
                         to="Remediation Agent", kind="data",
                         title=f"Re-search returned {n} result(s)",
                         content={"platform": pid, "count": n})
            except Exception as e:
                actions_done.append({"action": "research_platform", "target": pid,
                                     "outcome": f"failed: {str(e)[:80]}"})
                logger.warning(f"Remediation re-search failed for {pid}: {e}")
        elif a == "regenerate_recommendation":
            regen = True
            actions_done.append({"action": "regenerate_recommendation", "target": None,
                                 "outcome": "queued recommendation regeneration"})

    log.append({"round": round_n,
                "issues": [i.get("name") for i in validation.get("issues", [])],
                "actions": actions_done,
                "data_changed": data_changed,
                "regen": regen})

    n_ok = sum(1 for x in actions_done if "failed" not in x["outcome"])
    emit(f"Round {round_n}: applied {n_ok}/{len(actions_done)} fix(es)",
         stage="remediate", kind="ok")
    bus_send(frm="Remediation Agent", to="Validation Agent", kind="data",
             title=f"Round {round_n} done — re-validating",
             content={"actions": actions_done, "data_changed": data_changed, "regen": regen})

    return {"remediation_round": round_n, "remediation_log": log,
            "platform_results": platform_results}

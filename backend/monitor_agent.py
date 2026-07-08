"""
Monitor agent — a supervisor over the browser worker-agents.

Each platform search may spin up a browser-use agent (its own "tab"). When one gets
stuck, this agent reads that tab's step history + the exact error, figures out WHY it
failed, and produces a concrete fix: a one-line instruction that would unblock it (the
same kind of hint a human would give), plus a classification so the UI knows whether the
failure is even fixable (a CAPTCHA wall isn't; a missed button is).

It's a normal LLM call (the reasoning layer), with a fast heuristic fallback so it still
classifies failures when no model is available. Self-healing without the slow guesswork.
"""
from __future__ import annotations

import json
import logging

from backend.llm import chat
from backend.agent_bus import send as bus_send

logger = logging.getLogger(__name__)

_SYSTEM = """You are a debugging supervisor for browser-automation agents. A worker agent
tried to search a website and got stuck. Given its step history and the error, diagnose the
root cause and give the SINGLE most useful next instruction to unblock it.

Classify "category" as one of:
- bot_block   : CAPTCHA / "verify you're human" / forced login — NOT fixable by guidance.
- navigation  : missed a button/field/tab, dismissed-popup needed — fixable with a hint.
- timeout     : page too slow / never loaded — a retry or "wait longer" may help.
- no_results  : reached results but the site genuinely has none for this query.
- unknown     : unclear.

Return JSON: {
  "category": "...",
  "root_cause": "<short why-it-failed>",
  "diagnosis": "<one plain-English sentence for the user>",
  "fixable": true|false,
  "suggested_hint": "<one concrete instruction to retry with, or null if bot_block/no_results>",
  "confidence": "high|medium|low"
}"""


def _heuristic(error: str) -> dict:
    e = (error or "").lower()
    if any(k in e for k in ("can't be reached", "cant be reached", "err_http2", "err_connection",
                            "err_name_not_resolved", "protocol_error", "site can", "404",
                            "this page isn", "temporarily down")):
        return {"category": "navigation", "root_cause": "The deep link wouldn't load (bad/expired URL).",
                "diagnosis": "The pre-filled link didn't load — likely a malformed URL (e.g. missing dates).",
                "fixable": True,
                "suggested_hint": "open the site's homepage and run the search from the form there",
                "confidence": "high"}
    if any(k in e for k in ("captcha", "verify", "human", "robot", "sign in", "login", "blocked")):
        return {"category": "bot_block", "root_cause": "Site demanded human verification / login.",
                "diagnosis": "The site put up a bot check or login wall, which can't be bypassed.",
                "fixable": False, "suggested_hint": None, "confidence": "high"}
    if "timeout" in e or "timed out" in e:
        return {"category": "timeout", "root_cause": "Page didn't load in time.",
                "diagnosis": "The page was too slow to load.",
                "fixable": True, "suggested_hint": "retry — the page may load faster on a second attempt",
                "confidence": "medium"}
    if "max_steps" in e or "could not be completed" in e:
        return {"category": "navigation", "root_cause": "Ran out of steps before reaching results.",
                "diagnosis": "The agent couldn't find the path to the results in time.",
                "fixable": True, "suggested_hint": "dismiss any popup first, then click the main Search button",
                "confidence": "low"}
    return {"category": "unknown", "root_cause": "Unrecognized failure.",
            "diagnosis": "The browser couldn't reach a results list.",
            "fixable": True, "suggested_hint": "click the Search button, then read the first results",
            "confidence": "low"}


def analyze_failure(platform_name: str, params: dict, run: dict | None) -> dict:
    """Diagnose one stuck browser run → {category, root_cause, diagnosis, fixable,
    suggested_hint, confidence}. Always returns a usable dict."""
    run = run or {}
    error = run.get("error") or "No specific error; the agent ran out of steps."
    steps = run.get("steps", [])

    _agent = f"{platform_name} Agent"
    bus_send(frm=_agent, to="Monitor Agent", kind="error",
             title=f"{platform_name} agent is stuck — asking for help",
             content={"error": error[:300], "steps_taken": len(steps)})

    step_lines = "\n".join(
        f'{s.get("n","?")}. goal="{s.get("goal","")}" action="{s.get("action","")}" '
        f'result="{s.get("eval","")}" url={s.get("url","")}'
        for s in steps[-8:]
    ) or "(no steps recorded)"

    user = (f'Platform: {platform_name}\n'
            f'Searching for: {json.dumps(params)}\n'
            f'Steps taken:\n{step_lines}\n\n'
            f'Final error: {error}')

    try:
        resp = chat("monitor",
                    messages=[{"role": "system", "content": _SYSTEM},
                              {"role": "user", "content": user}],
                    response_format={"type": "json_object"},
                    temperature=0.1, max_tokens=400)
        data = json.loads(resp.choices[0].message.content)
        # Normalize / guard the fields we rely on.
        out = {
            "category": str(data.get("category", "unknown")),
            "root_cause": str(data.get("root_cause", ""))[:200],
            "diagnosis": str(data.get("diagnosis", ""))[:240],
            "fixable": bool(data.get("fixable", True)),
            "suggested_hint": (str(data["suggested_hint"])[:160]
                               if data.get("suggested_hint") else None),
            "confidence": str(data.get("confidence", "medium")),
            "by": "monitor-agent",
        }
    except Exception as e:
        logger.warning(f"Monitor agent fell back to heuristic ({platform_name}): {e}")
        out = _heuristic(error)
        out["by"] = "heuristic"

    bus_send(frm="Monitor Agent", to=_agent, kind="diagnosis",
             title=f"Diagnosis: {out.get('category', 'unknown')}"
                   + (" (fixable)" if out.get("fixable") else " (not fixable)"),
             content={"diagnosis": out.get("diagnosis"),
                      "suggested_fix": out.get("suggested_hint"),
                      "by": out.get("by")})
    return out

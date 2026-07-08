import logging
import os
import time
from langgraph.graph import StateGraph, END

from backend.state import AgentState
from backend.diagnostics import get_diagnostics
from backend.agent_bus import send as bus_send
from backend.nodes.intent import parse_intent_node
from backend.nodes.search import search_platforms_node
from backend.nodes.aggregate import aggregate_node
from backend.nodes.compare import compare_node
from backend.nodes.segregate import segregate_node
from backend.nodes.insights import insights_node
from backend.nodes.recommend import recommend_node
from backend.nodes.validate import validate_node
from backend.nodes.remediate import remediate_node


def _max_remediation_rounds() -> int:
    try:
        return int(os.getenv("REMEDIATION_MAX_ROUNDS", "3") or 3)
    except Exception:
        return 3

logger = logging.getLogger(__name__)


# Each pipeline node = one agent that hands its output to the next. This maps a node
# to (its agent name, the next agent it hands off to) so the Agent Communication view
# shows the stages talking to each other, with a one-line summary of the data passed.
_NODE_FLOW = {
    "aggregate": ("Aggregator", "Comparison Agent"),
    "compare":   ("Comparison Agent", "Grouping Agent"),
    "segregate": ("Grouping Agent", "Insights Agent"),
    "insights":  ("Insights Agent", "Recommendation Agent"),
    "recommend": ("Recommendation Agent", "You"),
}


def _handoff_summary(name: str, delta: dict) -> dict:
    """A compact, readable description of what a node produced — the payload shown
    on the bus when this stage hands off to the next."""
    d = delta or {}
    if name == "aggregate":
        return {"normalized_results": len(d.get("normalized") or [])}
    if name == "compare":
        c = d.get("comparison") or {}
        return {"platforms_compared": c.get("platforms_with_results"),
                "total_results": c.get("total_results")}
    if name == "segregate":
        seg = d.get("segments") or {}
        groups = seg.get("groups") if isinstance(seg, dict) else None
        return {"groups": len(groups) if groups else 0}
    if name == "insights":
        ins = d.get("insights") or {}
        return {"badges": len(ins.get("badges") or {}),
                "takeaways": len(ins.get("takeaways") or [])}
    if name == "recommend":
        rec = d.get("recommendation") or {}
        return {"winner": rec.get("winner_platform"),
                "confidence": rec.get("confidence"),
                "reasoning": (rec.get("reasoning") or "")[:200]}
    return {}


def _timed(name: str, fn):
    """Wrap a node so its wall-clock time is recorded in the run diagnostics AND its
    handoff to the next stage is published on the Agent Communication bus."""
    def _wrapped(state: AgentState) -> dict:
        t0 = time.monotonic()
        delta = None
        try:
            delta = fn(state)
            return delta
        finally:
            get_diagnostics().record_node(name, time.monotonic() - t0)
            flow = _NODE_FLOW.get(name)
            if flow and isinstance(delta, dict):
                frm, to = flow
                kind = "data" if to != "You" else "response"
                title = (f"{frm} → final recommendation" if to == "You"
                         else f"{frm} hands off to {to}")
                bus_send(frm=frm, to=to, kind=kind, title=title,
                         content=_handoff_summary(name, delta))
    return _wrapped


def _route_after_intent(state: AgentState) -> str:
    """Route: if clarification needed or error, go to END; else search."""
    if state.get("status") == "error":
        return END
    intent = state.get("intent")
    if intent and intent.get("clarification_needed"):
        return END  # UI will display the clarification question
    return "search_platforms"


def _total_results(state: AgentState) -> int:
    prs = state.get("platform_results") or {}
    return sum(len((r or {}).get("results") or []) for r in prs.values() if isinstance(r, dict))


def _route_after_search(state: AgentState) -> str:
    """Skip the whole analysis pipeline (aggregate→…→recommend→validate) when NO
    platform returned any results — running compare/insights/recommend on nothing
    just wastes time and fabricates an empty 'best pick'. Go straight to a clean
    no-results end state instead."""
    if state.get("status") == "error":
        return "no_results"
    return "aggregate" if _total_results(state) > 0 else "no_results"


def no_results_node(state: AgentState) -> dict:
    """Terminal state when a search found nothing — no analysis, a clear message."""
    n = len(state.get("platform_results") or {})
    bus_send(frm="Search Coordinator", to="You", kind="error",
             title="No results on any platform",
             content=f"Searched {n} platform(s); none returned usable results.")
    from backend.progress import emit
    emit("No results found — try a different query or retry a platform.",
         stage="recommend", kind="warn")
    return {"status": "no_results", "recommendation": None}


def _route_after_validate(state: AgentState) -> str:
    """Autonomous loop guard: if the validator queued fixes AND we're under the round
    cap, remediate; otherwise finish (the deterministic fixes are already applied)."""
    if state.get("status") == "error":
        return END
    report = state.get("validation") or {}
    plan = report.get("remediation_plan") or []
    if plan and state.get("remediation_round", 0) < _max_remediation_rounds():
        return "remediate"
    return END


def _route_after_remediate(state: AgentState) -> str:
    """After acting on the plan: re-pipe from aggregate if new data arrived, re-run
    just the recommender if we asked for a regeneration, else straight back to validate."""
    log = state.get("remediation_log") or []
    last = log[-1] if log else {}
    if last.get("data_changed"):
        return "aggregate"
    if last.get("regen"):
        return "recommend"
    return "validate"


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("parse_intent", _timed("parse_intent", parse_intent_node))
    builder.add_node("search_platforms", _timed("search_platforms", search_platforms_node))
    builder.add_node("aggregate", _timed("aggregate", aggregate_node))
    builder.add_node("compare", _timed("compare", compare_node))
    builder.add_node("segregate", _timed("segregate", segregate_node))
    builder.add_node("insights", _timed("insights", insights_node))
    builder.add_node("recommend", _timed("recommend", recommend_node))
    builder.add_node("validate", _timed("validate", validate_node))
    builder.add_node("remediate", _timed("remediate", remediate_node))
    builder.add_node("no_results", _timed("no_results", no_results_node))

    builder.set_entry_point("parse_intent")

    builder.add_conditional_edges(
        "parse_intent",
        _route_after_intent,
        {END: END, "search_platforms": "search_platforms"},
    )

    # Short-circuit to a clean no-results end state instead of running the analysis
    # pipeline (compare/insights/recommend) on zero results.
    builder.add_conditional_edges(
        "search_platforms", _route_after_search,
        {"aggregate": "aggregate", "no_results": "no_results"},
    )
    builder.add_edge("no_results", END)
    builder.add_edge("aggregate", "compare")
    builder.add_edge("compare", "segregate")
    builder.add_edge("segregate", "insights")
    builder.add_edge("insights", "recommend")
    # ── Autonomous validation & self-healing loop ──
    # recommend → validate → (remediate → re-pipe)* → END, bounded by REMEDIATION_MAX_ROUNDS.
    builder.add_edge("recommend", "validate")
    builder.add_conditional_edges(
        "validate", _route_after_validate,
        {"remediate": "remediate", END: END},
    )
    builder.add_conditional_edges(
        "remediate", _route_after_remediate,
        {"aggregate": "aggregate", "recommend": "recommend", "validate": "validate"},
    )

    return builder.compile()


# Singleton — compile once
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run_search(query: str) -> AgentState:
    """Run a full search pipeline synchronously and return the final state."""
    graph = get_graph()
    get_diagnostics().start()
    try:
        from backend.progress import clear_cancel
        clear_cancel()   # a fresh run is never pre-cancelled
    except Exception:
        pass
    try:
        from backend.agent_bus import get_bus
        get_bus().start()
    except Exception:
        pass
    initial_state: AgentState = {
        "query": query,
        "intent": None,
        "platform_results": {},
        "normalized": [],
        "comparison": None,
        "segments": None,
        "insights": None,
        "recommendation": None,
        "messages": [],
        "status": "parsing",
        "error": None,
        "validation": None,
        "remediation_round": 0,
        "remediation_log": [],
    }

    from backend.llm import langfuse_enabled, flush_traces
    if langfuse_enabled():
        from langfuse import get_client
        with get_client().start_as_current_observation(
            name=f"search: {query[:80]}",
            as_type="agent",
            input={"query": query},
            metadata={"query": query},
        ) as span:
            result = graph.invoke(initial_state)
            span.update(
                output={
                    "status": result.get("status"),
                    "platforms_searched": list(result.get("platform_results", {}).keys()),
                    "recommendation": result.get("recommendation"),
                }
            )
    else:
        result = graph.invoke(initial_state)

    # Ship any buffered Langfuse traces for this search (no-op if tracing is off).
    try:
        flush_traces()
    except Exception:
        pass

    # Attach the performance/outcome diagnostics so the UI can show "what was slow"
    # and surface any per-platform roadblocks for the user to help with.
    diag = get_diagnostics()
    diag.finish()
    try:
        result["diagnostics"] = diag.snapshot()
    except Exception:
        pass
    # Snapshot the live browser-use runs (steps + any roadblock error) so the UI
    # can keep showing them after the search completes.
    try:
        from backend.browser_tracker import get_browser_tracker
        result["browser_runs"] = get_browser_tracker().snapshot()
    except Exception:
        pass
    # Snapshot the inter-agent communication log so the UI can keep showing the
    # "how the agents talked to each other" feed after the search completes.
    try:
        from backend.agent_bus import get_bus
        result["agent_comms"] = get_bus().snapshot()
    except Exception:
        pass
    return result

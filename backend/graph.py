import logging
from langgraph.graph import StateGraph, END

from backend.state import AgentState
from backend.nodes.intent import parse_intent_node
from backend.nodes.search import search_platforms_node
from backend.nodes.aggregate import aggregate_node
from backend.nodes.compare import compare_node
from backend.nodes.segregate import segregate_node
from backend.nodes.insights import insights_node
from backend.nodes.recommend import recommend_node

logger = logging.getLogger(__name__)


def _route_after_intent(state: AgentState) -> str:
    """Route: if clarification needed or error, go to END; else search."""
    if state.get("status") == "error":
        return END
    intent = state.get("intent")
    if intent and intent.get("clarification_needed"):
        return END  # UI will display the clarification question
    return "search_platforms"


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("parse_intent", parse_intent_node)
    builder.add_node("search_platforms", search_platforms_node)
    builder.add_node("aggregate", aggregate_node)
    builder.add_node("compare", compare_node)
    builder.add_node("segregate", segregate_node)
    builder.add_node("insights", insights_node)
    builder.add_node("recommend", recommend_node)

    builder.set_entry_point("parse_intent")

    builder.add_conditional_edges(
        "parse_intent",
        _route_after_intent,
        {END: END, "search_platforms": "search_platforms"},
    )

    builder.add_edge("search_platforms", "aggregate")
    builder.add_edge("aggregate", "compare")
    builder.add_edge("compare", "segregate")
    builder.add_edge("segregate", "insights")
    builder.add_edge("insights", "recommend")
    builder.add_edge("recommend", END)

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
    }
    result = graph.invoke(initial_state)
    # Ship any buffered Langfuse traces for this search (no-op if tracing is off).
    try:
        from backend.llm import flush_traces
        flush_traces()
    except Exception:
        pass
    return result

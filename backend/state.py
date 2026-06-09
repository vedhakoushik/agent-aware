from typing import TypedDict, Annotated, Optional
from langgraph.graph.message import add_messages


def merge_dicts(a: dict, b: dict) -> dict:
    return {**a, **b}


class SearchIntent(TypedDict):
    type: str                  # flight, hotel, event, restaurant, product, train, bus, car_rental, general
    raw_query: str
    params: dict               # origin, destination, date, budget, location, etc — dynamic per type
    platforms: list            # list of platform IDs selected for this query
    clarification_needed: bool
    clarification_question: Optional[str]


class PlatformResult(TypedDict):
    platform_id: str
    platform_name: str
    icon: str
    results: list              # list of normalized result dicts
    raw_snippets: list         # raw search text before LLM parsing
    error: Optional[str]
    elapsed_seconds: float


class ComparisonEntry(TypedDict):
    platform_id: str
    platform_name: str
    best_result: dict          # the top result from this platform
    count: int                 # total results found
    min_price: Optional[float]
    max_price: Optional[float]
    avg_price: Optional[float]
    compare_type: str          # booking type this platform was compared on (e.g. "Standard Room")
    type_matched: bool         # False if this platform had no result of compare_type (stats fall back to all its results)


class Recommendation(TypedDict):
    winner_platform: str
    winner_result: dict
    reasoning: str
    price_analysis: str
    alternatives: list
    confidence: str            # high, medium, low


class AgentState(TypedDict):
    query: str
    intent: Optional[SearchIntent]
    platform_results: Annotated[dict, merge_dicts]   # platform_id -> PlatformResult
    normalized: list                                  # all results in common schema
    comparison: Optional[dict]                        # per-platform summary
    segments: Optional[dict]                          # results grouped by booking type, compared per-group
    insights: Optional[dict]                          # comparison matrix, badges, takeaways, trade-offs
    recommendation: Optional[Recommendation]
    messages: Annotated[list, add_messages]
    status: str                                       # parsing, searching, aggregating, comparing, done, error
    error: Optional[str]

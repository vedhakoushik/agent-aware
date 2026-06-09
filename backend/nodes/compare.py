import logging
from typing import Optional

from backend.state import AgentState
from backend.booking_type import dominant_type
from backend.progress import emit

logger = logging.getLogger(__name__)


def _platform_summary(platform_id: str, platform_results: dict, all_normalized: list,
                       compare_type: str = "") -> dict:
    """Build a per-platform summary with price stats and best result.

    When `compare_type` is set (e.g. "Standard Room", "Economy"), the platform's
    stats and best pick are computed from ONLY the results matching that type —
    so platforms are compared like-for-like rather than picking each platform's
    overall cheapest result regardless of what kind of room/seat/ticket it is.
    Falls back to all of the platform's results if it has none of that type.
    """
    presult = platform_results.get(platform_id, {})
    if isinstance(presult, dict):
        platform_name = presult.get("platform_name", platform_id)
        icon = presult.get("icon", "🔍")
        error = presult.get("error")
        elapsed = presult.get("elapsed_seconds", 0)
    else:
        platform_name = platform_id
        icon = "🔍"
        error = None
        elapsed = 0

    platform_items = [r for r in all_normalized if r.get("_platform_id") == platform_id]

    type_matched = True
    if compare_type:
        same_type = [r for r in platform_items if r.get("_booking_type") == compare_type]
        if same_type:
            platform_items = same_type
        else:
            type_matched = False  # this platform has no result of the compared type

    prices = [r["_price_numeric"] for r in platform_items if r.get("_price_numeric") is not None]

    best = None
    if platform_items:
        # Sort by price ascending if available, else keep first
        sortable = [r for r in platform_items if r.get("_price_numeric") is not None]
        if sortable:
            best = min(sortable, key=lambda r: r["_price_numeric"])
        else:
            best = platform_items[0]

    return {
        "platform_id": platform_id,
        "platform_name": platform_name,
        "icon": icon,
        "count": len(platform_items),
        "best_result": best,
        "min_price": min(prices) if prices else None,
        "max_price": max(prices) if prices else None,
        "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
        "error": error,
        "elapsed_seconds": elapsed,
        "compare_type": compare_type,
        "type_matched": type_matched,
    }


def _rank_platforms(summaries: list[dict]) -> list[dict]:
    """Rank platform summaries: lowest price first, errors last."""
    def sort_key(s):
        if s.get("error") and not s.get("best_result"):
            return (2, float("inf"))
        if s.get("min_price") is not None:
            return (0, s["min_price"])
        if s.get("best_result"):
            return (1, 0)
        return (2, float("inf"))

    return sorted(summaries, key=sort_key)


def compare_node(state: AgentState) -> dict:
    """LangGraph node: build per-platform comparison with price stats."""
    platform_results = state.get("platform_results", {})
    normalized = state.get("normalized", [])
    intent_type = (state.get("intent", {}) or {}).get("type", "general")

    # Anchor the whole comparison on ONE booking type — the most common one across
    # all platforms (e.g. most results are "Standard Room" → compare Standard Rooms
    # everywhere). This stops the engine from calling a platform "cheapest" just
    # because its cheapest listing happens to be a smaller/lower-tier booking than
    # everyone else's. Categories without a booking-type concept (or results that
    # never expose one) fall through with compare_type="" and behave as before.
    compare_type = dominant_type(normalized, intent_type)
    emit("Comparing options across platforms…", stage="compare", kind="start")

    summaries = [
        _platform_summary(pid, platform_results, normalized, compare_type)
        for pid in platform_results
    ]

    ranked = _rank_platforms(summaries)

    # Headline KPI stats ("Lowest price" / "Average") summarize EVERY priced
    # result found across all platforms — not just the dominant-type subset.
    # Scoping these to compare_type made them degenerate (min == avg) whenever
    # only one or two results carried that exact type, which is misleading on the
    # top-of-page metrics strip. The per-platform ranking above still uses
    # compare_type for like-for-like fairness; these are a broad "what's out
    # there" summary, so they use all prices.
    all_prices = [r["_price_numeric"] for r in normalized
                  if r.get("_price_numeric") is not None]
    # Within-type prices, for an honest average among directly-comparable options.
    type_prices = (
        [r["_price_numeric"] for r in normalized
         if r.get("_booking_type") == compare_type and r.get("_price_numeric") is not None]
        if compare_type else []
    )
    avg_basis = type_prices if len(type_prices) >= 2 else all_prices

    overall = {
        "total_results": len(normalized),
        "platforms_searched": len(platform_results),
        "platforms_with_results": sum(1 for s in summaries if s["count"] > 0),
        "overall_min_price": min(all_prices) if all_prices else None,
        "overall_max_price": max(all_prices) if all_prices else None,
        "overall_avg_price": round(sum(avg_basis) / len(avg_basis)) if avg_basis else None,
        "priced_results": len(all_prices),
        "ranked_platforms": ranked,
        "compare_type": compare_type,
    }

    logger.info(
        f"Comparison: {overall['total_results']} results"
        + (f", anchored on '{compare_type}'" if compare_type else "")
        + f", price range {overall['overall_min_price']}–{overall['overall_max_price']}"
    )
    return {"comparison": overall, "status": "recommending"}

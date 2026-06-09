import logging
import re
from typing import Optional

from backend.state import AgentState
from backend.memory.store import store_results
from backend.booking_type import extract_booking_type

logger = logging.getLogger(__name__)


def _extract_price(value) -> Optional[float]:
    """Robustly extract a numeric price from any value."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace("₹", "").replace("$", "").replace("€", "").strip()
    match = re.search(r"[\d]+(?:\.\d+)?", s)
    if match:
        return float(match.group())
    return None


def _normalize_result(result: dict, platform_id: str, platform_name: str, icon: str, intent_type: str = "general") -> dict:
    """Add platform metadata and normalize price field on any result dict."""
    normalized = dict(result)
    normalized["_platform_id"] = platform_id
    normalized["_platform_name"] = platform_name
    normalized["_platform_icon"] = icon
    normalized["_price_numeric"] = _extract_price(result.get("price") or result.get("price_per_night") or result.get("total_price"))
    # Tag with the booking type (room type / cabin class / seat class / …) so the
    # comparison stage can later line up similar bookings across platforms instead
    # of pitting a Standard Room against a Suite.
    normalized["_booking_type"] = extract_booking_type(result, intent_type)
    return normalized


def aggregate_node(state: AgentState) -> dict:
    """LangGraph node: flatten and normalize all platform results."""
    platform_results = state.get("platform_results", {})
    intent = state.get("intent", {})
    intent_type = intent.get("type", "general")
    params = intent.get("params", {})

    normalized = []
    for pid, presult in platform_results.items():
        if isinstance(presult, dict):
            results = presult.get("results", [])
            name = presult.get("platform_name", pid)
            icon = presult.get("icon", "🔍")
            for r in results:
                normalized.append(_normalize_result(r, pid, name, icon, intent_type))

            # Store in ChromaDB for price history
            if results:
                try:
                    store_results(intent_type, params, pid, results)
                except Exception as e:
                    logger.warning(f"Memory store failed: {e}")

    logger.info(f"Aggregated {len(normalized)} total results from {len(platform_results)} platforms")
    return {"normalized": normalized, "status": "comparing"}

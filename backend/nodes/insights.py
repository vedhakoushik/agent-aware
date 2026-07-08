"""
Insights node — the comparison intelligence engine.

Takes per-platform results and produces:
  1. comparison_matrix : dimension × platform grid of real values
  2. dimension_winners : which platform wins each factor (price, rating, amenities…)
  3. platform_badges   : smart tags per platform (Cheapest, Best Value, Top Rated…)
  4. key_takeaways     : AI-written trade-off bullets ("Booking is ₹500 cheaper but
                         Agoda includes free breakfast")
  5. value_scores      : a 0-100 composite score per platform's best option
"""
import json
import logging
import os
import re

from backend.llm import chat
from backend.state import AgentState
from backend.progress import emit
from backend.dimensions import discover_dimensions

logger = logging.getLogger(__name__)

_PLATFORMS_CACHE = None


def _load_dimensions(intent_type: str) -> list[dict]:
    global _PLATFORMS_CACHE
    if _PLATFORMS_CACHE is None:
        import yaml
        path = os.path.join(os.path.dirname(__file__), "../../config/platforms.yaml")
        with open(os.path.normpath(path), encoding="utf-8") as f:
            _PLATFORMS_CACHE = yaml.safe_load(f)
    cat = _PLATFORMS_CACHE.get("categories", {}).get(intent_type, {})
    return cat.get("comparison_dimensions", [])


# ── Value coercion ────────────────────────────────────────────

def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _truthy(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "yes", "1", "included", "free", "available"):
        return True
    if s in ("false", "no", "0", "not included", "unavailable", "n/a", ""):
        return False
    return None


def _duration_to_minutes(v):
    """Parse '2h 15m', '02:10', '1 h 5 m' → minutes."""
    if v is None:
        return None
    s = str(v).lower()
    h = re.search(r"(\d+)\s*h", s)
    m = re.search(r"(\d+)\s*m", s)
    if h or m:
        return (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)
    colon = re.search(r"(\d+):(\d+)", s)
    if colon:
        return int(colon.group(1)) * 60 + int(colon.group(2))
    return None


# ── Matrix + winner computation ───────────────────────────────

def _cell_value(result: dict, dim: dict):
    """Extract the comparable value of one dimension from one result."""
    key = dim["key"]
    raw = result.get(key)
    # alias common keys
    if raw is None:
        aliases = {
            "price": ["price", "fare", "total_price"],
            "rating": ["rating", "stars", "score"],
            "duration": ["duration", "travel_time"],
        }.get(key, [])
        for a in aliases:
            if result.get(a) is not None:
                raw = result.get(a)
                break
    return raw


def _comparable(raw, dtype):
    if dtype == "price":
        return _num(raw)
    if dtype in ("rating", "number"):
        return _num(raw)
    if dtype == "duration":
        return _duration_to_minutes(raw)
    if dtype == "bool":
        return _truthy(raw)
    return raw  # text


def _build_matrix(platform_summaries: list[dict], dims: list[dict]) -> dict:
    """
    Build {dimension_key: {platform_id: {raw, comparable, is_winner}}}
    plus dimension_winners {dimension_key: platform_id}.
    """
    matrix = {}
    winners = {}

    for dim in dims:
        key, dtype, better = dim["key"], dim["type"], dim.get("better", "none")
        row = {}
        comparables = {}
        for ps in platform_summaries:
            pid = ps["platform_id"]
            best = ps.get("best_result") or {}
            raw = _cell_value(best, dim)
            comp = _comparable(raw, dtype)
            row[pid] = {"raw": raw, "comparable": comp, "is_winner": False}
            if comp is not None:
                comparables[pid] = comp

        # Determine the winner for this dimension
        win_pid = None
        if comparables:
            if better == "lower":
                win_pid = min(comparables, key=comparables.get)
            elif better == "higher":
                win_pid = max(comparables, key=comparables.get)
            elif better == "true":
                trues = [p for p, c in comparables.items() if c is True]
                win_pid = trues[0] if trues else None
            elif better == "more":
                # longer text / more items = more features
                win_pid = max(comparables, key=lambda p: len(str(comparables[p])))
        if win_pid:
            row[win_pid]["is_winner"] = True
            winners[key] = win_pid

        matrix[key] = row

    return {"matrix": matrix, "dimension_winners": winners}


# ── Composite value score ─────────────────────────────────────

def _value_scores(platform_summaries: list[dict], matrix_data: dict, dims: list[dict]) -> dict:
    """
    0-100 composite: how often each platform wins a dimension, weighted
    (price & rating count double).
    """
    winners = matrix_data["dimension_winners"]
    weights = {}
    for dim in dims:
        w = 2.0 if dim["key"] in ("price", "price_per_night", "price_per_day", "rating") else 1.0
        if dim.get("better") in ("lower", "higher", "true", "more"):
            weights[dim["key"]] = w

    total_w = sum(weights.values()) or 1
    scores = {}
    for ps in platform_summaries:
        pid = ps["platform_id"]
        won = sum(weights.get(k, 0) for k, wp in winners.items() if wp == pid)
        scores[pid] = round(100 * won / total_w)
    return scores


# ── Smart badges ──────────────────────────────────────────────

def _assign_badges(platform_summaries: list[dict], matrix_data: dict,
                   value_scores: dict, dims: list[dict]) -> dict:
    """Assign human-friendly badges per platform."""
    winners = matrix_data["dimension_winners"]
    badges: dict[str, list[str]] = {ps["platform_id"]: [] for ps in platform_summaries}

    # Best Value = highest composite score
    if value_scores:
        best_value = max(value_scores, key=value_scores.get)
        if value_scores[best_value] > 0:
            badges[best_value].append("⭐ Best Value")

    # Map specific dimension wins → badges
    label_by_key = {d["key"]: d["label"] for d in dims}
    badge_names = {
        "price": "💰 Cheapest", "price_per_night": "💰 Cheapest",
        "price_per_day": "💰 Cheapest", "fare": "💰 Cheapest",
        "rating": "🌟 Top Rated",
        "duration": "⚡ Fastest",
        "stops": "🛫 Fewest Stops",
        "confirmation_chance": "✅ Most Likely Confirmed",
    }
    for key, pid in winners.items():
        if key in badge_names and badge_names[key] not in badges[pid]:
            badges[pid].append(badge_names[key])

    # Most amenities = platform whose best result has most true booleans / facilities
    bool_dims = [d["key"] for d in dims if d["type"] == "bool"]
    if bool_dims:
        counts = {}
        for ps in platform_summaries:
            best = ps.get("best_result") or {}
            counts[ps["platform_id"]] = sum(1 for k in bool_dims if _truthy(best.get(k)) is True)
        if counts and max(counts.values()) > 0:
            top = max(counts, key=counts.get)
            badges[top].append("🎁 Most Amenities")

    return badges


# ── AI takeaways ──────────────────────────────────────────────

TAKEAWAY_PROMPT = """You are a sharp comparison advisor. The user searched: "{query}" ({intent_type}).

Here is the cross-platform comparison data:
{comparison_json}

Write 3-5 punchy, specific TRADE-OFF insights that help the user decide. Each must:
- Cite real numbers/platforms from the data (e.g. "Booking.com is ₹520 cheaper than Agoda")
- Highlight a trade-off where relevant (cheaper BUT fewer amenities, faster BUT pricier)
- Be one sentence, scannable, useful

Return JSON: {{"takeaways": ["...", "..."], "verdict": "<one-line bottom-line recommendation>"}}
"""


def _ai_takeaways(query: str, intent_type: str, platform_summaries: list[dict],
                  matrix_data: dict, value_scores: dict, badges: dict) -> dict:
    compact = {
        "platforms": [
            {
                "id": ps["platform_id"],
                "name": ps["platform_name"],
                "best": {k: v for k, v in (ps.get("best_result") or {}).items()
                         if not k.startswith("_")},
                "value_score": value_scores.get(ps["platform_id"]),
                "badges": badges.get(ps["platform_id"], []),
            }
            for ps in platform_summaries if ps.get("best_result")
        ],
        "dimension_winners": matrix_data["dimension_winners"],
    }
    try:
        resp = chat(
            "insights",
            messages=[{"role": "user", "content": TAKEAWAY_PROMPT.format(
                query=query, intent_type=intent_type,
                comparison_json=json.dumps(compact, indent=2)[:3000],
            )}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=600,
        )
        data = json.loads(resp.choices[0].message.content)
        return {
            "takeaways": data.get("takeaways", []),
            "verdict": data.get("verdict", ""),
        }
    except Exception as e:
        logger.warning(f"AI takeaways failed: {e}")
        return {"takeaways": [], "verdict": ""}


# ── LangGraph node ────────────────────────────────────────────

def insights_node(state: AgentState) -> dict:
    comparison = state.get("comparison") or {}
    intent     = state.get("intent") or {}
    intent_type = intent.get("type", "general")
    query       = state.get("query", "")

    ranked = comparison.get("ranked_platforms", [])
    # only platforms that actually returned a best result
    summaries = [p for p in ranked if p.get("best_result")]

    if len(summaries) < 1:
        logger.info("Insights: not enough data to compare")
        return {"insights": {"available": False}, "status": "recommending"}

    # Comparison factors are derived from what these results ACTUALLY contain,
    # not a fixed per-category template — so the matrix adapts to each search
    # (surfacing real attributes the listings expose, dropping ones nothing
    # mentions). Static YAML dims are a fallback for thin/edge result sets.
    best_results = [s["best_result"] for s in summaries if s.get("best_result")]
    # A factor only earns a row if at least 2 platforms expose it — a value only one
    # site has isn't comparable and just litters the matrix with near-empty rows.
    # With only 1-2 platforms there's nothing to cross-check, so allow single coverage
    # there (otherwise the matrix would collapse to just Price). Price always shows.
    min_cov = 2 if len(best_results) >= 3 else 1
    dims = discover_dimensions(best_results, intent_type, max_dims=10, min_coverage=min_cov)
    if len(dims) < 2:
        static = _load_dimensions(intent_type)
        # Merge: keep discovered, append any static factors not already present.
        seen = {d["key"] for d in dims}
        dims = dims + [d for d in static if d["key"] not in seen]
    if not dims:
        return {"insights": {"available": False}, "status": "recommending"}

    emit("Analyzing trade-offs…", stage="insights", kind="start")
    matrix_data  = _build_matrix(summaries, dims)
    value_scores = _value_scores(summaries, matrix_data, dims)
    badges       = _assign_badges(summaries, matrix_data, value_scores, dims)
    ai           = _ai_takeaways(query, intent_type, summaries, matrix_data, value_scores, badges)

    insights = {
        "available": True,
        "dimensions": dims,
        "matrix": matrix_data["matrix"],
        "dimension_winners": matrix_data["dimension_winners"],
        "value_scores": value_scores,
        "badges": badges,
        "takeaways": ai["takeaways"],
        "verdict": ai["verdict"],
        "platforms_order": [p["platform_id"] for p in summaries],
        "platform_names": {p["platform_id"]: p["platform_name"] for p in summaries},
        "platform_icons": {p["platform_id"]: p.get("icon", "🔍") for p in summaries},
    }
    logger.info(f"Insights: {len(dims)} dims, {len(summaries)} platforms, "
                f"value_scores={value_scores}")
    return {"insights": insights, "status": "recommending"}

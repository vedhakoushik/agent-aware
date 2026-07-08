"""
Segregation node — groups results by *what they actually are*, then compares
each group side-by-side across platforms.

A single search ("hotels in Manali") returns a mix of Standard Rooms, Deluxe
Rooms and Suites scattered across Booking.com, Agoda, MakeMyTrip… Showing them
as one flat list makes it impossible to compare like-for-like. This node:

  1. SEPARATES every result into a group keyed by its booking type
     (room type for hotels, cabin class for flights, class for trains, …) —
     using the type the parser already extracted (`_booking_type`).
  2. For each group, lays out one row per platform with its cheapest matching
     option, that option's price, and its amenities/facilities — so the user
     sees "Deluxe Room: ₹4,200 on Agoda (breakfast ✓, pool ✓) vs ₹4,500 on
     Booking (breakfast ✗, pool ✓)" at a glance.
  3. Asks the LLM for one short plain-English comparison line per group
     (e.g. "Agoda is ₹300 cheaper and throws in breakfast"). This is best-effort:
     if the AI quota is exhausted the groups still render, just without the blurb.

The grouping itself is deterministic (no extra tokens) and rests on the LLM work
already done upstream (booking type + amenities were extracted during parsing),
so the core "separate & compare" feature keeps working even when the AI is rate
limited.
"""
import json
import logging
import os

from backend.llm import chat
from backend.state import AgentState
from backend.progress import emit

logger = logging.getLogger(__name__)

_PLATFORMS_CACHE = None


def _load_cfg() -> dict:
    global _PLATFORMS_CACHE
    if _PLATFORMS_CACHE is None:
        import yaml
        path = os.path.join(os.path.dirname(__file__), "../../config/platforms.yaml")
        with open(os.path.normpath(path), encoding="utf-8") as f:
            _PLATFORMS_CACHE = yaml.safe_load(f)
    return _PLATFORMS_CACHE


# Human label for the thing we group on, per category.
_GROUP_LABEL = {
    "hotel": "Room Type",
    "flight": "Cabin Class",
    "train": "Travel Class",
    "bus": "Bus Type",
    "event": "Ticket Type",
    "product": "Variant",
    "restaurant": "Service",
    "car_rental": "Vehicle Class",
}

_TRUE = {"true", "yes", "1", "included", "free", "available", "✓"}
_FALSE = {"false", "no", "0", "not included", "unavailable", "n/a", "", "✗"}


def _as_bool(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def _amenity_dims(normalized: list, intent_type: str) -> list[dict]:
    """Amenity/feature factors to show per group — discovered from the actual
    results (not a fixed template), minus the price column and the booking-type
    field itself (that's already the group heading)."""
    from backend.dimensions import discover_dimensions
    from backend.booking_type import BOOKING_TYPE_FIELDS

    dims = discover_dimensions(normalized, intent_type, max_dims=10)
    skip_keys = set(BOOKING_TYPE_FIELDS.get((intent_type or "").lower(), []))
    return [d for d in dims if d["type"] != "price" and d["key"] not in skip_keys]


def _row_amenities(result: dict, dims: list[dict]) -> list[dict]:
    """Pull amenity values out of one result, typed for clean rendering."""
    out = []
    for d in dims:
        key, dtype, label = d["key"], d["type"], d.get("label", d["key"])
        raw = result.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        if dtype == "bool":
            b = _as_bool(raw)
            if b is None:
                continue
            out.append({"key": key, "label": label, "type": "bool", "value": b})
        else:
            out.append({"key": key, "label": label, "type": "text", "value": str(raw)})
    return out


def _summarize_groups_llm(query: str, intent_type: str, groups: list[dict]) -> dict:
    """One LLM call → {group_type: one-line comparison}. Best-effort."""
    compact = []
    for g in groups:
        compact.append({
            "type": g["type"],
            "options": [
                {
                    "platform": r["platform_name"],
                    "price": r["price"],
                    "name": r["name"],
                    "amenities": {a["label"]: a["value"] for a in r["amenities"]},
                }
                for r in g["rows"]
            ],
        })
    prompt = (
        f'User searched: "{query}" ({intent_type}). Results are grouped by type below.\n'
        f"{json.dumps(compact, indent=2)[:3500]}\n\n"
        "For EACH group type, write ONE short sentence comparing the platforms in that "
        "group — cite the cheapest price + platform and the standout amenity difference "
        '(e.g. "Agoda is ₹300 cheaper and includes breakfast; Booking adds a pool"). '
        'Return JSON: {"summaries": {"<group type>": "<one sentence>", ...}}'
    )
    try:
        resp = chat(
            "segregate",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=600,
        )
        data = json.loads(resp.choices[0].message.content)
        return data.get("summaries", {}) or {}
    except Exception as e:
        logger.warning(f"Segment summaries skipped: {e}")
        return {}


def segregate_node(state: AgentState) -> dict:
    """LangGraph node: separate results into type-groups and compare each group
    across platforms."""
    normalized = state.get("normalized", [])
    intent = state.get("intent", {}) or {}
    intent_type = intent.get("type", "general")
    query = state.get("query", "")

    if not normalized:
        return {"segments": {"available": False}}

    label = _GROUP_LABEL.get(intent_type, "Type")
    amenity_dims = _amenity_dims(normalized, intent_type)

    # 1. Bucket every result by its booking type. Results with no detected type
    #    fall into a single "Other options" bucket so nothing is dropped.
    buckets: dict[str, list[dict]] = {}
    for r in normalized:
        btype = (r.get("_booking_type") or "").strip() or "Other options"
        buckets.setdefault(btype, []).append(r)

    emit(f"Grouping by {label.lower()}…", stage="segregate", kind="start")

    # 2. For each group, one row per platform = that platform's cheapest option
    #    of this type (or, if no price, its first option of this type).
    groups = []
    for btype, items in buckets.items():
        by_platform: dict[str, list[dict]] = {}
        for r in items:
            by_platform.setdefault(r.get("_platform_id", "?"), []).append(r)

        rows = []
        for pid, plist in by_platform.items():
            priced = [r for r in plist if r.get("_price_numeric") is not None]
            best = min(priced, key=lambda r: r["_price_numeric"]) if priced else plist[0]
            rows.append({
                "platform_id": pid,
                "platform_name": best.get("_platform_name", pid),
                "icon": best.get("_platform_icon", "🔍"),
                "name": best.get("name") or best.get("title") or best.get("car_model") or "Option",
                "price": int(best["_price_numeric"]) if best.get("_price_numeric") is not None else None,
                "url": best.get("url") or best.get("href") or "",
                "amenities": _row_amenities(best, amenity_dims),
                "count": len(plist),
            })

        # Sort rows cheapest-first (rows without a price sink to the bottom).
        rows.sort(key=lambda r: (r["price"] is None, r["price"] if r["price"] is not None else 0))
        priced_rows = [r for r in rows if r["price"] is not None]
        cheapest = priced_rows[0] if priced_rows else None

        groups.append({
            "type": btype,
            "total": len(items),
            "platform_count": len(by_platform),
            "rows": rows,
            "cheapest_platform": cheapest["platform_name"] if cheapest else None,
            "cheapest_price": cheapest["price"] if cheapest else None,
            "summary": "",
        })

    # 3. Order groups: biggest/most-compared first; "Other options" always last.
    groups.sort(key=lambda g: (g["type"] == "Other options", -g["total"]))

    # 4. Best-effort AI one-liner per group (skipped silently if quota is out, or
    #    if there's nothing to compare — a lone single-platform group).
    multi = [g for g in groups if g["platform_count"] >= 2]
    if multi:
        summaries = _summarize_groups_llm(query, intent_type, multi)
        for g in groups:
            g["summary"] = summaries.get(g["type"], "")

    # Union of amenity labels actually present — lets the UI build a stable
    # column order for the per-group comparison.
    amenity_order = [d["label"] for d in amenity_dims]

    segments = {
        "available": True,
        "group_label": label,
        "amenity_order": amenity_order,
        "groups": groups,
    }
    logger.info(f"Segregated into {len(groups)} groups by {label}: "
                f"{[g['type'] for g in groups]}")
    return {"segments": segments}

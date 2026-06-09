"""
Shared booking-type detection.

A single search ("hotels in Manali", "flights Delhi to Goa") returns results that
are NOT all the same kind of booking — a hotel platform mixes Standard Rooms with
Suites, a flight platform mixes Economy with Business. Comparing "cheapest overall"
across platforms in that situation is misleading: a ₹1,200 Standard Room from one
site isn't a fair comparison against a ₹6,000 Suite from another.

This module identifies the "booking type" of each result (room type, cabin class,
seat class, etc. depending on category) so that:
  - the UI can show the user exactly what they'd be booking, and
  - the comparison engine can line up similar types across platforms before
    declaring a winner (Standard Room vs Standard Room, not vs Suite).
"""
from collections import Counter

# Priority-ordered fields to look for the booking type, per intent category.
# Earlier fields win when a result has more than one populated.
BOOKING_TYPE_FIELDS: dict[str, list[str]] = {
    "flight":     ["cabin_class", "class", "seat_class", "fare_type", "fare_class"],
    "hotel":      ["room_type", "type", "room_category", "property_type"],
    "event":      ["category", "ticket_type", "tier", "section"],
    "restaurant": ["service_type", "order_type"],
    "product":    ["condition", "variant", "storage", "size"],
    "train":      ["class", "coach_class", "seat_type", "travel_class", "quota"],
    "bus":        ["bus_type", "seat_type", "coach_type"],
    "car_rental": ["category", "car_type", "vehicle_class"],
}

_BLANK = ("none", "null", "n/a", "-", "")


def extract_booking_type(result: dict, intent_type: str) -> str:
    """Return the normalized booking type for a result, e.g. 'Economy', 'Standard Room', 'Sleeper'."""
    if not result or not isinstance(result, dict):
        return ""
    for f in BOOKING_TYPE_FIELDS.get((intent_type or "").lower(), []):
        v = result.get(f)
        if v and str(v).strip() and str(v).strip().lower() not in _BLANK:
            return str(v).strip().title()
    return ""


def dominant_type(results: list, intent_type: str) -> str:
    """
    Pick the booking type to anchor the comparison on — the most common type seen
    across all platforms' results. Comparing "like for like" means lining every
    platform up against THIS type wherever they have a matching result.

    Returns "" if the category has no booking-type concept, or no result carries one
    (in which case callers should fall back to comparing everything, unfiltered).
    """
    counts = Counter(
        t for t in (extract_booking_type(r, intent_type) for r in (results or [])) if t
    )
    if not counts:
        return ""
    return counts.most_common(1)[0][0]

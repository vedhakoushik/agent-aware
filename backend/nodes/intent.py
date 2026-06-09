import json
import logging
import os
from datetime import datetime

import yaml

from backend.llm import get_chat_client
from backend.state import AgentState, SearchIntent
from backend.memory.reliability import rank_by_reliability
from backend.progress import emit

logger = logging.getLogger(__name__)

_PLATFORMS_CACHE = None


def _load_platforms() -> dict:
    global _PLATFORMS_CACHE
    if _PLATFORMS_CACHE is None:
        config_path = os.path.join(os.path.dirname(__file__), "../../config/platforms.yaml")
        with open(os.path.normpath(config_path), encoding="utf-8") as f:
            _PLATFORMS_CACHE = yaml.safe_load(f)
    return _PLATFORMS_CACHE


def _platform_index_by_category(config: dict) -> dict[str, list[dict]]:
    """Returns {category: [{id, name, keywords}, ...]} so LLM can match explicit mentions."""
    index: dict[str, list[dict]] = {}
    for p in config.get("platforms", []):
        entry = {"id": p["id"], "name": p["name"], "keywords": p.get("keywords", [])}
        for cat in p.get("categories", []):
            index.setdefault(cat, []).append(entry)
    return index


INTENT_SYSTEM = """You are an intent parser for a multi-platform search agent. Today's date is {today}.

Given a user query, extract the following as JSON:
{{
  "type": "<one of: flight, hotel, event, restaurant, product, train, bus, car_rental, general>",
  "params": {{<all relevant search parameters extracted from the query>}},
  "platforms": [<list of 3-5 platform IDs>],
  "clarification_needed": false,
  "clarification_question": null
}}

Rules:
- Extract EVERYTHING from the query into params: origin, destination, date, budget, location, cuisine, event_type, product_name, passengers, pooling, car_type, etc.
- For flights/trains/buses, capture the requested seat/cabin/travel class as "cabin_class" — normalize to one of:
  Economy, Premium Economy, Business, First (flights); Sleeper, AC 3 Tier, AC 2 Tier, AC First Class, Chair Car (trains).
  Recognize phrases like "business class", "1st class"/"first class", "economy", "premium economy", "in business", "fly business".
  If the user doesn't mention a class, omit "cabin_class" from params (do not guess).
- Resolve relative dates to actual date strings (e.g. "this Friday" → "2026-06-07")
- If critical info is missing (e.g. no destination for a flight), set clarification_needed=true
- For budget hints like "cheap" or "under 5000", store as {{"max": 5000}} in params.budget

DATES ARE REQUIRED FOR BOOKINGS — hotels, flights, trains, buses and car rentals need dates
to return real, priced availability. Apply this strictly:
- HOTEL: needs BOTH check-in and check-out. Store as params.check_in and params.check_out
  (YYYY-MM-DD). If either is missing, set clarification_needed=true and
  clarification_question="What are your check-in and check-out dates?"
- FLIGHT / TRAIN / BUS: needs a travel date. Store as params.date (YYYY-MM-DD; for round trips
  also params.return_date). If missing, set clarification_needed=true and
  clarification_question="What date are you travelling?"
- CAR_RENTAL: needs pick-up and drop-off dates (params.pickup_date, params.dropoff_date). If
  missing, set clarification_needed=true and clarification_question="What are your pick-up and
  drop-off dates?"
- If the user gives ANY usable date — including relative ones like "this weekend", "tomorrow",
  "next Friday", "13–15 June" — RESOLVE them to YYYY-MM-DD and DO NOT ask. Only ask when there
  is genuinely no date at all.
- event, restaurant, product and general searches do NOT require dates.

PLATFORM SELECTION — PRIORITY ORDER:
1. If the user explicitly names a platform (e.g. "search on Zoomcar", "find on Skyscanner", "check Swiggy"),
   those platforms MUST appear first in the list. Match by name or keyword from the platform keywords list.
2. Fill remaining slots (up to 5 total) with the best matching platforms for the intent type.
3. Never include a platform that doesn't match the intent type.

Available platforms with keywords:
{platform_index}
"""


def parse_intent_node(state: AgentState) -> dict:
    """LangGraph node: parse user query into structured intent."""
    emit("Understanding your request…", stage="intent", kind="start")
    config = _load_platforms()
    platform_index = _platform_index_by_category(config)
    today = datetime.now().strftime("%Y-%m-%d (%A)")

    client = get_chat_client()

    prompt = INTENT_SYSTEM.format(
        today=today,
        platform_index=json.dumps(platform_index, indent=2),
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": state["query"]},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        parsed = json.loads(response.choices[0].message.content)

        # Validate platforms exist in config
        valid_ids = {p["id"] for p in config.get("platforms", [])}
        parsed["platforms"] = [pid for pid in parsed.get("platforms", []) if pid in valid_ids]

        # Fallback: use category defaults if no valid platforms found
        if not parsed["platforms"]:
            intent_type = parsed.get("type", "general")
            parsed["platforms"] = platform_index.get(intent_type, list(valid_ids))[:4]

        # Nudge the order toward platforms with a track record of actually
        # returning usable results — without abandoning the LLM's relevance
        # ranking. `rank_by_reliability` only reorders platforms we have
        # *enough history* on (>= MIN_SAMPLES); brand-new/rarely-used ones
        # stay in their original (LLM-relevance) order. This means a source
        # that consistently times out or gets blocked gradually gets tried
        # less often, while one that reliably returns data gets tried first —
        # all self-tuning, no manual platform list edits needed.
        try:
            parsed["platforms"] = rank_by_reliability(parsed["platforms"])
        except Exception as e:
            logger.debug(f"Reliability ranking skipped: {e}")

        intent: SearchIntent = {
            "type": parsed.get("type", "general"),
            "raw_query": state["query"],
            "params": parsed.get("params", {}),
            "platforms": parsed["platforms"][:5],  # cap at 5
            "clarification_needed": parsed.get("clarification_needed", False),
            "clarification_question": parsed.get("clarification_question"),
        }

        logger.info(f"Intent parsed: type={intent['type']}, platforms={intent['platforms']}")
        if intent["clarification_needed"]:
            emit("Need a bit more detail…", stage="intent", kind="warn")
        return {"intent": intent, "status": "searching"}

    except Exception as e:
        logger.error(f"Intent parsing failed: {e}")
        emit("Couldn't understand the request.", stage="intent", kind="warn")
        return {"intent": None, "status": "error", "error": f"Intent parsing failed: {e}"}

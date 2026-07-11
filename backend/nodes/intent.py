import json
import logging
import os
import re
from datetime import datetime

import yaml

from backend.llm import chat
from backend.state import AgentState, SearchIntent
from backend.memory.reliability import rank_by_reliability
from backend.progress import emit
from backend.agent_bus import send as bus_send

logger = logging.getLogger(__name__)


def _site_names(platform_ids: list, config: dict) -> list[str]:
    names = {p["id"]: p["name"] for p in config.get("platforms", [])}
    return [names.get(pid, pid) for pid in platform_ids]


def _emit_plan_handoff(intent_type: str, platform_ids: list, params: dict, config: dict) -> None:
    """Publish the Intent agent's plan to the Search coordinator on the Agent
    Communication bus — the visible 'go search these websites with these params'
    instruction the user wants to watch hand off between agents."""
    sites = _site_names(platform_ids, config)
    bus_send(
        frm="Intent Agent", to="Search Coordinator", kind="handoff",
        title=f"Plan: {intent_type} search across {len(sites)} site(s)",
        content={"type": intent_type, "websites": sites, "params": params},
        meta={"type": intent_type},
    )

_PLATFORMS_CACHE = None


# ── Deterministic category planner ────────────────────────────────────────────
# The LLM picks the category + websites, but our cloud LLMs hit daily rate limits
# constantly and fall back to a weak local model that misclassifies (an iPhone
# search getting routed to Booking.com). This keyword layer decides the category
# WITHOUT any LLM, so routing stays correct even when every model is down. Ordered
# most-specific first: travel/stay/event signals win before the generic product net.
_CATEGORY_SIGNALS: list[tuple[str, str]] = [
    ("flight", r"\b(flight|flights|fly|flying|airfare|air ?ticket|one[- ]?way|round[- ]?trip|non[- ]?stop|layover|airlines?)\b"),
    ("train", r"\b(train|trains|irctc|railway|rail|pnr|sleeper coach|tatkal)\b"),
    ("bus", r"\b(bus|buses|redbus|volvo bus|sleeper bus|seater)\b"),
    ("car_rental", r"\b(car rental|rent a car|rental car|self[- ]?drive|zoomcar|myles)\b"),
    ("hotel", r"\b(hotel|hotels|resort|resorts|stay|homestay|guest ?house|room|rooms|accommodation|check[- ]?in|check[- ]?out|villa|airbnb|oyo|lodging)\b"),
    ("event", r"\b(concert|gig|movie|movies|cinema|show|event|events|ticket|tickets|festival|standup|stand[- ]?up|pvr|inox|bookmyshow|coldplay|tour 20\d\d)\b"),
    ("restaurant", r"\b(restaurant|restaurants|food|eat|dine|dining|dinner|lunch|breakfast|brunch|cafe|café|pizza|biryani|burger|sushi|swiggy|zomato|order food|takeaway)\b"),
    ("product", r"\b(buy|price|deal|deals|discount|order|iphone|samsung|galaxy|pixel|oneplus|redmi|realme|nothing phone|laptop|macbook|ipad|tablet|headphones?|earbuds?|airpods|smart ?watch|television|\btv\b|fridge|refrigerator|washing machine|ac\b|shoes|sneakers|pro max|\bgb\b|\bram\b|\bssd\b)\b"),
]


def _detect_category(query: str) -> str | None:
    """Return the category implied by the query's wording, or None if ambiguous.
    Deterministic — no LLM — so it works even when all model quotas are exhausted."""
    q = (query or "").lower()
    for category, pattern in _CATEGORY_SIGNALS:
        if re.search(pattern, q):
            return category
    return None


# ── Out-of-scope circuit breaker ──────────────────────────────────────────────
# The platform catalog only sells/lists physical products, travel, hotels,
# restaurants and events — never subscriptions or streaming services. The LLM is
# now instructed (see INTENT_SYSTEM) to recognize that itself, but a prompt
# instruction can be ignored under a weak fallback model. This is a zero-extra-
# LLM-call safety net for the clearest, highest-confidence misfires — e.g. "which
# country has the cheapest Netflix" getting classified as a product search and
# dispatched to Amazon/Flipkart/Croma, none of which can price a subscription.
# Deliberately narrow and example-based (not exhaustive) so it never over-blocks a
# legitimate product query — a Netflix GIFT CARD is still a real product; this only
# fires when the classifier already forced the query into "product".
_OUT_OF_SCOPE_SERVICES = (
    "netflix", "spotify", "disney+", "disney plus", "hulu", "hbo max", "apple music",
    "youtube premium", "hotstar", "sonyliv", "zee5", "prime video", "chatgpt plus",
    "github copilot", "notion pro", "canva pro",
)


_PHYSICAL_COMPANION_WORDS = ("gift card", "gift voucher", "voucher", "subscription code")


def _out_of_scope_service(query: str) -> str | None:
    """Return the matched service name if the query names a subscription/streaming
    service this app's platforms cannot price, else None. Skips queries that name a
    genuinely purchasable companion item (a Netflix GIFT CARD is a real product
    Amazon/Flipkart sell — only the subscription itself is out of scope)."""
    q = (query or "").lower()
    if any(w in q for w in _PHYSICAL_COMPANION_WORDS):
        return None
    for name in _OUT_OF_SCOPE_SERVICES:
        if name in q:
            return name
    return None


def _category_defaults(category: str, config: dict, limit: int = 5) -> list[str]:
    """The platforms that actually serve a category, straight from the catalog —
    used as a SAFE fallback (never arbitrary cross-category sites)."""
    return [p["id"] for p in config.get("platforms", [])
            if category in p.get("categories", [])][:limit]


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
- OUT-OF-SCOPE QUERIES: the available platforms below sell/list physical products, travel,
  hotels, restaurants and events — NOT subscriptions, streaming services, currency conversion,
  or general trivia. If NO available platform can genuinely answer the query (e.g. "which
  country has the cheapest Netflix", "convert 500 USD to INR", "what's the capital of France"),
  do NOT force it into "product" or any other category just because it superficially resembles
  one. Instead set clarification_needed=true and clarification_question to a short, honest
  explanation of why this app can't help with that request — never guess a category and
  dispatch a search that can't possibly return a correct answer.
- For budget hints like "cheap" or "under 5000", store as {{"max": 5000}} in params.budget
- For products, if the user specifies a CONDITION (e.g. "brand new", "new", "refurbished",
  "renewed", "used", "second hand", "pre-owned", "open box"), normalize it into params.condition
  as one of: new, refurbished, used, open_box. If the user doesn't mention a condition, OMIT
  params.condition entirely — do not assume "new".

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
    bus_send(frm="You", to="Intent Agent", kind="request",
             title="New search request", content=state["query"])
    config = _load_platforms()
    platform_index = _platform_index_by_category(config)
    today = datetime.now().strftime("%Y-%m-%d (%A)")

    prompt = INTENT_SYSTEM.format(
        today=today,
        platform_index=json.dumps(platform_index, indent=2),
    )

    try:
        response = chat(
            "intent",
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

        # DETERMINISTIC OVERRIDE — trust the keyword planner over the LLM's category.
        # When the LLM is rate-limited and we're on the weak local fallback, it
        # misclassifies (iPhone → general/hotel). A confident keyword match is more
        # reliable for routing, so it wins; we only keep the LLM's type when the query
        # gives no clear signal at all.
        llm_type = parsed.get("type", "general")
        detected = _detect_category(state["query"])
        intent_type = detected or llm_type
        if detected and detected != llm_type:
            logger.info(f"Category override: LLM said '{llm_type}', keyword planner → '{detected}'")
        parsed["type"] = intent_type

        # CATEGORY GUARDRAIL — keep only platforms that actually serve the category.
        # Now applied for EVERY concrete category (incl. when we just corrected a
        # mislabeled 'general'), so a product search can never drive a hotel/flight site.
        if intent_type != "general":
            cat_of = {p["id"]: set(p.get("categories", [])) for p in config.get("platforms", [])}
            kept = [pid for pid in parsed["platforms"] if intent_type in cat_of.get(pid, set())]
            if len(kept) != len(parsed["platforms"]):
                dropped = [p for p in parsed["platforms"] if p not in kept]
                logger.info(f"Dropped off-category platforms for {intent_type}: {dropped}")
            parsed["platforms"] = kept

        # Fallback: SAFE category defaults from the catalog — never arbitrary sites.
        # (The old `list(valid_ids)[:4]` is what surfaced Booking.com for an iPhone.)
        if not parsed["platforms"]:
            parsed["platforms"] = (_category_defaults(intent_type, config)
                                   or platform_index.get(intent_type, []))

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

        # OUT-OF-SCOPE CIRCUIT BREAKER — runs last, after the LLM/keyword/fallback
        # layers above, so it always wins regardless of what populated `platforms`.
        # Catches the LLM ignoring its out-of-scope instruction (e.g. "cheapest
        # country for Netflix" forced into type=product, dispatched to e-commerce
        # sites that can never answer it) WITHOUT spending another LLM call.
        oos_service = _out_of_scope_service(state["query"]) if intent_type == "product" else None
        if oos_service:
            logger.info(f"Out-of-scope circuit breaker: '{oos_service}' is a subscription "
                        f"service, not a product any configured platform sells — stopping "
                        f"before search instead of dispatching a search that can't answer it.")
            parsed["clarification_needed"] = True
            parsed["clarification_question"] = (
                f"This app searches products, travel, hotels, restaurants and events on "
                f"specific platforms — it can't compare {oos_service.title()}'s subscription "
                f"pricing across countries (no configured platform sells that). Try a "
                f"dedicated pricing-comparison site for that instead.")
            parsed["platforms"] = []

        intent: SearchIntent = {
            "type": parsed.get("type", "general"),
            "raw_query": state["query"],
            "params": parsed.get("params", {}),
            "platforms": parsed["platforms"][:5],  # cap at 5
            "clarification_needed": parsed.get("clarification_needed", False),
            "clarification_question": parsed.get("clarification_question"),
        }

        # Show the user the plan: what kind of search this is + which sites we'll hit.
        if not intent["clarification_needed"] and intent["platforms"]:
            names = {p["id"]: p["name"] for p in config.get("platforms", [])}
            site_list = ", ".join(names.get(pid, pid) for pid in intent["platforms"])
            emit(f"Plan: {intent['type']} search → {site_list}", stage="intent", kind="ok")
            _emit_plan_handoff(intent["type"], intent["platforms"], intent["params"], config)
        elif intent["clarification_needed"]:
            bus_send(frm="Intent Agent", to="You", kind="message",
                     title="Needs clarification",
                     content=intent.get("clarification_question") or "More detail needed.")

        logger.info(f"Intent parsed: type={intent['type']}, platforms={intent['platforms']}")
        if intent["clarification_needed"]:
            emit("Need a bit more detail…", stage="intent", kind="warn")
        return {"intent": intent, "status": "searching"}

    except Exception as e:
        logger.error(f"Intent parsing failed: {e}")
        # LLM-FREE FALLBACK — when every model is rate-limited, still run a useful
        # search for date-optional categories using the deterministic planner. (Travel
        # categories need date/route parsing we can't do reliably without an LLM, so
        # those still ask the user rather than search with bad params.)
        detected = _detect_category(state["query"])
        if detected in ("product", "event", "restaurant", "general"):
            plats = _category_defaults(detected, config)
            try:
                plats = rank_by_reliability(plats)
            except Exception:
                pass
            if plats:
                names = {p["id"]: p["name"] for p in config.get("platforms", [])}
                emit(f"Plan: {detected} search → "
                     + ", ".join(names.get(p, p) for p in plats[:5]),
                     stage="intent", kind="ok")
                bus_send(frm="Intent Agent", to="Search Coordinator", kind="error",
                         title="All LLMs rate-limited — planning from keywords instead",
                         content="Cloud models are out of quota; used the deterministic "
                                 "keyword planner so the search still runs.")
                _emit_plan_handoff(detected, plats[:5], {"query": state["query"]}, config)
                intent: SearchIntent = {
                    "type": detected, "raw_query": state["query"],
                    "params": {"query": state["query"]}, "platforms": plats[:5],
                    "clarification_needed": False, "clarification_question": None,
                }
                logger.info(f"Intent (LLM-free fallback): type={detected}, platforms={plats[:5]}")
                return {"intent": intent, "status": "searching"}
        emit("Couldn't understand the request.", stage="intent", kind="warn")
        return {"intent": None, "status": "error", "error": f"Intent parsing failed: {e}"}

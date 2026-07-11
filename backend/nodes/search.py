"""
Search node — each platform searched in parallel.
Primary: Tavily, restricted to the platform's own domain (fast, ~3s, real pages)
Secondary: browser-use agent — LLM-driven live browser, only when Tavily came
           back with little/no raw data (slow, ~60-90s)
Tertiary: Universal Playwright form automation
Fallback: Google / DuckDuckGo via Playwright

Uses a dedicated thread per search to avoid asyncio event-loop conflicts with Streamlit.
"""
import asyncio
import json
import logging
import os
import threading
import time
from typing import Optional

import yaml

from backend.llm import chat
from backend.state import AgentState, PlatformResult
from backend.memory.reliability import record_outcome
from backend.progress import emit
from backend.agent_bus import send as bus_send

logger = logging.getLogger(__name__)

_PLATFORMS_CACHE: Optional[dict] = None


def _load_platforms() -> dict:
    global _PLATFORMS_CACHE
    if _PLATFORMS_CACHE is None:
        config_path = os.path.join(os.path.dirname(__file__), "../../config/platforms.yaml")
        with open(os.path.normpath(config_path), encoding="utf-8") as f:
            _PLATFORMS_CACHE = yaml.safe_load(f)
    return _PLATFORMS_CACHE


def _get_platform_config(platform_id: str) -> Optional[dict]:
    for p in _load_platforms().get("platforms", []):
        if p["id"] == platform_id:
            return p
    return None


def _build_query(platform: dict, params: dict) -> str:
    template = platform.get("ddg_template", "{product_name}")
    try:
        return template.format_map({k: str(v) for k, v in params.items()})
    except KeyError:
        return f"{platform['name']} " + " ".join(str(v) for v in params.values())


# ── Search backends ───────────────────────────────────────────

def _domain_from_url(url: str) -> str:
    """Extract the bare registrable domain (e.g. 'www.croma.com' → 'croma.com')."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc or url
    return host[4:] if host.startswith("www.") else host


def _tavily_search(query: str, api_key: str, domain: str = "") -> list[dict]:
    """Tavily — fast, clean AI-ready snippets with real prices (~3s).

    When `domain` is given, results are restricted to that platform's own
    website (via Tavily's include_domains). This is what stops us getting back
    third-party news/affiliate articles ("Croma drops iPhone 16 price to Rs
    40,990") that quote a price but don't link to a real, live product page.
    """
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        # "basic" depth is ~2-3s vs "advanced" ~10-20s (the hotel-search hang). For a
        # price/listing snippet, basic is plenty. `timeout` stops a slow call from
        # stalling the whole platform thread. Both tunable via env.
        depth = os.getenv("TAVILY_DEPTH", "basic")
        kwargs = {"max_results": 8, "search_depth": depth,
                  "timeout": int(os.getenv("TAVILY_TIMEOUT", "12") or 12)}
        if domain:
            kwargs["include_domains"] = [domain]
        resp = client.search(query, **kwargs)
        return [
            {"title": r.get("title", ""), "snippet": r.get("content", ""), "url": r.get("url", "")}
            for r in resp.get("results", [])
        ]
    except Exception as e:
        logger.warning(f"Tavily failed ({query[:40]}): {e}")
        return []


def _ddg_search(query: str) -> list[dict]:
    """DuckDuckGo fallback."""
    try:
        from duckduckgo_search import DDGS
        time.sleep(0.8)
        with DDGS() as d:
            results = list(d.text(query, max_results=8))
        return [{"title": r.get("title",""), "snippet": r.get("body",""), "url": r.get("href","")} for r in results]
    except Exception as e:
        logger.warning(f"DDG failed ({query[:40]}): {e}")
        return []


def _playwright_run(coro, timeout: int = 60) -> list[dict]:
    """Run an async Playwright coroutine in a fresh thread+event loop."""
    result_holder, error_holder = [], []
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result_holder.extend(loop.run_until_complete(coro))
        except Exception as e:
            error_holder.append(e)
        finally:
            loop.close()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if error_holder:
        logger.warning(f"Playwright thread error: {error_holder[0]}")
    return result_holder


async def _scrape_platform(platform_name: str, search_url: str) -> list[dict]:
    from backend.tools.browser import scrape_platform_results
    # VISIBLE real Chrome (was headless=True — that's why product searches showed no
    # window in the app while force_browser tests did). Honor PLAYWRIGHT_HEADLESS so it
    # can be flipped back to invisible for speed via env.
    _headless = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
    return await scrape_platform_results(platform_name, search_url,
                                         wait_seconds=6, headless=_headless)


async def _universal_fill(platform_name: str, entry_url: str, params: dict) -> list[dict]:
    """Universal LLM-driven form automation — works on any platform."""
    from backend.tools.universal_filler import universal_search
    return await universal_search(platform_name, entry_url, params, wait_after_submit=5)


async def _browser_use_run(platform_name: str, entry_url: str, params: dict,
                           hint: str = "", platform_id: str = "",
                           headless: bool = None, homepage: str = "") -> list[dict]:
    """browser-use agent — navigates + reads the results page (primary scraper)."""
    from backend.tools.browser_agent import browser_use_search
    return await browser_use_search(platform_name, entry_url, params,
                                    hint=hint, platform_id=platform_id, headless=headless,
                                    homepage=homepage)


def _good_entry_url(deep_link: str, website: str) -> str:
    """Avoid handing the agent a half-built deep link. URLs like
    '...?hotelCity=Manali&checkin=&checkout=' (empty date params) protocol-error or
    404; start on the homepage instead and let the agent fill the form."""
    if not deep_link:
        return website
    bad = ("=&" in deep_link or deep_link.rstrip().endswith("=")
           or "={" in deep_link or "=%7B" in deep_link)  # empty or unfilled placeholders
    return website if (bad and website) else deep_link


def _playwright_google_search(query: str) -> list[dict]:
    """Google search via Playwright in an isolated thread."""
    async def _coro():
        from backend.tools.browser import google_search
        return await google_search(query, num_results=8)
    return _playwright_run(_coro())


# ── Python-side currency converter (more reliable than LLM math) ─
import re as _re

_CURRENCY_RATES = {
    "$": 83, "usd": 83, "us$": 83,
    "€": 90, "eur": 90,
    "£": 105, "gbp": 105,
    "sgd": 62, "s$": 62,
    "aed": 22,
    "aud": 54, "a$": 54,
    "¥": 0.55, "jpy": 0.55,
    "₹": 1, "rs": 1, "inr": 1, "rs.": 1, "rp": 1,
}

def _raw_price_to_inr(price_raw: str | None, price_num: float | None,
                      intent_type: str = "general") -> float | None:
    """
    Convert any price to INR.
    Priority: 1) detect currency in price_raw, 2) sanity-check numeric value.
    """
    if price_raw is None and price_num is None:
        return None

    raw = str(price_raw or "").strip().lower()

    # Detect currency symbol in the raw string
    rate = None
    for symbol, fx in _CURRENCY_RATES.items():
        if symbol in raw:
            rate = fx
            break

    # Extract numeric value
    nums = _re.findall(r"\d[\d,]*\.?\d*", raw)
    if nums:
        try:
            val = float(nums[0].replace(",", ""))
        except ValueError:
            val = price_num
    else:
        val = price_num

    if val is None:
        return None

    if rate is not None:
        # Currency symbol found — convert definitively
        return round(val * rate)

    # No currency symbol detected — apply domain sanity check
    # If value is suspiciously low for this intent type, treat as USD
    domain_mins = {
        "hotel": 250, "flight": 400, "event": 30,
        "car_rental": 100, "train": 20, "bus": 20,
        "restaurant": 15, "product": 5, "general": 5,
    }
    min_inr = domain_mins.get(intent_type, 5)
    if 0 < val < min_inr:
        converted = round(val * 83)  # assume USD
        logger.info(f"Sanity USD→INR: {val} → ₹{converted} (domain min ₹{min_inr})")
        return converted

    return round(val)  # assume already INR


# ── Domain-aware price bounds ─────────────────────────────────
# Prices outside these ranges are physically impossible → reject them
PRICE_BOUNDS = {
    "flight":     (800,   200000),   # domestic min ~₹800, intl max ~₹2L
    "hotel":      (300,   500000),   # budget room min ₹300/night
    "event":      (50,    100000),
    "restaurant": (30,    10000),    # per head
    "product":    (10,    10000000),
    "train":      (30,    10000),
    "bus":        (50,    5000),
    "car_rental": (200,   100000),
    "general":    (1,     10000000),
}

# Fields that carry price for each intent type
PRICE_FIELDS = ["price", "price_per_night", "total_price", "fare"]


def _validate_price(raw_val, intent_type: str) -> float | None:
    """Return float price if plausible for this intent, else None."""
    if raw_val is None:
        return None
    try:
        cleaned = str(raw_val).replace(",", "").replace("₹", "").replace("Rs", "").strip()
        # Strip trailing .0
        val = float(cleaned)
        lo, hi = PRICE_BOUNDS.get(intent_type, (1, 10000000))
        if lo <= val <= hi:
            return val
        logger.debug(f"Price {val} rejected for {intent_type} (bounds {lo}–{hi})")
        return None
    except Exception:
        return None


_ACCESSORY_WORDS = (
    "case", "cover", "tempered glass", "screen protector", "screen guard",
    "charger", "cable", "strap", "skin", "pouch", "holder", "adapter",
    "earphone", "earbud", "headphone", "back cover", "flip cover", "lens protector",
)


def _is_generic_name(name: str) -> bool:
    """True if name is a placeholder like 'Option 1', 'Result 2', etc."""
    if not name:
        return True
    lower = name.lower().strip()
    generic = ("option ", "result ", "listing ", "item ", "flight ", "hotel ",
               "unnamed", "unknown", "n/a", "null")
    return any(lower.startswith(g) for g in generic)


# ── Detail-richness scoring ───────────────────────────────────
# Tavily can return 8 "results" that are generic SEO/landing-page copy carrying a
# price but NO real per-listing detail (duration, class, stops…). Raw snippet count
# can't tell those apart from real listings — so we score how much comparison-worthy
# detail the parsed results actually carry, and fall back to a live browser when
# it's thin. The detail fields are exactly the category's comparison dimensions
# (minus the price column), so this stays config-driven per category.

def _detail_keys(intent_type: str) -> list[str]:
    cfg = _load_platforms()
    cat = cfg.get("categories", {}).get(intent_type, {})
    return [d["key"] for d in cat.get("comparison_dimensions", [])
            if d.get("type") != "price"]


def _detail_count(result: dict, keys: list[str]) -> int:
    if not isinstance(result, dict):
        return 0
    return sum(1 for k in keys if result.get(k) not in (None, "", "null", "None"))


def _detail_total(results: list[dict], intent_type: str) -> int:
    keys = _detail_keys(intent_type)
    return sum(_detail_count(r, keys) for r in (results or []))


def _is_detail_thin(results: list[dict], intent_type: str) -> bool:
    """True when the parsed results carry little usable per-listing detail —
    no results at all, or even the richest result has < 2 comparison fields.
    Categories with no comparison dimensions are only 'thin' when empty."""
    keys = _detail_keys(intent_type)
    if not keys:
        return not results
    if not results:
        return True
    return max(_detail_count(r, keys) for r in results) < 2


# ── LLM parser ───────────────────────────────────────────────

PARSE_SYSTEM = """You are a strict data extraction assistant for "{platform_name}".

Extract ONLY real, verifiable listings from the search snippets below.

ANTI-HALLUCINATION RULE — read this first:
   — Every field you output (name, price, url, condition, etc.) MUST come from text that is
     LITERALLY PRESENT in the PAGE CONTENT / LISTING LINKS below.
   — Do NOT use your own knowledge of typical prices, models, or sellers to fill in or "correct"
     a value. If the snippet doesn't show a price for an item, price must be null — never guess
     or estimate one.
   — If the snippets contain NOTHING relevant to the search context, return {{"results": []}}.
     An empty result is correct and expected when the page has no matching listings — never
     invent one to avoid an empty answer.

Return JSON: {{"results": [list of objects]}}
Fields per result: {fields} PLUS always include "price_raw" (the exact price text from the snippet).

DETAIL — be thorough, WHATEVER the category. Add up to 6 EXTRA snake_case fields per result
for any other clearly-comparable attribute the listing prominently shows — the goal is to
capture as much real detail as possible so the user can compare like-for-like:
  — Flights: layover, baggage, seat_pitch, refund_policy, departure_terminal, arrival_terminal,
    aircraft_type, meal_included.
  — Trains: train_number, coach_class, quota, boarding_station, pantry, running_days.
  — Buses: boarding_point, dropping_point, seat_type, live_tracking, amenities.
  — Hotels: bed_type, room_size, view, max_occupancy, cancellation_window, check_in_time,
    check_out_time, distance_to_landmark.
  — Products: storage/RAM/color/size variant, warranty, brand, model_number, key_specs,
    delivery_estimate, return_policy, offers, exchange_offer.
  — Car rentals: car_model, fuel, transmission, seats, mileage, free_cancellation, provider.
  — Events: venue, date, seating_type, tier, age_limit, duration, artist.
  — Restaurants: cuisine, seating_type, dress_code, opening_hours, veg, avg_cost_for_two.
ONLY add a field when a real value for it is present in the snippet — do not invent fields
or values, and do not pad with nulls just to fill the quota.

=== STRICT RULES ===

1. PRICE:
   — Find the price text in the snippet (e.g. "$9.39", "₹1,579", "USD 77/night", "€45").
   — Store the EXACT original text in "price_raw" (e.g. "$9.39/night").
   — Also store the numeric value ONLY (no symbols) in "price" (e.g. 9.39).
   — Do NOT convert currency — Python will handle that.
   — Numbers like "9.2 rating", "39 reviews", "4/5 stars" are NOT prices. Ignore them.
   — If no explicit price found → set price and price_raw to null.

2. NAME — real names only:
   — Hotels: property name (e.g. "Snow Valley Resorts", "Joeys Hostel").
   — Flights: airline + flight number (e.g. "IndiGo 6E-201").
   — Cars: model or category (e.g. "Swift Dzire", "Hatchback").
   — Never use generic names like "Option 1", "Result N". Set null if no real name found.

3. CABIN / SEAT CLASS — for flights, trains and buses, find the travel class mentioned in the
   listing (e.g. "Economy", "Premium Economy", "Business Class", "First Class", "1st Class",
   "Business", "Sleeper", "AC 2 Tier", "Chair Car", "Saver", "Flexi Plus", "Semi-Sleeper", "AC Sleeper").
   — Normalize it into "cabin_class" using the listing's own wording where possible
     (e.g. "Business Class" → "Business", "1st Class"/"First" → "First").
   — If the listing doesn't mention a class at all, set "cabin_class" to null — do NOT guess
     "Economy" by default. This field is REQUIRED (even if null) for flights/trains/buses.

3b. ROOM TYPE — for hotels, find the room/property type mentioned in the listing
   (e.g. "Deluxe Room", "Standard Room", "Superior Room", "Suite", "Twin Room", "Studio",
   "Villa", "Dorm Bed", "Private Room").
   — Store it verbatim (or lightly normalized) in "room_type".
   — If the listing doesn't mention a room type at all, set "room_type" to null — do NOT
     guess "Standard Room" by default. This field is REQUIRED (even if null) for hotels.

3c. TYPE / VARIANT for every OTHER category — always try to capture the one field that says
   "what kind of thing is this", because the comparison groups like-for-like on it:
   — Products: the buying variant in "variant" (e.g. "128GB · Black", "Large", "1kg") and
     "condition" (see rule 8).
   — Car rentals: the vehicle class in "category" (e.g. "Hatchback", "SUV", "Sedan", "Luxury").
   — Buses: the coach in "bus_type" (e.g. "AC Sleeper", "Non-AC Seater", "Volvo Multi-Axle").
   — Events: the ticket tier in "category" (e.g. "General", "Gold", "VIP", "Fan Pit").
   — Restaurants: the service in "service_type" (e.g. "Dine-in", "Delivery", "Takeaway").
   Store the listing's own wording; set the field to null if it isn't mentioned — never guess.

4. AMENITIES / FACILITIES — extract everything the listing mentions:
   — For boolean facilities (wifi, breakfast, pool, parking, free_cancellation, refundable,
     meal, emi, veg) set true ONLY if the text clearly says so, false if it says not included,
     null if not mentioned. Look for words like "Free WiFi", "Breakfast included",
     "Free cancellation", "Non-refundable", "Swimming pool", "No meal".
   — For text facilities (baggage, warranty, amenities, offers) copy the actual detail.
   — Capture rating as a number (e.g. "4.5"), and ratings out of 5 or out of 10 as-is.

5. SKIP: ads, homepage/sign-in links, category pages with no specific listing.

6. Up to 8 results. Prefer real, detailed listings — but when a snippet genuinely lists
   several options for THIS search (flights, rooms, products, trains…), include them all
   (up to 8) rather than dropping detail.

7. URL — match each listing to its real link:
   — The input has a PAGE CONTENT block (names + prices) and a LISTING LINKS block (text → url).
   — For each listing you extract from PAGE CONTENT, find the matching link in LISTING LINKS
     (match by name similarity) and use that URL.
   — The url MUST point at that specific item's own product/listing page. Never use the
     platform homepage, a category/browse page, or a search-results page as a listing url.
   — If no matching item-specific link found, set url to null.

8. CONDITION — for products, always include a "condition" field:
   — Set it to "refurbished", "renewed", "used", "open_box", or "second_hand" if the listing's
     own title/snippet says so (these are different products from a brand-new one and must be
     labeled, never hidden).
   — Otherwise set "condition" to "new".

9. RELEVANCE — the search context (below) describes what the user is looking for
   (e.g. product_name, condition). SKIP any listing that does not match:
   — Accessories/parts (phone cases, covers, screen protectors, chargers, cables, straps,
     skins, pouches, earphones) are NOT the product itself — skip them UNLESS the user's
     product_name is itself for that accessory.
   — If context.condition == "new" (or the user said "brand new"/"new"), SKIP any listing whose
     condition is refurbished/renewed/used/open_box/second_hand.
   — If context.condition is refurbished/used/etc., SKIP listings that don't match that condition.

10. ROUTE / LOCATION MATCH — when the context has an origin/destination (flights, trains,
   buses) or a location/city (hotels, restaurants, events):
   — ONLY extract listings for THAT exact route or location. A snippet may quote fares for
     other routes ("Kenya to India $420", "Jorhat to New Delhi") or other cities — SKIP those
     entirely. Do NOT borrow a price, date, or duration from a different route/city.
   — If a snippet is generic marketing copy ("the cheapest flight was IndiGo from $128",
     "IndiGo flies to 82 cities") with no concrete listing for THIS route, do not manufacture
     a result from it — return fewer results instead.

11. MULTIPLE LISTINGS — many snippets list SEVERAL options in one block, often in a compact
   tabular form (e.g. flights "Air India IX-1229 · 16h 35m · 14:10 ; IndiGo 6E-6512 · 10h 35m
   · 09:05", or hotels "Taj · ₹8,400 · 4.5★ ; Oberoi · ₹12,000 · 4.7★", or products
   "128GB ₹65,999 ; 256GB ₹72,999"). Extract EACH option as its own result, pulling every
   field that line exposes (its own price, time, name, variant, rating…) — do not collapse
   them into one or keep only the first.

Search context: {context}
"""


# Caps how many per-platform extraction calls hit the LLM at once. Cloud free tiers
# need a tight cap (rate limits); a LOCAL model has no rate limit, so the only limit
# is the GPU — we allow more concurrency there so the parallel per-site agents also
# extract in parallel. Tunable via LLM_PARSE_CONCURRENCY.
_PARSE_SEMAPHORE = threading.Semaphore(
    int(os.getenv("LLM_PARSE_CONCURRENCY", "2"))
)

# Serialize the BROWSER tier across platforms. We drive ONE real Chrome via a single
# CDP endpoint (:9222); several browser agents hitting it at once in separate event
# loops collide ("Event loop is closed") and the losers fall back to the bundled
# Chrome-for-Testing (not your signed-in browser). Running them one-at-a-time gives
# each agent clean exclusive access to your real Chrome. Raise BROWSER_USE_CONCURRENCY
# only if you accept Chrome-for-Testing fallback for parallel speed.
_BROWSER_SEMAPHORE = threading.Semaphore(
    max(1, int(os.getenv("BROWSER_USE_CONCURRENCY", "1")))
)


def _refresh_parse_concurrency() -> None:
    """Local LLM (Ollama) has no rate limit, so allow more concurrent extraction."""
    global _PARSE_SEMAPHORE
    try:
        from backend.llm import provider_available
        default = "5" if provider_available("ollama") else "2"
        n = int(os.getenv("LLM_PARSE_CONCURRENCY", default))
        _PARSE_SEMAPHORE = threading.Semaphore(max(1, n))
    except Exception:
        pass


def _loads_lenient(content: str) -> dict:
    """Parse an LLM JSON reply even when it's wrapped in ```json fences, prefixed with
    prose, or has trailing junk — some platform-parse models return exactly that
    'invalid JSON'. Raises ValueError only when no JSON object can be recovered."""
    if not content or not content.strip():
        raise ValueError("empty content")
    s = content.strip()
    if s.startswith("```"):
        s = _re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        s = _re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # Recover the outermost {...} object if the model added prose around it.
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j > i:
        return json.loads(s[i:j + 1])
    raise ValueError("no JSON object found in model reply")


def _parse_with_groq(snippets: list[dict], platform: dict, params: dict,
                     intent_type: str = "general") -> list[dict]:
    if not snippets:
        return []

    fields = list(platform.get("result_fields", ["name", "price", "url"]))

    # Augment with comparison-dimension keys so we capture amenities/facilities
    # (wifi, breakfast, free_cancellation, refundable, baggage, etc.) per category.
    cfg = _load_platforms()
    cat = cfg.get("categories", {}).get(intent_type, {})
    for dim in cat.get("comparison_dimensions", []):
        if dim["key"] not in fields:
            fields.append(dim["key"])
    if "name" not in fields:
        fields.insert(0, "name")

    # Separate the bulk page-content snippet from the listing-link snippets.
    # The universal filler returns: [{title:"Page content", snippet:<full text>, url}, {title, url}, ...]
    page_blocks = [s for s in snippets if s.get("snippet")]
    link_blocks = [s for s in snippets if not s.get("snippet") and s.get("url")]

    page_text = "\n\n".join(
        f"PAGE CONTENT ({s.get('url','')}):\n{s.get('snippet','')[:4000]}"
        for s in page_blocks[:3]
    )
    # Each page_block (e.g. a Tavily result) is itself a specific page with its own
    # url — usually the exact product/listing page for that result. Offer those as
    # candidate links too, so the parser can attach a real URL to the listing it
    # came from instead of leaving url null.
    link_lines = [f"- \"{s.get('title','')}\" → {s.get('url','')}" for s in link_blocks[:25]]
    link_lines += [f"- \"{s.get('title','')}\" → {s.get('url','')}" for s in page_blocks[:3] if s.get("url")]
    links_text = "\n".join(link_lines)
    raw = f"=== PAGE CONTENT ===\n{page_text}\n\n=== LISTING LINKS ===\n{links_text or '(none)'}"

    system_content = PARSE_SYSTEM.format(
        platform_name=platform["name"],
        fields=", ".join(fields),
        context=json.dumps(params),
    )

    raw_results = []
    # The parser is the heaviest LLM caller (one call per platform). chat() routes it
    # to its primary provider (Gemini by default) and auto-fails-over to the other
    # (Groq) on a rate-limit, so a single provider's per-minute cap no longer drops a
    # platform. The semaphore still caps how many parses run at once; the backoff loop
    # only kicks in if BOTH providers are momentarily exhausted.
    with _PARSE_SEMAPHORE:
        for attempt in range(3):
            try:
                resp = chat(
                    "search_parse",
                    messages=[
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": raw},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    max_tokens=2500,
                    # Hard timeout so a slow/rate-limited provider (esp. the local Ollama
                    # fallback under burst load) FAILS FAST instead of stalling the whole
                    # search to the overall timeout. Tunable via PARSE_TIMEOUT.
                    timeout=float(os.getenv("PARSE_TIMEOUT", "22") or 22),
                )
                parsed = _loads_lenient(resp.choices[0].message.content)
                raw_results = parsed.get("results", [])
                break

            except Exception as e:
                msg = str(e).lower()
                # TEMP (JSON-robustness): malformed/empty JSON is retryable too, not just
                # rate limits — some platform-parse models return invalid JSON.
                _json_err = isinstance(e, (json.JSONDecodeError, ValueError)) or "json" in msg or "expecting value" in msg
                if ("rate" in msg or "quota" in msg or "exhausted" in msg) and attempt < 2:
                    wait = 8.0
                    m = _re.search(r"try again in ([\d.]+)s", str(e))
                    if m:
                        wait = float(m.group(1)) + 0.5
                    logger.info(f"  All providers busy ({platform['name']}), retrying in {wait:.1f}s…")
                    time.sleep(wait)
                    continue
                if _json_err and attempt < 2:
                    logger.info(f"  Malformed JSON from {platform['name']} parser; retrying...")
                    time.sleep(0.5)
                    continue
                logger.warning(f"Parse error ({platform['name']}): {e}")
                return []

    # ── Post-processing: convert currency, validate, clean ──
    requested_condition = str(params.get("condition") or "").strip().lower()
    product_name = str(params.get("product_name") or "").strip().lower()

    clean = []
    for r in raw_results:
        if not isinstance(r, dict):
            continue

        name = r.get("name") or r.get("title") or r.get("car_model") or ""
        if _is_generic_name(name):
            logger.debug(f"  Rejected generic name: '{name}'")
            continue

        # Drop accessories/parts when the user asked for the device itself.
        if intent_type == "product":
            name_l = name.lower()
            is_accessory = any(w in name_l for w in _ACCESSORY_WORDS)
            wants_accessory = any(w in product_name for w in _ACCESSORY_WORDS)
            if is_accessory and not wants_accessory:
                logger.debug(f"  Rejected accessory: '{name}'")
                continue

        # The "View on X" link must point at X's own site — not a third-party news
        # article that merely quotes a price (e.g. an Economic Times piece about a
        # Croma sale). Drop urls that don't belong to the platform's own domain.
        url = r.get("url")
        if url:
            platform_domain = _domain_from_url(platform.get("website", ""))
            if platform_domain and platform_domain not in _domain_from_url(url):
                logger.debug(f"  Dropped off-site url for '{name}': {url}")
                r["url"] = None

        # Enforce the requested condition (e.g. "brand new" must exclude refurbished/used).
        condition = str(r.get("condition") or "new").strip().lower()
        if requested_condition and requested_condition != condition:
            logger.debug(f"  Rejected condition mismatch ({condition} != {requested_condition}): '{name}'")
            continue
        if not requested_condition and condition != "new":
            logger.debug(f"  Rejected non-new without explicit request ({condition}): '{name}'")
            continue

        # Step 1: Python currency conversion for all price fields
        price_raw = r.pop("price_raw", None)  # remove helper field
        for pf in PRICE_FIELDS:
            if pf in r:
                inr_val = _raw_price_to_inr(price_raw, r.get(pf), intent_type)
                r[pf] = inr_val

        # Step 2: Domain bounds validation
        for pf in PRICE_FIELDS:
            if pf in r and r[pf] is not None:
                validated = _validate_price(r[pf], intent_type)
                r[pf] = validated

        # Step 3: Remove null price keys
        for pf in PRICE_FIELDS:
            if pf in r and r[pf] is None:
                del r[pf]

        clean.append(r)

    logger.info(f"  Parser: {len(raw_results)} raw → {len(clean)} valid results")
    return clean[:8]


# ── Roadblock explanation ─────────────────────────────────────

def _build_roadblock(platform: dict, tiers_tried: list, tavily_raw_count: int,
                     params: dict | None = None) -> dict:
    """Turn an empty-handed search into a plain-English explanation + suggestion the
    user can act on via the 'help it along' retry. Also attaches the SPECIFIC browser
    error AND the monitor agent's diagnosis (root cause + suggested fix)."""
    name = platform.get("name", "this platform")
    tried = ", ".join(t["tier"] for t in tiers_tried) or "every method"
    bu_ran = any(t["tier"] == "browser-use" for t in tiers_tried)

    # The exact error from the live browser run + the monitor agent's analysis.
    specific_error = None
    analysis = None
    try:
        from backend.browser_tracker import get_browser_tracker
        bt = get_browser_tracker()
        run = bt.get(platform.get("id", ""))
        specific_error = (run or {}).get("error")
        if bu_ran and run is not None:
            # Supervisor agent diagnoses WHY it failed and proposes a fix.
            from backend.monitor_agent import analyze_failure
            analysis = analyze_failure(name, params or {}, run)
            bt.set_analysis(platform.get("id", ""), analysis)
    except Exception as e:
        logger.debug(f"Monitor analysis skipped for {name}: {e}")

    if tavily_raw_count and not bu_ran:
        reason = (f"{name} returned search snippets, but none matched your exact "
                  f"request, so nothing was kept.")
        suggestion = ("Try the live browser with a hint about where the results are "
                      "(e.g. 'click the Search button', 'open the Flights tab').")
    elif bu_ran:
        reason = (f"The live browser opened {name} but couldn't reach a results list — "
                  f"the page may have changed, loaded slowly, or asked to verify you're human.")
        suggestion = ("Tell it the next step you'd take by eye — e.g. 'dismiss the popup "
                      "first', 'the date field is a calendar', or paste a direct results URL.")
    else:
        reason = f"None of the search methods ({tried}) found live listings for {name}."
        suggestion = ("Retry with the live browser and a hint, or open the platform "
                      "directly using its link above.")
    # Prefer the monitor agent's plain-English diagnosis + concrete fix when available.
    if analysis:
        reason = analysis.get("diagnosis") or reason
        if analysis.get("suggested_hint"):
            suggestion = f"Suggested fix: {analysis['suggested_hint']}"
        elif analysis.get("category") == "bot_block":
            suggestion = "This is a bot/login wall — it can't be bypassed. Open the site yourself from its link."
    return {
        "reason": reason,
        "suggestion": suggestion,
        "error": specific_error,                      # the exact blocker, shown verbatim
        "analysis": analysis,                          # monitor agent's full diagnosis
        "tiers_tried": [t["tier"] for t in tiers_tried],
    }


# ── Per-platform worker (runs in thread) ─────────────────────

def _search_one_platform_sync(platform_id: str, params: dict,
                              intent_type: str = "general",
                              hint: str = "", force_browser: bool = False,
                              headed: bool = False
                              ) -> tuple[str, PlatformResult]:
    """Synchronous per-platform search. Called from a ThreadPoolExecutor.

    `hint` is user guidance passed to the browser agent (recovery flow).
    `force_browser` skips Tavily and drives the live browser first — used when the
    user retries a stuck platform, since we already know Tavily was thin for it.
    `headed` opens a VISIBLE Chrome window for that browser run so the user can
    watch and guide it (the "open the browser so I can see the problem" flow).
    """
    platform = _get_platform_config(platform_id)
    if not platform:
        return platform_id, PlatformResult(
            platform_id=platform_id, platform_name=platform_id, icon="❓",
            results=[], raw_snippets=[], error="Platform config not found",
            elapsed_seconds=0.0, tier="", roadblock={
                "reason": "This platform isn't configured.",
                "suggestion": "Nothing to retry — it was removed or misnamed in config.",
            },
        )

    start = time.time()
    query = _build_query(platform, params)
    logger.info(f"Searching {platform['name']}: {query[:60]}")
    emit(f"Searching {platform['name']}…", stage="search", kind="start")
    _agent = f"{platform['name']} Agent"
    bus_send(frm="Search Coordinator", to=_agent, kind="dispatch",
             title=f"Search {platform['name']}", content={"query": query, "params": params},
             meta={"platform_id": platform_id})

    from backend.tools.url_builder import build_search_url
    platform_search_url = build_search_url(platform, params)
    website = platform.get("website", "")
    results = []
    snippets = []
    tier = ""                 # which tier produced the kept results
    tiers_tried: list[dict] = []  # [{tier, seconds, n}] — for the diagnostics panel

    # DEMO / FAST MODE: when SEARCH_API_ONLY=true, use ONLY the API/HTTP tiers (Tavily
    # primary + DDG fallback + the SerpApi flight path) and skip ALL browser tiers
    # (deep-link scrape, browser-use agent, universal form-filler, Playwright Google).
    # → fast (~3-10s/platform) and reliable, no Chrome windows, no bot-walls.
    _api_only = os.getenv("SEARCH_API_ONLY", "").lower() in ("1", "true", "yes")

    def _leg(name: str, fn):
        """Run one search-tier attempt, timing it and recording the leg."""
        t0 = time.time()
        out = fn()
        tiers_tried.append({"tier": name, "seconds": round(time.time() - t0, 2),
                            "n": len(out) if out else 0})
        return out

    # ── 0. RAG cache — a near-identical (platform, params) search answered recently.
    #       Skips the ENTIRE cascade below, including the parse LLM call and any
    #       browser-use run, so this is the only tier that cuts both time AND LLM
    #       usage. Bypassed on a user-driven retry (force_browser) — that's an
    #       explicit "go live again" signal. Always labeled "cache" in the UI with
    #       its age, never presented as a fresh live result. ──
    if not force_browser:
        from backend.memory.search_cache import get_cached
        cached = _leg("cache", lambda: get_cached(intent_type, platform_id, params) or [])
        if cached:
            results = cached
            tier = "cache"
            emit(f"⚡ {platform['name']}: served from cache", stage="search", kind="ok")

    # ── 1. Tavily — fast, real-page snippets restricted to the platform's own
    #       domain (~3s). This is the FAST PATH and covers most platforms.
    #       Skipped entirely on a user-driven retry (force_browser): we already
    #       know Tavily was thin, so go straight to the live browser. Also
    #       skipped on a cache hit — `results` is already populated. ──
    tavily_key = os.getenv("TAVILY_API_KEY", "")
    tavily_raw_count = 0
    if tavily_key and not force_browser and not results:
        logger.info(f"  → Tavily (primary) for {platform['name']}")
        domain = _domain_from_url(website) if website else ""
        snippets = _leg("tavily", lambda: _tavily_search(query, tavily_key, domain=domain))
        if not snippets and domain:
            # Platform thin on Tavily's index for its own domain — retry without
            # the restriction rather than jumping straight to browser-use.
            snippets = _leg("tavily-open", lambda: _tavily_search(query, tavily_key))
        tavily_raw_count = len(snippets)
        results = _parse_with_groq(snippets, platform, params, intent_type) if snippets else []
        if results:
            tier = "tavily"

    # ── 1b. Deterministic deep-link scrape — navigate STRAIGHT to the platform's own
    #        pre-filled results URL and read the rendered page. No LLM in the browser
    #        loop, so it's much faster than the LLM agent. BUT it only works on sites
    #        that server-render results (simple product/listing pages) — heavy JS sites
    #        like flight OTAs render fares async after load and land on a promo state, so
    #        the scrape gets junk AND is slow. So this tier is OPT-IN per platform:
    #        set `fast_scrape: true` in platforms.yaml only for sites where it's verified
    #        to return real results fast. (For flights, prefer an official API.) ──
    if (platform.get("fast_scrape") and not results and not force_browser and not _api_only
            and platform_search_url and platform_search_url != website):
        logger.info(f"  → deep-link scrape (deterministic) for {platform['name']}")
        emit(f"Reading {platform['name']}'s results page…", stage="search", kind="info")
        scrape_snippets = _leg("scrape", lambda: _playwright_run(
            _scrape_platform(platform["name"], platform_search_url), timeout=25))
        scrape_results = _parse_with_groq(scrape_snippets, platform, params, intent_type) if scrape_snippets else []
        if scrape_results:
            results = scrape_results
            snippets = scrape_snippets
            tier = "scrape"

    # ── 1c. Selenium + BeautifulSoup — real Chrome renders the results page once,
    #        BS4 parses it. Applies to EVERY platform (unlike the opt-in `fast_scrape`
    #        tier above) — deliberately short-timeout (SELENIUM_TIMEOUT, default 6s)
    #        with a fail-fast sanity check, so a heavy-JS/async-fare platform that
    #        can't be scraped this way misses in a few seconds instead of the
    #        "junk AND slow" failure the deep-link tier's own comments warn about.
    #        Still LLM-parsed via _parse_with_groq like every other tier; a hit here
    #        means browser-use never runs for this platform at all. ──
    if not results and not force_browser and not _api_only:
        from backend.tools.selenium_scraper import scrape_platform_selenium, is_enabled as _selenium_enabled
        if _selenium_enabled():
            logger.info(f"  → Selenium+BS4 scrape for {platform['name']}")
            sel_snippets = _leg("selenium", lambda: scrape_platform_selenium(
                platform["name"], platform_search_url or website,
                timeout_seconds=float(os.getenv("SELENIUM_TIMEOUT", "6"))))
            sel_results = _parse_with_groq(sel_snippets, platform, params, intent_type) if sel_snippets else []
            if sel_results:
                results = sel_results
                snippets = sel_snippets
                tier = "selenium"

    # ── 2. browser-use agent — an LLM drives a real browser (SLOW PATH, ~30-90s).
    #       Production search engines (Perplexity et al.) escalate to the expensive
    #       tier RARELY — cheap retrieval first, slow tier only when it returns nothing.
    #       So by default we fire the live browser ONLY when a platform STILL has NO
    #       usable results (Tavily + the deterministic scrape both came up empty). Set
    #       BROWSER_USE_ON_THIN=true to also chase thin detail; the manual "Open browser
    #       & fix" still works always. ──
    from backend.tools.browser_agent import is_enabled as _bu_enabled
    escalate_on_thin = os.getenv("BROWSER_USE_ON_THIN", "false").lower() == "true"
    tavily_thin = force_browser or not results or (
        escalate_on_thin and _is_detail_thin(results, intent_type))
    if tavily_thin and _bu_enabled() and not _api_only:
        # Don't start the agent on a half-built deep link (empty date params → the
        # ERR_HTTP2_PROTOCOL_ERROR "site can't be reached" you saw); use the homepage
        # and let the agent fill the form. It can also self-recover to the homepage.
        # START AT THE HOMEPAGE, not a deep link. Deep-link templates go stale and
        # 404 (the ixigo/IRCTC/ConfirmTkt 404s you saw), burning the whole run on
        # self-recovery. The homepage always loads; the agent fills the form from there.
        # Opt back into deep links with BROWSER_USE_DEEPLINK=true if a site needs it.
        if os.getenv("BROWSER_USE_DEEPLINK", "false").lower() in ("1", "true", "yes"):
            entry_url = _good_entry_url(platform_search_url, website) or website
        else:
            entry_url = website or _good_entry_url(platform_search_url, website)
        if entry_url:
            reason = ("user retry" if force_browser else
                      "no data" if tavily_raw_count < 2 else
                      "empty" if not results else "detail-thin")
            logger.info(f"  → browser-use agent for {platform['name']} ({reason}) @ {entry_url[:60]}")
            emit(f"Browsing {platform['name']} live for full details…", stage="search", kind="info")
            # Take turns on the single real Chrome (see _BROWSER_SEMAPHORE) — one agent
            # at a time so every run attaches to YOUR Chrome, never the Testing fallback.
            with _BROWSER_SEMAPHORE:
                bu_snippets = _leg("browser-use", lambda: _playwright_run(
                    _browser_use_run(platform["name"], entry_url, params,
                                     hint=hint, platform_id=platform_id,
                                     headless=(False if headed else None), homepage=website),
                    # A visible, user-guided run needs more time than a headless one.
                    # TEMP (bot-reasoning test): headless budget now tunable via BROWSER_USE_TIMEOUT.
                    timeout=150 if headed else int(os.getenv("BROWSER_USE_TIMEOUT", "75"))))
            bu_results = _parse_with_groq(bu_snippets, platform, params, intent_type) if bu_snippets else []
            # Keep the live results when they're richer (more total detail) OR
            # simply more numerous than what Tavily gave us.
            if bu_results and (
                _detail_total(bu_results, intent_type) > _detail_total(results, intent_type)
                or len(bu_results) > len(results)
            ):
                logger.info(f"    browser-use enriched {platform['name']}: "
                            f"{len(results)}→{len(bu_results)} results, "
                            f"detail {_detail_total(results, intent_type)}→{_detail_total(bu_results, intent_type)}")
                results = bu_results
                snippets = bu_snippets
                tier = "browser-use"

    # ── 2b. Universal form automation — last resort, only if we STILL have nothing
    #       after Tavily, the deterministic scrape, and the LLM browser. ──
    if not results and not _api_only:
        entry_url = platform_search_url or website
        if entry_url:
            logger.info(f"  → Universal browser automation for {platform['name']}")
            auto_snippets = _leg("universal", lambda: _playwright_run(
                _universal_fill(platform["name"], entry_url, params)))
            auto_results = _parse_with_groq(auto_snippets, platform, params, intent_type) if auto_snippets else []
            if len(auto_results) > len(results):
                results = auto_results
                snippets = auto_snippets
                tier = "universal"

    # ── 3. Google search via Playwright ──
    if not results and not _api_only:
        logger.info(f"  → Google search fallback for {platform['name']}")
        snippets = _leg("google", lambda: _playwright_google_search(query))
        results  = _parse_with_groq(snippets, platform, params, intent_type) if snippets else []
        if results:
            tier = "google"

    # ── 4. DuckDuckGo last resort — SKIPPED in API-only/demo mode: the duckduckgo_search
    #       lib rate-limits and retries with backoff, which made empty platforms HANG up
    #       to the overall timeout (the 126s hotel search). Tavily-only is fast + reliable.
    if not results and not _api_only:
        snippets = _leg("ddg", lambda: _ddg_search(query))
        results  = _parse_with_groq(snippets, platform, params, intent_type) if snippets else []
        if results:
            tier = "ddg"

    elapsed = round(time.time() - start, 2)
    logger.info(f"  {platform['name']}: {len(results)} results in {elapsed}s via {tier or 'none'}")

    # Feed the RAG cache for next time — only a FRESH live result is worth caching;
    # a cache-hit re-caching itself would just refresh its own timestamp for free.
    if results and tier != "cache":
        from backend.memory.search_cache import set_cached
        set_cached(intent_type, platform_id, params, results)

    # Build a plain-English roadblock when nothing came back, so the UI can explain
    # what happened and offer the "help it along" retry instead of a silent blank.
    roadblock = None
    if not results:
        roadblock = _build_roadblock(platform, tiers_tried, tavily_raw_count, params)

    if results:
        emit(f"Found {len(results)} option{'s' if len(results) != 1 else ''} on {platform['name']}",
             stage="search", kind="ok")
        bus_send(frm=_agent, to="Search Coordinator", kind="data",
                 title=f"Returned {len(results)} result(s) via {tier or 'search'}",
                 content={"count": len(results), "tier": tier,
                          "elapsed_s": elapsed, "sample": results[:2]},
                 meta={"platform_id": platform_id})
    else:
        emit(f"No results on {platform['name']}", stage="search", kind="warn")
        bus_send(frm=_agent, to="Search Coordinator", kind="error",
                 title="No results — hit a roadblock",
                 content=(roadblock or {}).get("reason", "No results found."),
                 meta={"platform_id": platform_id})

    # Record this platform's outcome into the run diagnostics (timing + tier legs).
    try:
        from backend.diagnostics import get_diagnostics
        get_diagnostics().record_platform(
            platform_id, platform_name=platform["name"], tier=tier,
            n_results=len(results), elapsed=elapsed, tiers_tried=tiers_tried,
            roadblock=roadblock,
        )
    except Exception:
        pass

    return platform_id, PlatformResult(
        platform_id=platform_id,
        platform_name=platform["name"],
        icon=platform.get("icon", "🔍"),
        results=results,
        raw_snippets=[{"title": s.get("title",""), "snippet": s.get("snippet","")[:150]}
                      for s in snippets[:3]],
        error=None,
        elapsed_seconds=elapsed,
        tier=tier,
        roadblock=roadblock,
    )


# ── LangGraph node ────────────────────────────────────────────

# ── Fast flight tier — Google Flights data API (no browser, no Groq) ──────────
_IATA = {
    "mumbai": "BOM", "bombay": "BOM", "delhi": "DEL", "new delhi": "DEL",
    "bangalore": "BLR", "bengaluru": "BLR", "hyderabad": "HYD", "chennai": "MAA",
    "kolkata": "CCU", "goa": "GOI", "pune": "PNQ", "ahmedabad": "AMD",
    "jaipur": "JAI", "kochi": "COK", "lucknow": "LKO", "dubai": "DXB",
    "singapore": "SIN", "london": "LHR", "new york": "JFK",
}


def _iata(city: str) -> str:
    return _IATA.get((city or "").strip().lower(), (city or "XXX")[:3].upper())


def _flight_base_params(params: dict) -> dict:
    """The SerpApi google_flights query shared by the fares call and the per-website
    booking-options calls (so they describe the exact same search)."""
    origin = _iata(params.get("origin", ""))
    dest = _iata(params.get("destination", ""))
    date = str(params.get("date") or params.get("depart_date") or "").strip()
    if not date:
        from datetime import date as _d, timedelta
        date = (_d.today() + timedelta(days=14)).isoformat()
    return {
        "engine": "google_flights", "departure_id": origin, "arrival_id": dest,
        "outbound_date": date, "type": 2, "currency": "INR", "gl": "in", "hl": "en",
    }


def _serpapi_flights(params: dict, keep_token: bool = False) -> list[dict]:
    """Real flights from Google Flights via SerpApi — fast (~few s), reliable, no
    browser automation and no Groq dependency. This is what makes a flight search
    actually return results quickly instead of grinding through OTA browser agents.
    With keep_token=True each fare also carries its `_booking_token` so we can fetch
    the list of booking WEBSITES + prices for it."""
    key = os.getenv("SERPAPI_KEY", "").strip()
    if not key:
        return []
    base = _flight_base_params(params)
    origin, dest, date = base["departure_id"], base["arrival_id"], base["outbound_date"]
    try:
        import requests
        r = requests.get("https://serpapi.com/search.json",
                         params={**base, "api_key": key}, timeout=25)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"SerpApi flights failed: {e}")
        return []
    groups = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    out = []
    for g in groups[:8]:
        segs = g.get("flights", [])
        if not segs:
            continue
        first, last = segs[0], segs[-1]
        dur = g.get("total_duration")
        item = {
            "name": f'{first.get("airline","")} {first.get("flight_number","")}'.strip(),
            "airline": first.get("airline", ""),
            "price": float(g["price"]) if g.get("price") else None,
            "stops": len(segs) - 1,
            "duration": (f"{dur//60}h {dur%60}m" if dur else ""),
            "departure_time": (first.get("departure_airport", {}).get("time", "") or "")[-5:],
            "arrival_time": (last.get("arrival_airport", {}).get("time", "") or "")[-5:],
            "cabin_class": first.get("travel_class", "Economy"),
            "url": ("https://www.google.com/travel/flights?q="
                    + _re.sub(r"\s+", "+", f"flights from {origin} to {dest} on {date}")),
        }
        if keep_token:
            item["_booking_token"] = g.get("booking_token", "")
        out.append(item)
    return out


# Friendly icons for the common Indian/global flight booking sites.
_FLIGHT_SITE_ICONS = {
    "makemytrip": "🧳", "goibibo": "🧳", "cleartrip": "🛫", "yatra": "🧭",
    "easemytrip": "💺", "paytm travel": "💳", "booking.com": "🏨", "ixigo": "🐦",
    "expedia": "🌐", "kayak": "🌐", "akasa air": "✈️", "indigo": "✈️",
    "air india": "✈️", "vistara": "✈️", "spicejet": "✈️", "google flights": "✈️",
}


def _flight_site_icon(name: str) -> str:
    return _FLIGHT_SITE_ICONS.get((name or "").strip().lower(), "🌐")


def _serpapi_booking_options(params: dict, token: str) -> list[dict]:
    """Given a fare's booking_token, return the list of WEBSITES selling that exact
    flight (Goibibo, Cleartrip, YATRA, airline-direct…) each with its own price."""
    key = os.getenv("SERPAPI_KEY", "").strip()
    if not key or not token:
        return []
    try:
        import requests
        r = requests.get("https://serpapi.com/search.json", params={
            **_flight_base_params(params), "booking_token": token, "api_key": key,
        }, timeout=25)
        r.raise_for_status()
        return r.json().get("booking_options") or []
    except Exception as e:
        logger.warning(f"SerpApi booking options failed: {e}")
        return []


def _flight_site_platforms(params: dict, flights: list[dict]) -> dict:
    """Pivot Google Flights' booking options into one platform PER WEBSITE, so the
    comparison shows real fares across Goibibo / Cleartrip / YATRA / airline-direct…
    Costs one extra SerpApi call per fare expanded (FLIGHT_BOOKING_DEPTH, default 3),
    so it's capped + toggleable to protect the free quota."""
    if os.getenv("FLIGHT_BOOKING_SITES", "true").lower() not in ("1", "true", "yes"):
        return {}
    depth = int(os.getenv("FLIGHT_BOOKING_DEPTH", "3") or 3)
    max_sites = int(os.getenv("FLIGHT_BOOKING_MAX_SITES", "8") or 8)

    sites: dict[str, list[dict]] = {}
    # Fetch every fare's booking-options IN PARALLEL — these were sequential SerpApi
    # calls (~20s each) and dominated flight latency (the 78s search). One thread per
    # fare collapses it to roughly a single call's time.
    from concurrent.futures import ThreadPoolExecutor
    fares = [f for f in flights[:depth] if f.get("_booking_token")]
    with ThreadPoolExecutor(max_workers=max(1, len(fares))) as _pool:
        opt_results = list(_pool.map(
            lambda f: (f, _serpapi_booking_options(params, f["_booking_token"])), fares))
    for f, opts in opt_results:
        for o in opts:
            t = o.get("together") or o.get("departing") or {}
            name = (t.get("book_with") or "").strip()
            price = t.get("price")
            if not name or not price:
                continue
            row = {k: f.get(k) for k in ("name", "airline", "stops", "duration",
                                         "departure_time", "arrival_time", "cabin_class")}
            row["price"] = float(price)
            row["booking_site"] = name
            row["url"] = f.get("url", "")
            sites.setdefault(name, []).append(row)

    # Rank websites by their cheapest offer; keep the top few to avoid clutter.
    out: dict = {}
    ranked = sorted(sites.items(), key=lambda kv: min(r["price"] for r in kv[1]))
    for name, rows in ranked[:max_sites]:
        # one row per flight per site (the cheapest), sorted cheapest-first
        best: dict[str, dict] = {}
        for r in rows:
            k = r["name"]
            if k not in best or r["price"] < best[k]["price"]:
                best[k] = r
        rows2 = sorted(best.values(), key=lambda r: r["price"])
        pid = "site_" + _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        out[pid] = PlatformResult(
            platform_id=pid, platform_name=name, icon=_flight_site_icon(name),
            results=rows2, raw_snippets=[], error=None, elapsed_seconds=0.0,
            tier="google-flights-booking", roadblock=None,
        )
        try:
            record_outcome(platform_id=pid, platform_name=name, success=True,
                           error=None, result_count=len(rows2), elapsed_seconds=0.0)
        except Exception:
            pass
    return out


def _site_names_from_results(platforms: dict) -> list[str]:
    """Platform display names from a {platform_id: PlatformResult} map — for the bus."""
    return [p.get("platform_name", pid) for pid, p in (platforms or {}).items()]


def _cheapest_summary(flights: list) -> str:
    """'₹8,859' for the lowest fare in a list of fare dicts — a readable one-liner."""
    prices = []
    for f in flights or []:
        for pf in ("price", "fare", "total_price"):
            v = f.get(pf)
            if v:
                try:
                    prices.append(float(str(v).replace(",", "").replace("₹", "")))
                except Exception:
                    pass
                break
    return f"₹{int(min(prices)):,}" if prices else "n/a"


def search_platforms_node(state: AgentState) -> dict:
    intent = state.get("intent")
    if not intent:
        return {"status": "error", "error": "No intent parsed"}

    platforms    = intent["platforms"]
    params       = intent["params"]
    intent_type  = intent.get("type", "general")

    # ── FAST FLIGHT PATH ── For flights, skip the slow per-OTA browser automation
    # entirely and pull real fares from the Google Flights data API in one fast call.
    if intent_type == "flight" and os.getenv("SERPAPI_KEY", "").strip():
        emit("Getting live flight fares…", stage="search", kind="start")
        bus_send(frm="Search Coordinator", to="Flights API Agent", kind="dispatch",
                 title="Fetch live fares (Google Flights)",
                 content={k: params.get(k) for k in ("origin", "destination", "date",
                          "return_date", "cabin_class") if params.get(k)})
        flights = _serpapi_flights(params, keep_token=True)
        if flights:
            logger.info(f"Fast flight tier: {len(flights)} fares from Google Flights")
            # Expand into one platform PER booking website (Goibibo, Cleartrip, YATRA…)
            # so the comparison spans real sites, not just Google Flights.
            emit("Comparing prices across booking sites…", stage="search", kind="start")
            site_platforms = _flight_site_platforms(params, flights)
            # tokens were only needed for the booking-options calls — don't leak to UI
            for f in flights:
                f.pop("_booking_token", None)

            results: dict = dict(site_platforms)
            results["google_flights"] = PlatformResult(
                platform_id="google_flights", platform_name="Google Flights", icon="✈️",
                results=flights, raw_snippets=[], error=None, elapsed_seconds=0.0,
                tier="google-flights-api", roadblock=None,
            )
            try:
                record_outcome(platform_id="google_flights", platform_name="Google Flights",
                               success=True, error=None, result_count=len(flights),
                               elapsed_seconds=0.0)
            except Exception:
                pass
            n_sites = len(site_platforms)
            emit(f"Found {len(flights)} flights across {n_sites + 1} sites",
                 stage="search", kind="ok")
            bus_send(frm="Flights API Agent", to="Search Coordinator", kind="data",
                     title=f"Returned {len(flights)} fares across {n_sites + 1} sites",
                     content={"fares": len(flights),
                              "booking_sites": _site_names_from_results(site_platforms),
                              "cheapest": _cheapest_summary(flights)})
            return {"platform_results": results, "status": "aggregating"}

    # Fresh browser-use tracking for this search (clears any prior run's steps).
    try:
        from backend.browser_tracker import get_browser_tracker
        get_browser_tracker().clear()
    except Exception:
        pass

    # Let the per-site agents extract with more concurrency when on a local model.
    _refresh_parse_concurrency()

    logger.info(f"Launching {len(platforms)} parallel platform searches (intent={intent_type})…")
    emit(f"Searching {len(platforms)} platforms…", stage="search", kind="start")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    platform_results = {}
    # Each platform gets its own thread — Chrome windows open simultaneously.
    # Universal form automation is slower per platform, so allow up to 150s overall.
    from backend.progress import is_cancelled
    overall_timeout = int(os.getenv("SEARCH_OVERALL_TIMEOUT", "120") or 120)
    # BROWSER_USE_ALWAYS: drive the VISIBLE real browser on every platform (skip the
    # Tavily/API fast path) so the user always sees the automation. Slower, but it's
    # what they asked for. Off → fast tiers first, browser only when they come up empty.
    _always = os.getenv("BROWSER_USE_ALWAYS", "").lower() in ("1", "true", "yes")
    if _always:
        logger.info("BROWSER_USE_ALWAYS=on → forcing the visible browser for every platform")
    # NOTE: do NOT use `with ThreadPoolExecutor(...)` — its __exit__ calls
    # shutdown(wait=True), which BLOCKS on a hung platform thread and ignores the
    # overall timeout (this was the 123s hotel hang: one slow platform stalled the
    # whole search). We shut down with wait=False so a stuck thread is abandoned and
    # the search returns within `overall_timeout`.
    # Throttle concurrency: 5 platforms firing Tavily + LLM-parse at once overwhelm the
    # free tiers (rate-limit → 0 results, e.g. the 5-platform hotel search returning
    # nothing while ONE platform alone returns 4). A small worker cap keeps every call
    # inside the providers' limits. Tunable via SEARCH_MAX_CONCURRENCY.
    _max_conc = max(1, int(os.getenv("SEARCH_MAX_CONCURRENCY", "3") or 3))
    pool = ThreadPoolExecutor(max_workers=min(len(platforms), _max_conc))
    futures = {
        pool.submit(_search_one_platform_sync, pid, params, intent_type,
                    force_browser=_always): pid
        for pid in platforms
    }
    try:
        try:
            for future in as_completed(futures, timeout=overall_timeout):
                # User hit "New search (clear & stop)" → stop collecting and bail.
                if is_cancelled():
                    emit("Search cancelled.", stage="search", kind="warn")
                    break
                try:
                    pid, result = future.result()
                    platform_results[pid] = result
                except Exception as e:
                    pid = futures[future]
                    logger.error(f"Thread error for {pid}: {e}")
                    platform_results[pid] = PlatformResult(
                        platform_id=pid, platform_name=pid, icon="❌",
                        results=[], raw_snippets=[], error=str(e), elapsed_seconds=0.0,
                        tier="", roadblock={
                            "reason": "This platform's search crashed before finishing.",
                            "suggestion": "Retry it — transient errors (a slow page, a timeout) "
                                          "often clear on a second attempt.",
                        },
                    )
        except TimeoutError:
            # Mark any platforms that didn't finish in time
            for future, pid in futures.items():
                if pid not in platform_results:
                    platform_results[pid] = PlatformResult(
                        platform_id=pid, platform_name=pid, icon="⏱️",
                        results=[], raw_snippets=[], error="Timed out",
                        elapsed_seconds=float(overall_timeout),
                        tier="", roadblock={
                            "reason": "This platform took too long and was cut off "
                                      "so the rest of the search could finish.",
                            "suggestion": "Retry just this one — on its own it has the full "
                                          "time budget and usually completes.",
                        },
                    )
    finally:
        # Abandon any still-running thread instead of waiting for it (the hang fix).
        pool.shutdown(wait=False, cancel_futures=True)

    successful = sum(1 for r in platform_results.values() if r.get("results"))
    logger.info(f"All searches done: {successful}/{len(platforms)} returned results")
    emit("Organizing results…", stage="search", kind="info")

    # Record each platform's outcome for this run — builds a rolling track
    # record (success rate, typical latency) that future searches use to
    # prefer sources that actually come back with usable data over ones
    # that consistently error out, time out, or get blocked. Recording
    # happens centrally here (rather than inside each search thread) so it
    # uniformly captures clean successes, thread-level exceptions, AND
    # timeouts in one place.
    for pid, result in platform_results.items():
        try:
            record_outcome(
                platform_id=pid,
                platform_name=result.get("platform_name", pid),
                success=bool(result.get("results")),
                error=result.get("error"),
                result_count=len(result.get("results") or []),
                elapsed_seconds=result.get("elapsed_seconds", 0.0),
            )
        except Exception as e:
            logger.debug(f"Reliability record skipped for {pid}: {e}")

    return {"platform_results": platform_results, "status": "aggregating"}


# ── Human-in-the-loop retry ───────────────────────────────────

def retry_platform_with_hint(platform_id: str, params: dict, intent_type: str = "general",
                             hint: str = "", headed: bool = False) -> PlatformResult:
    """Re-run ONE platform with the live browser + the user's hint (recovery flow).

    Called from the UI when a search hit a roadblock and the user offers guidance
    ("click Search", "open the Flights tab", a direct results URL). Skips Tavily
    (already known thin) and drives the browser agent first, so the user's hint
    lands where it matters. `headed=True` opens a VISIBLE Chrome window so the user
    can watch the agent follow their instruction. Returns a fresh PlatformResult.
    """
    logger.info(f"Retry with hint for {platform_id} (headed={headed}): {hint[:60]!r}")
    emit(f"Retrying {platform_id} with your hint…", stage="search", kind="start")
    _pid, result = _search_one_platform_sync(
        platform_id, params, intent_type, hint=hint, force_browser=True, headed=headed,
    )
    return result

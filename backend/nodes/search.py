"""
Search node — each platform searched in parallel.
Primary: Tavily (reliable, AI-optimised)
Secondary: Playwright Chrome (visible browser, real SERP)
Fallback: DuckDuckGo

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

from backend.llm import get_chat_client
from backend.state import AgentState, PlatformResult
from backend.memory.reliability import record_outcome
from backend.progress import emit

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

def _tavily_search(query: str, api_key: str) -> list[dict]:
    """Tavily — clean AI-ready snippets with real prices."""
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        resp = client.search(query, max_results=8, search_depth="advanced")
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
    return await scrape_platform_results(platform_name, search_url, wait_seconds=4)


async def _universal_fill(platform_name: str, entry_url: str, params: dict) -> list[dict]:
    """Universal LLM-driven form automation — works on any platform."""
    from backend.tools.universal_filler import universal_search
    return await universal_search(platform_name, entry_url, params, wait_after_submit=5)


async def _browser_use_run(platform_name: str, entry_url: str, params: dict) -> list[dict]:
    """browser-use agent — navigates + reads the results page (primary scraper)."""
    from backend.tools.browser_agent import browser_use_search
    return await browser_use_search(platform_name, entry_url, params)


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


def _is_generic_name(name: str) -> bool:
    """True if name is a placeholder like 'Option 1', 'Result 2', etc."""
    if not name:
        return True
    lower = name.lower().strip()
    generic = ("option ", "result ", "listing ", "item ", "flight ", "hotel ",
               "unnamed", "unknown", "n/a", "null")
    return any(lower.startswith(g) for g in generic)


# ── LLM parser ───────────────────────────────────────────────

PARSE_SYSTEM = """You are a strict data extraction assistant for "{platform_name}".

Extract ONLY real, verifiable listings from the search snippets below.

Return JSON: {{"results": [list of objects]}}
Fields per result: {fields} PLUS always include "price_raw" (the exact price text from the snippet).
You MAY also add up to 3 EXTRA snake_case fields for any other clearly-comparable attribute the
listing prominently shows (e.g. seat_pitch, layover, cancellation_window, check_in_time, deposit) —
ONLY when a real value is present in the snippet. Do not invent fields or values.

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
     "Economy" by default.

4. AMENITIES / FACILITIES — extract everything the listing mentions:
   — For boolean facilities (wifi, breakfast, pool, parking, free_cancellation, refundable,
     meal, emi, veg) set true ONLY if the text clearly says so, false if it says not included,
     null if not mentioned. Look for words like "Free WiFi", "Breakfast included",
     "Free cancellation", "Non-refundable", "Swimming pool", "No meal".
   — For text facilities (baggage, warranty, amenities, offers) copy the actual detail.
   — Capture rating as a number (e.g. "4.5"), and ratings out of 5 or out of 10 as-is.

5. SKIP: ads, homepage/sign-in links, category pages with no specific listing.

6. Max 5 results. Quality over quantity.

7. URL — match each listing to its real link:
   — The input has a PAGE CONTENT block (names + prices) and a LISTING LINKS block (text → url).
   — For each listing you extract from PAGE CONTENT, find the matching link in LISTING LINKS
     (match by name similarity) and use that URL.
   — If no matching link found, set url to null. Never use the homepage as a listing url.

Search context: {context}
"""


def _parse_with_groq(snippets: list[dict], platform: dict, params: dict,
                     intent_type: str = "general") -> list[dict]:
    if not snippets:
        return []

    client = get_chat_client()
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
    links_text = "\n".join(
        f"- \"{s.get('title','')}\" → {s.get('url','')}"
        for s in link_blocks[:25]
    )
    raw = f"=== PAGE CONTENT ===\n{page_text}\n\n=== LISTING LINKS ===\n{links_text or '(none)'}"

    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",   # use stronger model for accuracy
            messages=[
                {"role": "system", "content": PARSE_SYSTEM.format(
                    platform_name=platform["name"],
                    fields=", ".join(fields),
                    context=json.dumps(params),
                )},
                {"role": "user", "content": raw},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=1500,
        )
        parsed = json.loads(resp.choices[0].message.content)
        raw_results = parsed.get("results", [])

    except Exception as e:
        logger.warning(f"Groq parse error ({platform['name']}): {e}")
        return []

    # ── Post-processing: convert currency, validate, clean ──
    clean = []
    for r in raw_results:
        if not isinstance(r, dict):
            continue

        name = r.get("name") or r.get("title") or r.get("car_model") or ""
        if _is_generic_name(name):
            logger.debug(f"  Rejected generic name: '{name}'")
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
    return clean[:5]


# ── Per-platform worker (runs in thread) ─────────────────────

def _search_one_platform_sync(platform_id: str, params: dict, tavily_key: str,
                              intent_type: str = "general") -> tuple[str, PlatformResult]:
    """Synchronous per-platform search. Called from a ThreadPoolExecutor."""
    platform = _get_platform_config(platform_id)
    if not platform:
        return platform_id, PlatformResult(
            platform_id=platform_id, platform_name=platform_id, icon="❓",
            results=[], raw_snippets=[], error="Platform config not found", elapsed_seconds=0.0,
        )

    start = time.time()
    query = _build_query(platform, params)
    logger.info(f"Searching {platform['name']}: {query[:60]}")
    emit(f"Searching {platform['name']}…", stage="search", kind="start")

    from backend.tools.url_builder import build_search_url
    platform_search_url = build_search_url(platform, params)
    website = platform.get("website", "")
    results = []
    snippets = []

    # ── 1. Tavily — fast & reliable real listing data (PRIMARY) ──
    # Tavily returns clean content with real prices for most platforms in ~3s.
    if tavily_key:
        logger.info(f"  → Tavily (primary) for {platform['name']}")
        snippets = _tavily_search(query, tavily_key)
        results  = _parse_with_groq(snippets, platform, params, intent_type) if snippets else []

    # ── 2. browser-use agent — PRIMARY browser scraper for sites Tavily can't
    #       cover. An LLM drives a real browser: navigates, searches, reads the
    #       results page. Heavier on tokens (see backend/tools/browser_agent.py),
    #       so it only runs when Tavily came back thin, and is given a longer
    #       wall-clock budget than the lighter fallbacks. ──
    from backend.tools.browser_agent import is_enabled as _bu_enabled
    if len(results) < 2 and _bu_enabled():
        entry_url = platform_search_url or website
        if entry_url:
            logger.info(f"  → browser-use agent for {platform['name']}")
            emit(f"Browsing {platform['name']} live…", stage="search", kind="info")
            snippets = _playwright_run(
                _browser_use_run(platform["name"], entry_url, params), timeout=120)
            bu_results = _parse_with_groq(snippets, platform, params, intent_type) if snippets else []
            if len(bu_results) > len(results):
                results = bu_results

    # ── 2b. Universal form automation — fallback if browser-use came up empty ──
    if len(results) < 2:
        entry_url = platform_search_url or website
        if entry_url:
            logger.info(f"  → Universal browser automation for {platform['name']}")
            snippets = _playwright_run(_universal_fill(platform["name"], entry_url, params))
            auto_results = _parse_with_groq(snippets, platform, params, intent_type) if snippets else []
            if len(auto_results) > len(results):
                results = auto_results

    # ── 3. Google search via Playwright ──
    if not results:
        logger.info(f"  → Google search fallback for {platform['name']}")
        snippets = _playwright_google_search(query)
        results  = _parse_with_groq(snippets, platform, params, intent_type) if snippets else []

    # ── 4. DuckDuckGo last resort ──
    if not results:
        snippets = _ddg_search(query)
        results  = _parse_with_groq(snippets, platform, params, intent_type) if snippets else []

    elapsed = round(time.time() - start, 2)
    logger.info(f"  {platform['name']}: {len(results)} results in {elapsed}s")
    if results:
        emit(f"Found {len(results)} option{'s' if len(results) != 1 else ''} on {platform['name']}",
             stage="search", kind="ok")
    else:
        emit(f"No results on {platform['name']}", stage="search", kind="warn")

    return platform_id, PlatformResult(
        platform_id=platform_id,
        platform_name=platform["name"],
        icon=platform.get("icon", "🔍"),
        results=results,
        raw_snippets=[{"title": s.get("title",""), "snippet": s.get("snippet","")[:150]}
                      for s in snippets[:3]],
        error=None,
        elapsed_seconds=elapsed,
    )


# ── LangGraph node ────────────────────────────────────────────

def search_platforms_node(state: AgentState) -> dict:
    intent = state.get("intent")
    if not intent:
        return {"status": "error", "error": "No intent parsed"}

    platforms    = intent["platforms"]
    params       = intent["params"]
    intent_type  = intent.get("type", "general")
    tavily_key   = os.getenv("TAVILY_API_KEY", "")

    logger.info(f"Launching {len(platforms)} parallel platform searches (intent={intent_type})…")
    emit(f"Searching {len(platforms)} platforms…", stage="search", kind="start")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    platform_results = {}
    # Each platform gets its own thread — Chrome windows open simultaneously.
    # Universal form automation is slower per platform, so allow up to 150s overall.
    with ThreadPoolExecutor(max_workers=len(platforms)) as pool:
        futures = {
            pool.submit(_search_one_platform_sync, pid, params, tavily_key, intent_type): pid
            for pid in platforms
        }
        try:
            for future in as_completed(futures, timeout=150):
                try:
                    pid, result = future.result()
                    platform_results[pid] = result
                except Exception as e:
                    pid = futures[future]
                    logger.error(f"Thread error for {pid}: {e}")
                    platform_results[pid] = PlatformResult(
                        platform_id=pid, platform_name=pid, icon="❌",
                        results=[], raw_snippets=[], error=str(e), elapsed_seconds=0.0,
                    )
        except TimeoutError:
            # Mark any platforms that didn't finish in time
            for future, pid in futures.items():
                if pid not in platform_results:
                    platform_results[pid] = PlatformResult(
                        platform_id=pid, platform_name=pid, icon="⏱️",
                        results=[], raw_snippets=[], error="Timed out", elapsed_seconds=150.0,
                    )

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

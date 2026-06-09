"""
browser-use integration — an LLM-driven browser agent that actually navigates a
platform, runs the search, dismisses cookie/popups, and reads the results page.

This replaces the hand-rolled `universal_filler` as the primary browser-automation
scraper. Unlike a one-shot snippet fetch, the agent perceives the live page and
decides each action, so it copes with dynamic search forms and result pages much
better — which is the lever for fixing "0 results" on JS-heavy platforms.

Powered by Groq (your choice) via browser-use's own `ChatGroq`, with vision OFF
(Llama-3.3-70b is text-only) so it reasons over the DOM, not screenshots.

⚠️ TOKEN COST: the agent works in a loop — every step is one Groq call carrying the
page state. With several platforms in parallel this can use a lot of your daily
Groq tokens fast. Tunable via env:
    BROWSER_USE_ENABLED     (default "true")  — turn the whole thing on/off
    BROWSER_USE_MAX_STEPS   (default "6")     — hard cap on agent steps per platform
    BROWSER_USE_MODEL       (default "llama-3.3-70b-versatile")
    PLAYWRIGHT_HEADLESS     (default "true")  — headless browser

SAFETY: the task explicitly forbids logging in, creating accounts, or solving
CAPTCHAs / bot checks. If a site blocks the agent, it stops and returns nothing
(callers fall back to Google/DuckDuckGo) — we never attempt to defeat bot
detection.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    return os.getenv("BROWSER_USE_ENABLED", "true").lower() == "true"


def _max_steps() -> int:
    try:
        return max(2, int(os.getenv("BROWSER_USE_MAX_STEPS", "6")))
    except ValueError:
        return 6


def _build_task(platform_name: str, entry_url: str, params: dict) -> str:
    return (
        f"You are reading a price-comparison listing page. Open {entry_url} (the "
        f"{platform_name} website) and search for these criteria: {json.dumps(params)}.\n"
        f"Steps: dismiss any cookie or promo popup, fill the search form with the criteria, "
        f"submit it, and wait for the results/listings to load.\n"
        f"Then READ the results page and report the top 5 listings you can see — for each, "
        f"give its name, its price, and any key details shown (dates, rating, room/cabin/seat "
        f"type, amenities, times). Do NOT open individual listing detail pages; just read the "
        f"results list.\n"
        f"HARD RULES: do NOT log in, create an account, or enter any personal/payment details. "
        f"Do NOT attempt to solve any CAPTCHA or bot check. If the site blocks access or asks you "
        f"to sign in or verify you're human, STOP and report what you saw so far."
    )


async def _run_agent(platform_name: str, entry_url: str, params: dict, max_steps: int) -> str:
    from browser_use import Agent, BrowserProfile
    from browser_use.llm import ChatGroq

    llm = ChatGroq(
        model=os.getenv("BROWSER_USE_MODEL", "llama-3.3-70b-versatile"),
        api_key=os.getenv("GROQ_API_KEY", ""),
        temperature=0.0,
    )
    profile = BrowserProfile(
        headless=os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true",
    )
    agent = Agent(
        task=_build_task(platform_name, entry_url, params),
        llm=llm,
        browser_profile=profile,
        use_vision=False,        # Groq Llama is text-only → reason over DOM
        max_failures=2,
    )
    history = await agent.run(max_steps=max_steps)

    # Prefer the agent's final answer; fall back to everything it extracted en route.
    text = ""
    try:
        text = history.final_result() or ""
    except Exception:
        pass
    if not text:
        try:
            chunks = history.extracted_content() or []
            text = "\n".join(c for c in chunks if c)
        except Exception:
            pass

    # Be tidy: close the browser session if the API exposes it.
    try:
        await agent.close()
    except Exception:
        pass

    return text or ""


async def browser_use_search(platform_name: str, entry_url: str, params: dict) -> list[dict]:
    """Navigate + read a platform's results with a browser agent.

    Returns snippet dicts shaped like the other search backends ([{title, snippet,
    url}]) so the existing Groq extractor can structure them uniformly. Returns []
    on any failure so callers fall back to Google/DuckDuckGo.
    """
    if not is_enabled():
        return []
    if not entry_url:
        return []

    try:
        text = await _run_agent(platform_name, entry_url, params, _max_steps())
    except Exception as e:
        logger.warning(f"browser-use failed for {platform_name}: {e}")
        return []

    if not text or len(text.strip()) < 20:
        logger.info(f"browser-use returned no usable content for {platform_name}")
        return []

    logger.info(f"browser-use got {len(text)} chars of content for {platform_name}")
    return [{"title": f"{platform_name} results", "snippet": text, "url": entry_url}]

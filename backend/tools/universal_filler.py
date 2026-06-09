"""
Universal Playwright + LLM form automation.
Works on ANY search website — no hardcoded selectors.

Flow for every platform:
  1. Open the platform's URL (pre-filled where possible)
  2. Dismiss overlays (cookies, login prompts, ads)
  3. Read ALL interactive elements from the live page DOM
  4. Groq LLM maps intent params → fill actions (which field gets what value)
  5. Playwright executes each action (fill, click, autocomplete, submit)
  6. Wait for results page
  7. Extract listing cards + real URLs from the results page
  8. Return to Agent-Aware
"""
import asyncio
import json
import logging
import os

from backend.llm import get_chat_client

logger = logging.getLogger(__name__)

HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_ARGS = ["--no-sandbox", "--disable-blink-features=AutomationControlled", "--start-maximized"]


# ── Page reading ──────────────────────────────────────────────

_READ_DOM_JS = """
() => {
    function labelFor(el) {
        const lbl = el.id && document.querySelector(`label[for="${el.id}"]`);
        return (lbl ? lbl.innerText : '') || el.getAttribute('aria-label') || el.placeholder || '';
    }
    function visible(el) {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }
    function bestSel(el) {
        if (el.id)   return '#' + el.id;
        if (el.name) return `[name="${el.name}"]`;
        if (el.placeholder) return `input[placeholder="${el.placeholder}"]`;
        return null;
    }

    const out = { inputs: [], buttons: [] };

    document.querySelectorAll(
      'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file])'
    ).forEach(el => {
        if (!visible(el)) return;
        out.inputs.push({
            tag: 'input', type: el.type || 'text',
            id: el.id, name: el.name,
            placeholder: el.placeholder,
            label: labelFor(el),
            value: el.value,
            sel: bestSel(el),
        });
    });

    document.querySelectorAll('select').forEach(el => {
        if (!visible(el)) return;
        out.inputs.push({
            tag: 'select',
            id: el.id, name: el.name,
            label: labelFor(el),
            options: Array.from(el.options).slice(0,8).map(o=>o.text),
            sel: el.id ? '#'+el.id : (el.name ? `[name="${el.name}"]` : null),
        });
    });

    document.querySelectorAll(
      'button, [role=button], input[type=submit], input[type=button]'
    ).forEach(el => {
        if (!visible(el)) return;
        const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0,60);
        if (!t) return;
        out.buttons.push({
            text: t,
            id: el.id || '',
            sel: el.id ? '#'+el.id : `button:has-text("${t.slice(0,30)}")`,
        });
    });

    return out;
}
"""


# ── LLM action planner ────────────────────────────────────────

_PLAN_PROMPT = """You are a form automation expert for a travel/shopping search agent.

Given:
- Platform name: {platform}
- Search intent: {intent}
- Live page elements: {elements}

Output a JSON array of sequential actions to fill this form and search.

Action types:
  {{"action":"fill",              "sel":"<css>",   "val":"<text>"}}
  {{"action":"autocomplete",      "sel":"<css>",   "val":"<text>"}}   ← type + pick first suggestion
  {{"action":"click",             "sel":"<css>"}}
  {{"action":"select_option",     "sel":"<css>",   "val":"<option text>"}}
  {{"action":"submit",            "sel":"<css>"}}

Rules:
1. FIRST action: the primary location/destination/city/from field. This field ALMOST ALWAYS
   has a dropdown — you MUST use {{"action":"autocomplete"}} for it, NEVER plain "fill".
   Sites reject the search if you type a city without picking it from the dropdown.
2. Any field for city, destination, origin, "from", "to", "where", "search by city" → use "autocomplete".
3. Fill check-in / departure date field if present (use "fill" with YYYY-MM-DD).
4. Fill check-out / return date field if present.
5. LAST action must be {{"action":"submit"}} for the Search / Find / Go button.
6. Use selectors from the elements list. Prefer #id, then [name=...], then placeholder partial match.
7. Skip fields you have no value for. Keep it minimal.

Example for a hotel site:
[
  {{"action":"autocomplete","sel":"#city","val":"Manali"}},
  {{"action":"fill","sel":"[name=checkin]","val":"2026-06-07"}},
  {{"action":"fill","sel":"[name=checkout]","val":"2026-06-09"}},
  {{"action":"submit","sel":"button:has-text('Search')"}}
]

Return ONLY a valid JSON array. No explanation.
"""


def _plan_actions(dom: dict, intent_params: dict, platform_name: str) -> list[dict]:
    client = get_chat_client()
    elements_str = json.dumps({
        "inputs":  dom.get("inputs", [])[:30],
        "buttons": dom.get("buttons", [])[:15],
    })
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": _PLAN_PROMPT.format(
                    platform=platform_name,
                    intent=json.dumps(intent_params),
                    elements=elements_str,
                )
            }],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=700,
        )
        content = json.loads(resp.choices[0].message.content)
        if isinstance(content, list):
            return content
        for key in ("actions", "steps", "plan", "form_actions"):
            if key in content and isinstance(content[key], list):
                return content[key]
        return []
    except Exception as e:
        logger.warning(f"Action planner failed: {e}")
        return []


# ── Autocomplete (the hard part) ──────────────────────────────

# Keywords that indicate a location/destination field → always needs autocomplete
_LOCATION_HINTS = ("destination", "city", "location", "where", "from", "to",
                   "origin", "place", "search by", "property", "going", "leaving",
                   "pickup", "drop", "source", "station")

_SUGGESTION_SELECTORS = [
    '[role="option"]',
    '[role="listbox"] li',
    'ul[role="listbox"] li',
    '.autocomplete-item', '.autocomplete__item', '.autosuggest-item',
    '.suggestion-item', '.suggestions li', '.suggestion',
    '.react-autosuggest__suggestion',
    '[data-automation*="autocomplete" i]',
    '[class*="suggestion" i] li', '[class*="dropdown" i] li',
    '[class*="autocomplete" i] li', '[class*="result" i] li',
    'li[class*="city" i]', 'li[class*="location" i]',
]


def _looks_like_location(sel: str, val: str) -> bool:
    s = (sel or "").lower()
    return any(h in s for h in _LOCATION_HINTS)


async def _do_autocomplete(page, loc, val: str) -> bool:
    """
    Robustly fill an autocomplete field: type, wait for dropdown,
    pick the first matching suggestion. Returns True if a selection was made.
    """
    await loc.click()
    await asyncio.sleep(0.4)
    try:
        await loc.fill("")
    except Exception:
        pass
    # Type char-by-char to trigger the dropdown
    await loc.type(val, delay=110)
    await asyncio.sleep(2.0)  # give the dropdown time to populate

    # Strategy 1: click the first visible suggestion that matches the value
    first_word = val.split()[0].lower() if val else ""
    for base in _SUGGESTION_SELECTORS:
        try:
            # Prefer a suggestion that contains our text
            matching = page.locator(f'{base}:has-text("{val.split()[0]}")').first
            target = matching if await matching.count() > 0 else page.locator(base).first
            if await target.count() > 0 and await target.is_visible(timeout=800):
                await target.click()
                await asyncio.sleep(0.6)
                return True
        except Exception:
            continue

    # Strategy 2: keyboard navigation (works on most widgets)
    try:
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.4)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.6)
        return True
    except Exception:
        return False


async def _click_search_button(page, sel: str = "") -> bool:
    """Find and click the search/submit button."""
    if sel:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=2000):
                await loc.click()
                return True
        except Exception:
            pass
    for btn in [
        'button:has-text("Search")', 'button:has-text("SEARCH")',
        'button:has-text("Find")',   'button:has-text("GO")',
        'button:has-text("Explore")','a:has-text("Search")',
        'button[type="submit"]',     'input[type="submit"]',
        '[data-element-name*="search" i]', '[class*="search-btn" i]',
        '[class*="SearchButton" i]', '#search-button',
    ]:
        try:
            b = page.locator(btn).first
            if await b.count() > 0 and await b.is_visible(timeout=800):
                await b.click()
                return True
        except Exception:
            continue
    # Last resort: press Enter
    try:
        await page.keyboard.press("Enter")
        return True
    except Exception:
        return False


# ── Action executor ───────────────────────────────────────────

async def _execute(page, actions: list[dict]) -> None:
    for act in actions:
        kind = act.get("action", "")
        sel  = act.get("sel") or act.get("selector", "")
        val  = str(act.get("val") or act.get("value", ""))

        try:
            # Auto-upgrade: a "fill" on a location field MUST use autocomplete,
            # otherwise the site rejects the search ("select from list").
            if kind == "fill" and _looks_like_location(sel, val):
                kind = "autocomplete"

            if kind == "fill" and sel:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible(timeout=3000):
                    await loc.click()
                    await asyncio.sleep(0.3)
                    await loc.fill(val)

            elif kind == "autocomplete" and sel:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible(timeout=3000):
                    ok = await _do_autocomplete(page, loc, val)
                    logger.debug(f"  autocomplete {sel} = '{val}' → {'picked' if ok else 'no pick'}")

            elif kind == "click" and sel:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible(timeout=3000):
                    await loc.click()

            elif kind == "select_option" and sel:
                await page.locator(sel).first.select_option(label=val)

            elif kind == "submit":
                await _click_search_button(page, sel)

            await asyncio.sleep(0.5)

        except Exception as e:
            logger.debug(f"Action {kind} ({sel}) failed: {e}")
            continue


# ── Overlay dismissal ─────────────────────────────────────────

async def _dismiss_overlays(page) -> None:
    for text in ["Accept all", "Accept", "I agree", "Close", "No thanks",
                 "Maybe later", "Continue without accepting", "Dismiss", "Got it"]:
        try:
            btn = page.locator(
                f'button:has-text("{text}"), [role="button"]:has-text("{text}")'
            ).first
            if await btn.is_visible(timeout=500):
                await btn.click()
                await asyncio.sleep(0.5)
                break
        except Exception:
            pass


# ── Results extractor ─────────────────────────────────────────

_EXTRACT_LINKS_JS = """
() => {
    const seen = new Set(), out = [];
    document.querySelectorAll('a[href]').forEach(a => {
        const h = a.href, t = (a.innerText || a.title || '').trim();
        if (!h || !h.startsWith('http') || seen.has(h)) return;
        // Skip nav, footer, social media, google links
        if (/google|facebook|twitter|instagram|linkedin|mailto|javascript/i.test(h)) return;
        if (t.length < 2 || t.length > 120) return;
        seen.add(h);
        out.push({ title: t, url: h });
    });
    return out.slice(0, 40);
}
"""


async def _extract_page_data(page) -> list[dict]:
    page_text = await page.evaluate("() => document.body.innerText")
    links     = await page.evaluate(_EXTRACT_LINKS_JS)
    results   = [{"title": "Page content", "snippet": page_text[:5000], "url": page.url}]
    for lnk in links[:20]:
        results.append({"title": lnk["title"], "snippet": "", "url": lnk["url"]})
    return results


# ── Results-vs-form detection ─────────────────────────────────

_DETECT_RESULTS_JS = """
() => {
    const text = document.body.innerText;
    // Count price-like patterns (₹1,234 / Rs 999 / $45 / €80)
    const priceMatches = (text.match(/(?:₹|Rs\\.?|\\$|€|£)\\s?[\\d,]{2,}/g) || []).length;
    // Count listing-card-like elements
    const cards = document.querySelectorAll(
        '[class*="card" i], [class*="listing" i], [class*="result" i], [class*="property" i], [data-testid*="card" i]'
    ).length;
    return { priceCount: priceMatches, cardCount: cards };
}
"""


async def _page_already_has_results(page) -> bool:
    """True if the page looks like a results page (many prices + cards)."""
    try:
        stats = await page.evaluate(_DETECT_RESULTS_JS)
        # Heuristic: a real results page has several prices AND listing cards
        return stats.get("priceCount", 0) >= 3 and stats.get("cardCount", 0) >= 3
    except Exception:
        return False


# ── Main entry point ──────────────────────────────────────────

async def universal_search(
    platform_name: str,
    platform_url: str,
    intent_params: dict,
    wait_after_submit: int = 5,
) -> list[dict]:
    """
    Open any platform, fill its form using LLM-planned actions, submit,
    and return [{title, snippet, url}] from the results page.
    """
    from playwright.async_api import async_playwright

    logger.info(f"Universal filler → {platform_name}: {platform_url[:70]}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=HEADLESS, args=_ARGS)
            ctx = await browser.new_context(
                user_agent=_UA,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = await ctx.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )

            # 1. Open the URL
            await page.goto(platform_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # 2. Dismiss overlays
            await _dismiss_overlays(page)
            await asyncio.sleep(0.5)

            # 3. Already a results page? (pre-filled URL landed on results) → just scrape
            if await _page_already_has_results(page):
                logger.info(f"  {platform_name}: page already shows results — scraping directly")
                await asyncio.sleep(2)
                results = await _extract_page_data(page)
                await browser.close()
                return results

            # 4. It's a search form — read live DOM
            dom = await page.evaluate(_READ_DOM_JS)
            logger.debug(f"  DOM: {len(dom.get('inputs',[]))} inputs, {len(dom.get('buttons',[]))} buttons")
            if not dom.get("inputs"):
                await asyncio.sleep(3)
                dom = await page.evaluate(_READ_DOM_JS)

            # 5. LLM plans form-fill actions
            actions = _plan_actions(dom, intent_params, platform_name)
            logger.info(f"  Planned {len(actions)} actions for {platform_name}")

            # 6. Execute the form fill + submit
            await _execute(page, actions)

            # 7. Wait for results to load
            await asyncio.sleep(wait_after_submit)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(2)

            # 8. Extract results from the page we landed on
            results = await _extract_page_data(page)
            logger.info(f"  Extracted {len(results)} items from {platform_name} results page")

            await browser.close()
            return results

    except Exception as e:
        logger.error(f"Universal filler failed ({platform_name}): {e}")
        return []

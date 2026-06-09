"""
Smart Playwright form filler.
Navigates to each platform, auto-fills search details, submits, and returns
the results page URL + scraped listing content.
"""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


async def _get_page_listings(page, url: str) -> list[dict]:
    """Extract listing cards + real URLs from the current results page."""
    page_text = await page.evaluate("() => document.body.innerText")
    links = await page.evaluate("""
        () => {
            const seen = new Set(), out = [];
            document.querySelectorAll('a[href]').forEach(a => {
                const h = a.href, t = a.innerText.trim();
                if (!h || !h.startsWith('http') || seen.has(h) || t.length < 3) return;
                if (h.includes('google') || h.includes('javascript')) return;
                seen.add(h);
                out.push({ title: t.slice(0, 100), url: h });
            });
            return out.slice(0, 25);
        }
    """)
    results = [{"title": "Page content", "snippet": page_text[:5000], "url": url}]
    for lnk in links:
        results.append({"title": lnk["title"], "snippet": "", "url": lnk["url"]})
    return results


async def _safe_fill(page, selectors: list[str], value: str) -> bool:
    """Try a list of CSS/text selectors until one accepts the input."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click()
                await asyncio.sleep(0.4)
                await loc.fill("")
                await loc.type(value, delay=60)
                return True
        except Exception:
            continue
    return False


async def _pick_autocomplete(page, value: str) -> bool:
    """Wait for autocomplete dropdown and pick the first matching item."""
    await asyncio.sleep(1.2)
    selectors = [
        f'[role="option"]:has-text("{value.split()[0]}")',
        '[role="option"]:first-child',
        '[role="listbox"] li:first-child',
        '.autocomplete-item:first-child',
        '.tt-suggestion:first-child',
        '[data-automation="autocomplete-item"]:first-child',
        'ul[role="listbox"] li:first-child',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click()
                await asyncio.sleep(0.5)
                return True
        except Exception:
            continue
    # Press Enter as last resort
    await page.keyboard.press("ArrowDown")
    await asyncio.sleep(0.3)
    await page.keyboard.press("Enter")
    return False


async def _click_search(page) -> bool:
    """Find and click the primary search/submit button."""
    selectors = [
        'button:has-text("Search")',
        'button:has-text("SEARCH")',
        'button[type="submit"]',
        '[data-element-name="search-button"]',
        '[data-testid="searchbox-datepicker-footer-CTA"]',
        '.search-btn', '.SearchButton', '#search-button',
        'input[type="submit"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click()
                return True
        except Exception:
            continue
    return False


# ── Platform-specific automations ────────────────────────────

async def agoda_search(params: dict) -> list[dict]:
    from playwright.async_api import async_playwright
    location  = params.get("location", "")
    check_in  = params.get("check_in", "")
    check_out = params.get("check_out", "")
    if not location:
        return []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx  = await browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        url = f"https://www.agoda.com/search?checkIn={check_in}&checkOut={check_out}&adults=2"
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(2)

        # Fill destination
        filled = await _safe_fill(page, [
            'input[placeholder*="destination" i]',
            'input[placeholder*="city" i]',
            'input[placeholder*="Enter" i]',
            'input[placeholder*="hotel" i]',
            '[data-element-name="search-box-destination"] input',
            '#searchbox-text-editor-desktop input',
        ], location)
        if filled:
            await _pick_autocomplete(page, location)

        await asyncio.sleep(0.5)
        await _click_search(page)
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        await asyncio.sleep(4)

        results_url = page.url
        data = await _get_page_listings(page, results_url)
        await browser.close()
        logger.info(f"Agoda automation → {results_url[:80]}")
        return data


async def booking_com_search(params: dict) -> list[dict]:
    from playwright.async_api import async_playwright
    location  = params.get("location", "")
    check_in  = params.get("check_in", "")
    check_out = params.get("check_out", "")
    if not location:
        return []
    search_url = (f"https://www.booking.com/searchresults.html"
                  f"?ss={location}&checkin={check_in}&checkout={check_out}"
                  f"&group_adults=2&no_rooms=1&selected_currency=INR&lang=en-gb")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx  = await browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(4)
        data = await _get_page_listings(page, page.url)
        await browser.close()
        logger.info(f"Booking.com → {page.url[:80]}")
        return data


async def makemytrip_hotel_search(params: dict) -> list[dict]:
    from playwright.async_api import async_playwright
    location  = params.get("location", "")
    check_in  = params.get("check_in", "")
    check_out = params.get("check_out", "")
    if not location:
        return []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx  = await browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        await page.goto("https://www.makemytrip.com/hotels/", wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(2)

        # Fill city
        await _safe_fill(page, [
            '#city', 'input[placeholder*="city" i]',
            'input[placeholder*="Where" i]',
            'input[placeholder*="hotel" i]',
        ], location)
        await _pick_autocomplete(page, location)
        await asyncio.sleep(0.5)
        await _click_search(page)
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        await asyncio.sleep(4)

        results_url = page.url
        data = await _get_page_listings(page, results_url)
        await browser.close()
        return data


async def airbnb_search(params: dict) -> list[dict]:
    from playwright.async_api import async_playwright
    location  = params.get("location", "")
    check_in  = params.get("check_in", "")
    check_out = params.get("check_out", "")
    if not location:
        return []
    search_url = (f"https://www.airbnb.in/s/{location}/homes"
                  f"?checkin={check_in}&checkout={check_out}&adults=2")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx  = await browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)
        data = await _get_page_listings(page, page.url)
        await browser.close()
        return data


async def oyo_search(params: dict) -> list[dict]:
    from playwright.async_api import async_playwright
    location  = params.get("location", "")
    check_in  = params.get("check_in", "")
    check_out = params.get("check_out", "")
    if not location:
        return []
    search_url = (f"https://www.oyorooms.com/search/"
                  f"?location={location}&checkinDate={check_in}&checkoutDate={check_out}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx  = await browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(4)
        data = await _get_page_listings(page, page.url)
        await browser.close()
        return data


async def zomato_search(params: dict) -> list[dict]:
    from playwright.async_api import async_playwright
    location = params.get("location", "")
    cuisine  = params.get("cuisine", "")
    if not location:
        return []
    slug   = cuisine.lower().replace(" ","-") if cuisine else "restaurants"
    url    = f"https://www.zomato.com/{location.lower().replace(' ','-')}/{slug}"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx  = await browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(3)
        data = await _get_page_listings(page, page.url)
        await browser.close()
        return data


# ── Registry — map platform_id → automation function ─────────
PLATFORM_AUTOMATIONS = {
    "agoda":             agoda_search,
    "booking_com":       booking_com_search,
    "makemytrip_hotels": makemytrip_hotel_search,
    "airbnb":            airbnb_search,
    "oyo":               oyo_search,
    "zomato":            zomato_search,
}

"""
Playwright browser tool.
- google_search(): visible Chrome → Google SERP → real listing URLs (unwrapped)
- scrape_platform_results(): opens platform's search page directly → real listing URLs
"""
import asyncio
import logging
import os
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"

_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--start-maximized",
    "--disable-dev-shm-usage",
]

# Force the REAL Chrome binary (never Playwright's bundled Chrome-for-Testing, which
# Google flags + can't sign in). channel="chrome" makes Playwright use installed Chrome.
_CHANNEL = (os.getenv("BROWSER_USE_CHANNEL", "chrome").strip() or "chrome")
# Strip Chrome's "controlled by automated software" switch — the single strongest
# bot-detection signal. Removing --enable-automation makes navigator.webdriver false
# and drops the automation infobar, so the browser looks like a normal user session.
_IGNORE_AUTOMATION = ["--enable-automation"]
# Injected into every page so navigator.webdriver is undefined (belt-and-suspenders).
_STEALTH_JS = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "window.chrome={runtime:{}};"
    "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});"
    "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _cdp_reachable(cdp_url: str) -> bool:
    try:
        import httpx
        return httpx.get(f"{cdp_url.rstrip('/')}/json/version", timeout=1.5).status_code == 200
    except Exception:
        return False


async def _new_page(p, headless: bool = True):
    """Return (page, close_fn). Uses YOUR logged-in Chrome via CDP when it's running
    (so sites see a real, signed-in user — no 'access denied'); otherwise launches a
    fresh browser. With CDP we close only the TAB, never your Chrome."""
    # 1) Attach to YOUR real, signed-in Chrome if it's running (launch_my_browser.bat).
    #    This is the preferred path — real Chrome, your logins, no "Chrome for Testing".
    cdp = os.getenv("BROWSER_USE_CDP_URL", "").strip()
    if cdp and _cdp_reachable(cdp):
        try:
            browser = await p.chromium.connect_over_cdp(cdp)
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await ctx.new_page()

            async def _close():
                try:
                    await page.close()       # close the tab only — leave your Chrome open
                except Exception:
                    pass
            return page, _close
        except Exception as e:
            logger.warning(f"CDP connect failed ({cdp}); trying persistent profile: {e}")

    # 2) Otherwise launch real Chrome with the dedicated persistent profile (keeps your
    #    logins across runs). channel='chrome' forces the real binary, not the bundled one.
    # The persistent profile is opt-in: parallel platform scrapes would all try to lock
    # the SAME user_data_dir → "profile in use" → fallback. Default = fresh window below
    # (real Chrome via channel, no shared lock). Enable with BROWSER_USE_PERSIST_PROFILE.
    _persist = os.getenv("BROWSER_USE_PERSIST_PROFILE", "").lower() in ("1", "true", "yes")
    udd = os.getenv("BROWSER_USE_USER_DATA_DIR", "").strip()
    channel = _CHANNEL
    if _persist and udd:
        try:
            ctx = await p.chromium.launch_persistent_context(
                udd, headless=headless, args=_BROWSER_ARGS, channel=channel,
                ignore_default_args=_IGNORE_AUTOMATION,
                user_agent=_USER_AGENT, viewport={"width": 1280, "height": 900})
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.add_init_script(_STEALTH_JS)

            async def _close():
                try:
                    await ctx.close()
                except Exception:
                    pass
            return page, _close
        except Exception as e:
            logger.warning(f"persistent profile launch failed ({udd}); fresh browser: {e}")

    # 3) Last resort — a fresh real-Chrome window (channel=chrome, never bundled Testing).
    browser = await p.chromium.launch(headless=headless, args=_BROWSER_ARGS, channel=channel,
                                      ignore_default_args=_IGNORE_AUTOMATION)
    ctx = await browser.new_context(user_agent=_USER_AGENT,
                                    viewport={"width": 1280, "height": 900})
    page = await ctx.new_page()
    await page.add_init_script(_STEALTH_JS)

    async def _close():
        try:
            await browser.close()
        except Exception:
            pass
    return page, _close

# JS that unwraps Google redirect URLs and skips Google-internal links
_EXTRACT_RESULTS_JS = """
() => {
    function cleanUrl(href) {
        if (!href) return '';
        try {
            // Unwrap /url?q=https://actual-site.com/...
            if (href.includes('/url?q=')) {
                const q = new URL(href).searchParams.get('q');
                if (q && q.startsWith('http')) return q;
            }
        } catch(e) {}
        // Drop Google-internal links
        if (href.includes('google.com') || href.includes('google.co.')) return '';
        return href.startsWith('http') ? href : '';
    }

    const out = [];
    document.querySelectorAll('div.g, div[data-hveid], div[data-sokoban-container]').forEach(el => {
        const h3   = el.querySelector('h3');
        const snip = el.querySelector('.VwiC3b, .yXK7lf, [data-sncf="1"], span[data-ved]');
        const a    = el.querySelector('a[href]');
        if (!h3 || !h3.innerText.trim()) return;
        const url = cleanUrl(a ? a.href : '');
        out.push({
            title:   h3.innerText.trim(),
            snippet: snip ? snip.innerText.trim() : '',
            url:     url
        });
    });
    return out.slice(0, 10);
}
"""


async def google_search(query: str, num_results: int = 8) -> list[dict]:
    """
    Open Chrome, search Google, return [{title, snippet, url}] with
    real platform URLs (Google redirect links are unwrapped).
    """
    from playwright.async_api import async_playwright

    search_url = f"https://www.google.com/search?q={quote_plus(query)}&num={num_results}&hl=en"
    logger.info(f"Chrome → Google: {query[:70]}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=HEADLESS, args=_BROWSER_ARGS,
                                               channel=_CHANNEL, ignore_default_args=_IGNORE_AUTOMATION)
            ctx = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = await ctx.new_page()
            await page.add_init_script(_STEALTH_JS)
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

            # Dismiss consent if shown
            try:
                btn = page.locator("button:has-text('Accept all'), button:has-text('I agree'), #L2AGLb")
                if await btn.count() > 0:
                    await btn.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            await asyncio.sleep(2)

            results = await page.evaluate(_EXTRACT_RESULTS_JS)

            # Fallback: full page text
            if not results:
                body = await page.evaluate("() => document.body.innerText")
                results = [{"title": query, "snippet": body[:3000], "url": ""}]

            await browser.close()
            logger.info(f"Chrome → {len(results)} results for: {query[:50]}")
            return results

    except Exception as e:
        logger.error(f"Playwright google_search failed ({query[:50]}): {e}")
        return []


async def scrape_platform_results(
    platform_name: str,
    search_url: str,
    wait_seconds: int = 4,
    headless: bool = True,
) -> list[dict]:
    """
    Open the platform's pre-filled search results URL directly in Chrome,
    extract listing cards (name, price, direct URL).
    Returns raw text if structured extraction fails.

    DETERMINISTIC — no LLM in the loop, so this is ~5s vs the LLM browser agent's
    ~60-150s. Runs headless by default (it's a backend fast tier, not user-facing).
    """
    from playwright.async_api import async_playwright

    logger.info(f"Chrome → {platform_name} results page: {search_url[:80]}")
    try:
        async with async_playwright() as p:
            page, _close = await _new_page(p, headless=headless)
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(wait_seconds)  # let results render

            # Get all visible text + all <a href> that look like listing pages
            data = await page.evaluate("""
                () => {
                    const text = document.body.innerText.slice(0, 6000);
                    const links = [];
                    document.querySelectorAll('a[href]').forEach(a => {
                        const h = a.href;
                        // Only keep links to specific listing pages (not nav/footer)
                        if (h && h.startsWith('http') && !h.includes('google') &&
                            a.innerText && a.innerText.trim().length > 3) {
                            links.push({ text: a.innerText.trim().slice(0, 80), url: h });
                        }
                    });
                    // Deduplicate URLs
                    const seen = new Set();
                    const deduped = links.filter(l => {
                        if (seen.has(l.url)) return false;
                        seen.add(l.url); return true;
                    });
                    return { page_text: text, links: deduped.slice(0, 30) };
                }
            """)
            await _close()

            page_text = data.get("page_text", "")
            links     = data.get("links", [])

            # Return as snippets the LLM can parse
            results = [{"title": platform_name, "snippet": page_text, "url": search_url}]
            for lnk in links[:10]:
                results.append({"title": lnk["text"], "snippet": "", "url": lnk["url"]})

            return results

    except Exception as e:
        logger.error(f"scrape_platform_results failed ({platform_name}): {e}")
        return []


async def screenshot_page(url: str, wait_seconds: int = 5, headless: bool = True) -> str:
    """Navigate to a URL and return a base64 PNG of the rendered page — used to SHOW
    the user the website before they tell the agent what to do. Deterministic + fast
    (no LLM); just opens the page, lets it render, and snaps it."""
    import base64
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as p:
            page, _close = await _new_page(p, headless=headless)
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(wait_seconds)
            png = await page.screenshot(full_page=False)
            await _close()
            return base64.b64encode(png).decode()
    except Exception as e:
        logger.warning(f"screenshot_page failed ({url[:50]}): {e}")
        return ""


async def fetch_page_text(url: str, timeout: int = 20000) -> str:
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=HEADLESS, args=_BROWSER_ARGS,
                                               channel=_CHANNEL, ignore_default_args=_IGNORE_AUTOMATION)
            ctx = await browser.new_context(user_agent=_USER_AGENT)
            page = await ctx.new_page()
            await page.add_init_script(_STEALTH_JS)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            await asyncio.sleep(2)
            text = await page.evaluate("() => document.body.innerText")
            await browser.close()
            return text[:6000]
    except Exception as e:
        logger.warning(f"fetch_page_text failed ({url}): {e}")
        return ""

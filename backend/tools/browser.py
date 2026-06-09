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
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

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
            browser = await p.chromium.launch(headless=HEADLESS, args=_BROWSER_ARGS)
            ctx = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = await ctx.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
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
) -> list[dict]:
    """
    Open the platform's pre-filled search results URL directly in Chrome,
    extract listing cards (name, price, direct URL).
    Returns raw text if structured extraction fails.
    """
    from playwright.async_api import async_playwright

    logger.info(f"Chrome → {platform_name} results page: {search_url[:80]}")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=HEADLESS, args=_BROWSER_ARGS)
            ctx = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 900},
            )
            page = await ctx.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
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
            await browser.close()

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


async def fetch_page_text(url: str, timeout: int = 20000) -> str:
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=HEADLESS, args=_BROWSER_ARGS)
            ctx = await browser.new_context(user_agent=_USER_AGENT)
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            await asyncio.sleep(2)
            text = await page.evaluate("() => document.body.innerText")
            await browser.close()
            return text[:6000]
    except Exception as e:
        logger.warning(f"fetch_page_text failed ({url}): {e}")
        return ""

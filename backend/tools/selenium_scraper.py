"""Selenium + BeautifulSoup search tier — a fast, deterministic alternative to the
LLM-driven browser-use tier (backend/tools/browser_agent.py).

Renders a platform's search-results page ONCE with real Chrome (via Selenium — plain
Selenium by default), then hands that single render's HTML to BeautifulSoup, which
parses hundreds of elements in milliseconds — far faster than either the LLM-driven
browser-use loop (30-160s, one LLM call per step) or Selenium's own per-element DOM
queries. Runs BEFORE browser-use in the tier cascade (backend/nodes/search.py); a hit
here means browser-use never runs for that platform at all.

Plain Selenium is the default (SELENIUM_UNDETECTED=false) rather than
undetected-chromedriver: measured on this project, uc's own binary-patching adds ~12s
of PURE LAUNCH overhead per call (~13.5s vs ~1.3s), which directly fights this tier's
"fail fast on a bad platform" design — a platform this tier correctly rejects would
still cost ~15-20s with uc vs ~2-8s with plain Selenium. A bot-walled platform simply
falls through to browser-use either way (the cascade's existing safety net), so the
extra stealth mainly trades speed for a marginally lower chance of that fallthrough —
not worth the tax as the default. Set SELENIUM_UNDETECTED=true to opt back in.

Mirrors backend/tools/browser.py's scrape_platform_results() return shape exactly
([{title, snippet, url}, ...]) so it drops into the existing _parse_with_groq LLM
structuring step with zero special-casing downstream — this tier still pays for one
parse LLM call on a hit, same as every other tier, but the input handed to it is denser
and pre-filtered to price-bearing content.

Runs with its OWN dedicated Chrome profile (agent-aware-selenium), separate from
Playwright's persistent profile (agent-aware-chrome) and browser-use's fresh temp
profiles — reusing either would risk the exact profile-lock collision this app already
fought through once (see ARCHITECTURE.md / DESIGN.md).

Fails open, always: any Selenium/BS4 exception, timeout, or thin/junk page returns []
so the cascade falls through to browser-use exactly as it would on an empty Tavily
result — this tier's failure must never break the search.
"""
import logging
import os
import re
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

_PRICE_RE = re.compile(r"(?:₹|Rs\.?|\$|€|£)\s?[\d,]{2,}(?:\.\d+)?")

# A real listing page's text is dominated by product/price content; an error or
# bot-wall page's text is dominated by ONE of these phrases instead. Checked against
# the first ~400 chars only — a genuine listing page could mention "unavailable" in
# passing (e.g. "out of stock") without that being the page's actual subject.
_ERROR_PAGE_RE = re.compile(
    r"\b(503 service unavailable|service unavailable|access denied|forbidden|"
    r"are you a human|are you a robot|unusual traffic|verify you.?re human|"
    r"captcha|429 too many requests|temporarily unavailable|rate limit(ed)?)\b",
    re.IGNORECASE,
)


def _looks_like_error_page(text: str) -> bool:
    return bool(_ERROR_PAGE_RE.search((text or "")[:400]))


def is_enabled() -> bool:
    return os.getenv("SELENIUM_ENABLED", "true").lower() in ("1", "true", "yes")


def _chrome_profile_dir() -> str:
    custom = os.getenv("SELENIUM_USER_DATA_DIR", "").strip()
    if custom:
        return custom
    # A DEDICATED profile, distinct from Playwright's agent-aware-chrome and
    # browser-use's temp profiles — never share a profile dir across automation
    # libraries; that's the exact bug class this app already fought through once.
    return os.path.join(os.path.expanduser("~"), "agent-aware-selenium")


_chrome_version_cache: int | None = -1  # -1 = not yet checked, None = checked & unknown


def _detected_chrome_major_version(chrome_binary: str) -> int | None:
    """The installed Chrome's major version, via Windows file metadata (avoids
    shelling out to `chrome.exe --version`, which on some Windows setups reuses an
    existing browser window instead of printing to stdout). Cached — this is called
    on every driver launch. Returns None if detection fails; undetected-chromedriver
    falls back to its own auto-detection in that case."""
    global _chrome_version_cache
    if _chrome_version_cache != -1:
        return _chrome_version_cache
    try:
        import subprocess
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Item '{chrome_binary}').VersionInfo.ProductVersion"],
            stderr=subprocess.DEVNULL, timeout=5, text=True)
        m = re.match(r"(\d+)\.", out.strip())
        _chrome_version_cache = int(m.group(1)) if m else None
    except Exception:
        _chrome_version_cache = None
    return _chrome_version_cache


def _build_driver(headless: bool):
    """Launch real Chrome via undetected-chromedriver (preferred — patches away
    automation signals at the binary level) with a plain-Selenium fallback if the
    optional dependency is missing or fails to launch. Both paths force the REAL
    installed Chrome binary (never a bundled test browser), reusing the same
    Chrome-finder already proven for the Playwright/browser-use path."""
    from backend.tools.browser_agent import _chrome_exe
    chrome_binary = _chrome_exe()
    user_data_dir = _chrome_profile_dir()
    os.makedirs(user_data_dir, exist_ok=True)
    # Default OFF — see module docstring for the measured ~12s launch-time cost of
    # undetected-chromedriver that motivated this. Opt in via SELENIUM_UNDETECTED=true.
    use_undetected = os.getenv("SELENIUM_UNDETECTED", "false").lower() in ("1", "true", "yes")

    if use_undetected:
        try:
            import undetected_chromedriver as uc
            options = uc.ChromeOptions()
            if chrome_binary and os.path.exists(chrome_binary):
                options.binary_location = chrome_binary
            options.user_data_dir = user_data_dir
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            # Pin the driver to the ACTUAL installed Chrome's major version — without
            # this, uc can resolve a mismatched chromedriver and spend a long time
            # retrying/negotiating before failing over, defeating this tier's whole
            # "fail fast" design.
            version_main = _detected_chrome_major_version(chrome_binary)
            return uc.Chrome(options=options, headless=headless, use_subprocess=True,
                             version_main=version_main)
        except Exception as e:
            logger.warning(f"undetected-chromedriver unavailable ({e}); "
                           f"falling back to plain Selenium")

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    if chrome_binary and os.path.exists(chrome_binary):
        options.binary_location = chrome_binary
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    if headless:
        options.add_argument("--headless=new")
    # Strip the "Chrome is being controlled by automated test software" flag — the
    # same principle as the Playwright path's ignore_default_args=["--enable-automation"].
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=options)


def _page_ready(driver) -> bool:
    """WebDriverWait condition: page has finished loading AND has SOME visible text.
    No platform has per-platform selectors configured (confirmed: zero across
    platforms.yaml), so this is a deliberately generic, platform-agnostic condition —
    not "did the results grid render" (unknowable without per-site config) but
    "did the page render at all"."""
    try:
        ready = driver.execute_script("return document.readyState") == "complete"
        text_len = driver.execute_script(
            "return document.body ? document.body.innerText.length : 0")
        return bool(ready) and text_len > 500
    except Exception:
        return False


def _extract_price_blocks(soup, base_url: str, cap: int = 15) -> list[dict]:
    """Find elements that look like listing cards: a price-shaped text node, walked up
    to the nearest ancestor that also contains a link. Denser and more pre-structured
    than a flat page-text dump — makes the downstream LLM parse call cheaper and more
    accurate on platforms whose markup fits this pattern."""
    blocks = []
    seen_hrefs = set()
    for text_node in soup.find_all(string=_PRICE_RE):
        el = text_node.parent
        href = None
        hops = 0
        while el is not None and hops < 5:
            a_tag = el.find("a", href=True) if hasattr(el, "find") else None
            if a_tag:
                href = a_tag["href"]
                break
            el = el.parent
            hops += 1
        if not href:
            continue
        href = urljoin(base_url, href)
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        block_text = el.get_text(separator=" ", strip=True)[:300] if el else str(text_node)[:300]
        blocks.append({"title": block_text, "snippet": "", "url": href})
        if len(blocks) >= cap:
            break
    return blocks


def _flat_fallback(soup, platform_name: str, search_url: str) -> tuple[list[dict], str]:
    """Same shape as browser.py's scrape_platform_results(): one page-text blob +
    up to 10 link entries. Used when the page doesn't have ≥3 recognizable price
    blocks — keeps behavior safe on markup the price-block heuristic doesn't fit."""
    text = soup.get_text(separator=" ", strip=True)[:6000]
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(search_url, a["href"])
        label = a.get_text(strip=True)
        if not label or len(label) <= 3 or "google" in href or href in seen:
            continue
        seen.add(href)
        links.append({"title": label[:80], "url": href})
        if len(links) >= 30:
            break
    results = [{"title": platform_name, "snippet": text, "url": search_url}]
    for lnk in links[:10]:
        results.append({"title": lnk["title"], "snippet": "", "url": lnk["url"]})
    return results, text


def scrape_platform_selenium(platform_name: str, search_url: str,
                              timeout_seconds: float = 6.0) -> list[dict]:
    """Open the platform's pre-filled search-results URL in real Chrome via Selenium,
    parse it with BeautifulSoup, return [{title, snippet, url}, ...] for _parse_with_groq.

    Deliberately short-timeout and fail-fast: this tier runs for EVERY platform (no
    per-platform opt-in), including heavy-JS async sites known to render junk from this
    style of scrape (flight OTAs etc.) — a thin/junk page returns [] within a few
    seconds rather than the "junk AND slow" failure the deep-link scrape tier's own
    comments warn about, so the cascade escalates to browser-use quickly and cheaply.
    """
    if not is_enabled():
        return []

    from bs4 import BeautifulSoup
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.support.ui import WebDriverWait

    headless = os.getenv("SELENIUM_HEADLESS", "true").lower() in ("1", "true", "yes")
    driver = None
    try:
        try:
            driver = _build_driver(headless=headless)
        except Exception as e:
            logger.warning(f"Selenium driver launch failed for {platform_name}: {e}")
            return []

        driver.set_page_load_timeout(timeout_seconds + 5)
        try:
            driver.get(search_url)
        except TimeoutException:
            pass  # partial load is fine — still try to read what's there

        try:
            WebDriverWait(driver, timeout_seconds).until(_page_ready)
        except TimeoutException:
            logger.debug(f"Selenium: {platform_name} page didn't settle in "
                        f"{timeout_seconds}s; parsing anyway")

        soup = BeautifulSoup(driver.page_source, "lxml")

        price_blocks = _extract_price_blocks(soup, search_url)
        if len(price_blocks) >= 3:
            logger.info(f"  Selenium+BS4: {platform_name} → "
                       f"{len(price_blocks)} price-bearing blocks")
            return price_blocks

        fallback_results, flat_text = _flat_fallback(soup, platform_name, search_url)
        if len(flat_text) < 200 or _looks_like_error_page(flat_text):
            logger.info(f"  Selenium+BS4: {platform_name} page too thin or error-like "
                       f"({len(flat_text)} chars, {len(price_blocks)} price blocks) — "
                       f"treating as a miss")
            return []
        logger.info(f"  Selenium+BS4: {platform_name} → flat-page fallback "
                   f"({len(flat_text)} chars)")
        return fallback_results

    except WebDriverException as e:
        logger.warning(f"Selenium scrape failed for {platform_name}: {e}")
        return []
    except Exception as e:
        logger.warning(f"Selenium+BS4 scrape error for {platform_name}: {e}")
        return []
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

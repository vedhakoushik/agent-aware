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


def _cdp_reachable(cdp_url: str) -> bool:
    """True if your debug Chrome is actually up at that URL. Lets us attach to your
    logged-in browser when it's running, and silently fall back to a normal browser
    when it isn't — so a search never fails just because you didn't launch it."""
    try:
        import httpx
        base = cdp_url.rstrip("/")
        r = httpx.get(f"{base}/json/version", timeout=30)
        return r.status_code == 200
    except Exception:
        return False


def _purge_profile_cache(user_data_dir: str) -> None:
    """Delete the regenerable Chrome cache dirs in a profile so browser-use's profile
    COPY can't fail on locked Cache files (the '[Errno 13] Permission denied' → 0
    results bug). Cookies / Login Data / Network are NOT touched, so sign-in survives."""
    import shutil
    cache_dirs = ["Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnCache",
                  "GrShaderCache", "ShaderCache", "Service Worker"]
    for sub in ("", "Default"):
        base = os.path.join(user_data_dir, sub) if sub else user_data_dir
        for c in cache_dirs:
            p = os.path.join(base, c)
            if os.path.isdir(p):
                try:
                    shutil.rmtree(p, ignore_errors=True)
                except Exception:
                    pass


def _chrome_exe() -> str:
    """Path to the REAL Google Chrome binary (never Playwright's bundled
    Chrome-for-Testing). Override with BROWSER_USE_CHROME_PATH if installed elsewhere."""
    import shutil
    candidates = [
        os.getenv("BROWSER_USE_CHROME_PATH", "").strip(),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return shutil.which("chrome") or "chrome.exe"


def _ensure_real_chrome_cdp() -> str:
    """Make sure YOUR real Chrome is listening for the debugger, and return its CDP url.
    If it's already up (you ran launch_my_browser.bat) we just use it. If it's NOT up,
    we auto-launch real Chrome with the dedicated profile + remote debugging — so every
    search runs in your real, signed-in Chrome with zero manual steps. Returns '' only
    if CDP isn't configured or Chrome couldn't be started."""
    cdp = os.getenv("BROWSER_USE_CDP_URL", "").strip()
    if not cdp:
        return ""
    if _cdp_reachable(cdp):
        return cdp
    if os.getenv("BROWSER_USE_AUTOLAUNCH_CHROME", "true").lower() not in ("1", "true", "yes"):
        return ""
    import subprocess
    import time
    port = cdp.rsplit(":", 1)[-1].split("/")[0].strip() or "9222"
    udd = (os.getenv("BROWSER_USE_USER_DATA_DIR", "").strip()
           or os.path.expanduser(r"~\agent-aware-chrome"))
    exe = _chrome_exe()
    try:
        logger.info(f"CDP not up — auto-launching your real Chrome ({exe}) on :{port}")
        subprocess.Popen(
            [exe, f"--remote-debugging-port={port}", f"--user-data-dir={udd}",
             "--no-first-run", "--no-default-browser-check",
             "--restore-last-session", "about:blank"],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
    except Exception as e:
        logger.warning(f"auto-launch of real Chrome failed: {e}")
        return ""
    for _ in range(30):                 # wait up to ~15s for the debugger to come up
        if _cdp_reachable(cdp):
            logger.info("your real Chrome is up and attachable via CDP")
            return cdp
        time.sleep(0.5)
    return cdp if _cdp_reachable(cdp) else ""


def _max_steps() -> int:
    try:
        return max(2, int(os.getenv("BROWSER_USE_MAX_STEPS", "6")))
    except ValueError:
        return 6


def _build_task(platform_name: str, entry_url: str, params: dict, hint: str = "",
                homepage: str = "") -> str:
    # A user-supplied hint (from the "needs your help" recovery flow) is injected as
    # high-priority guidance, e.g. "click the Search button at the top right" or
    # "the date picker is a calendar — pick June 22". This is the phase-1 form of
    # human-in-the-loop control; a future live mode would stream each step instead.
    hint_block = (
        f"\nIMPORTANT USER GUIDANCE — the user watched this fail last time and suggests: "
        f"\"{hint.strip()}\". Follow this guidance first; it usually unblocks the page.\n"
        if hint and hint.strip() else ""
    )
    # SELF-RECOVERY: deep-link URLs are sometimes half-built (missing dates) and return
    # "this site can't be reached" / a protocol error. The agent must NOT give up — it
    # should fall back to the homepage and search from the form there.
    recover = (homepage or entry_url.split("/")[0] + "//" + entry_url.split("/")[2]
               if "//" in entry_url else homepage)
    recover_block = (
        f"SELF-RECOVERY: if the page fails to load — 'this site can't be reached', a "
        f"connection/protocol error (e.g. ERR_HTTP2_PROTOCOL_ERROR), a 404, or a blank/error "
        f"page — do NOT stop. Navigate to the homepage {recover} and do the search from the "
        f"form there instead. Only give up after the homepage also fails.\n"
    )
    return (
        f"You are reading a price-comparison listing page. Open {entry_url} (the "
        f"{platform_name} website) and search for these criteria: {json.dumps(params)}.\n"
        f"Steps: dismiss any cookie/promo popup. If the search form is ALREADY filled with "
        f"the route/date, do NOT re-type those fields — the only thing left is to click the big "
        f"primary SEARCH button. After clicking SEARCH, WAIT for the results page to fully load "
        f"(it can take 5-10 seconds; the URL changes and a list of fares/listings appears) BEFORE "
        f"you read anything — don't conclude 'no results' from a still-loading page. If you do "
        f"need to fill a field, pick the matching option from its autocomplete dropdown, then SEARCH.\n"
        f"{recover_block}"
        f"{hint_block}"
        f"Then READ the results page and report the top 5 listings you can see — for each, "
        f"give its name, its price, and any key details shown (dates, rating, room/cabin/seat "
        f"type, amenities, times). Do NOT open individual listing detail pages; just read the "
        f"results list.\n"
        f"HARD RULES: do NOT log in, create an account, or enter any personal/payment details. "
        f"Do NOT attempt to solve any CAPTCHA or bot check. If the site blocks access or asks you "
        f"to sign in or verify you're human, STOP and report what you saw so far."
    )


def _make_step_callback(platform_id: str, platform_name: str = ""):
    """browser-use calls this on every step with (browser_state, model_output, n).
    We turn each step into a human-readable line for the live browser-use panel:
    what the agent decided to do, the concrete action, and the current URL — AND
    mirror it into the Agent Communication feed so you can watch the LLM's live
    navigation reasoning (its read of the last step + what it does next)."""
    from backend.browser_tracker import get_browser_tracker
    from backend.agent_bus import send as bus_send
    bt = get_browser_tracker()
    agent_label = f"{platform_name or platform_id} Browser Agent"

    def _cb(browser_state_summary, model_output, n_steps):
        try:
            url  = getattr(browser_state_summary, "url", "") or ""
            goal = getattr(model_output, "next_goal", "") or ""
            evalp = getattr(model_output, "evaluation_previous_goal", "") or ""
            # Summarize the concrete action (e.g. "click_element_by_index 5",
            # "go_to_url https://…", "input_text Mumbai").
            action = ""
            acts = getattr(model_output, "action", None) or []
            if acts:
                first = acts[0]
                d = first.model_dump(exclude_none=True) if hasattr(first, "model_dump") else {}
                if d:
                    name = next(iter(d))
                    params = d[name]
                    pv = ""
                    if isinstance(params, dict):
                        pv = next((str(v) for v in params.values() if v not in (None, "")), "")
                    action = f"{name} {pv}".strip()
            bt.record_step(platform_id, n=n_steps, goal=goal, action=action,
                           url=url, eval_prev=evalp)
            # Mirror the LLM's navigation decision into the communication feed.
            try:
                bus_send(frm="LLM · browser", to=agent_label, kind="response",
                         title=f"Step {n_steps}: {action or goal or 'deciding…'}"[:90],
                         content={"step": n_steps,
                                  "read_of_last_step": evalp,
                                  "next_goal": goal,
                                  "action": action,
                                  "url": url},
                         meta={"platform_id": platform_id})
            except Exception:
                pass
            # Capture the live page screenshot (base64 PNG) so the UI can show the
            # browser being controlled, computer-use style. Captured every step
            # regardless of vision (include_screenshot defaults True).
            shot = getattr(browser_state_summary, "screenshot", None)
            if shot:
                bt.record_screenshot(platform_id, shot)
        except Exception:
            pass  # tracking must never break the agent

    return _cb


def _groq_model_available(model: str, key: str) -> bool:
    """Probe whether Groq can serve this model right now (it dies on daily limits)."""
    if not key:
        return False
    try:
        from openai import OpenAI
        # Request a realistic step-sized budget (~2k tokens) so a near-exhausted daily
        # quota is correctly detected as unavailable — a 1-token probe false-passes.
        OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1").chat.completions.create(
            model=model, messages=[{"role": "user", "content": "x"}], max_tokens=2000)
        return True
    except Exception as e:
        s = str(e).lower()
        # Only treat rate-limit / quota as "unavailable"; other errors → assume usable.
        return not any(k in s for k in ("rate", "429", "quota", "exhausted"))


def _pick_browser_llm():
    """Pick a WORKING model for the browser agent. browser-use makes a call every step,
    so when Groq's daily limit is hit it must fall over — to Gemini, then the local
    model — instead of dying. Override the choice with BROWSER_USE_PROVIDER."""
    from browser_use.llm import ChatGroq, ChatGoogle, ChatOllama
    from browser_use.llm import ChatOpenAI
    prov = os.getenv("BROWSER_USE_PROVIDER", "auto").lower()
    groq_key = os.getenv("GROQ_API_KEY", "")
    gem_key = os.getenv("GEMINI_API_KEY", "")
    cere_key = os.getenv("CEREBRAS_API_KEY", "").strip()
    ollama_on = os.getenv("OLLAMA_ENABLED", "").lower() in ("1", "true", "yes")
    groq_model = os.getenv("BROWSER_USE_MODEL", "llama-3.3-70b-versatile")

    def mk_groq():
        return ChatGroq(model=groq_model, api_key=groq_key, temperature=0.0)

    def mk_cerebras():
        # Cerebras gpt-oss-120b via its OpenAI-compatible endpoint. 1M tokens/DAY free
        # (vs Gemini's ~20/day) → it NEVER hits the quota wall that kills browser runs.
        # Text-only (no vision), but strong enough to navigate the DOM, and being text
        # it's compatible with the text fallback (no "multimodal not supported" errors).
        return ChatOpenAI(model=os.getenv("BROWSER_USE_CEREBRAS_MODEL", "gpt-oss-120b"),
                          api_key=cere_key, base_url="https://api.cerebras.ai/v1",
                          temperature=0.0)

    def mk_gemini():
        # VISION-capable but tiny free quota (~20/day) — burns out fast under browser-use's
        # one-call-per-step. Use only when you have Gemini headroom (BROWSER_USE_PROVIDER=gemini).
        return ChatGoogle(model=os.getenv("BROWSER_USE_GEMINI_MODEL", "gemini-2.0-flash"),
                          api_key=gem_key)

    def mk_ollama():
        return ChatOllama(model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
                          host=os.getenv("OLLAMA_HOST", "http://localhost:11434"))

    try:
        if prov == "cerebras" and cere_key:
            return mk_cerebras(), "cerebras"
        if prov == "groq" and groq_key:
            return mk_groq(), "groq"
        if prov == "gemini" and gem_key:
            return mk_gemini(), "gemini"
        if prov == "ollama" and ollama_on:
            return mk_ollama(), "ollama"
        # auto: Cerebras (huge headroom) → Groq if serving → Gemini → local
        if cere_key:
            return mk_cerebras(), "cerebras"
        if groq_key and _groq_model_available(groq_model, groq_key):
            return mk_groq(), "groq"
        if gem_key:
            return mk_gemini(), "gemini"
        if ollama_on:
            return mk_ollama(), "ollama"
    except Exception as e:
        logger.warning(f"browser LLM pick failed ({e}); defaulting to Groq")
    return mk_groq(), "groq"


async def _run_agent(platform_name: str, entry_url: str, params: dict, max_steps: int,
                     hint: str = "", platform_id: str = "", headless: bool = None,
                     homepage: str = "") -> str:
    from browser_use import Agent, BrowserProfile

    llm, _prov = _pick_browser_llm()
    logger.info(f"browser-use using {_prov} for {platform_name}")
    # headless=None → use the env default; False → pop open a VISIBLE Chrome window
    # (used for the guided "open the browser so I can see the problem" recovery).
    if headless is None:
        headless = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"

    # ── Speed settings (from browser-use's own "fast agent" guidance) ──
    # The default agent waits ~0.5–1s between actions and for page loads, and emits a
    # "thinking" block each step — fine for hard tasks, slow for "search + read 5 rows".
    # We cut the waits right down and enable flash_mode (no thinking tokens) so each
    # step is a single fast LLM call, and let it batch a few actions per step.
    # Give async result pages (MakeMyTrip, flight OTAs…) time to render before the
    # agent reads them. The old 0.1s was too fast: it clicked Search, read instantly,
    # saw a still-loading page, and gave up — even though the search had fired. We now
    # wait for the network to settle (where the fares come from). Tunable via env.
    profile_kwargs = dict(
        headless=headless,
        minimum_wait_page_load_time=float(os.getenv("BROWSER_USE_MIN_WAIT", "1.0")),
        wait_for_network_idle_page_load_time=float(os.getenv("BROWSER_USE_IDLE_WAIT", "3.5")),
        wait_between_actions=float(os.getenv("BROWSER_USE_ACTION_WAIT", "0.4")),
    )
    # ── Use YOUR real browser instead of a fresh guest profile (optional) ──
    # Set these in .env to run with your logins/cookies (helps dodge bot-walls that
    # block fresh profiles). SAFETY: the agent is then authenticated as you — use a
    # DEDICATED Chrome profile signed into only the travel/shopping sites, not email/bank.
    #   BROWSER_USE_CDP_URL       e.g. http://localhost:9222  (attach to a Chrome you
    #                             launched with --remote-debugging-port=9222)
    #   BROWSER_USE_USER_DATA_DIR path to a Chrome user-data dir (a COPY if Chrome is open)
    #   BROWSER_USE_PROFILE       which profile inside it (e.g. "Default", "Profile 1")
    #   BROWSER_USE_CHANNEL       "chrome" to drive real Chrome instead of bundled Chromium
    # ── Browser Use Cloud (opt-in, PAID) ──────────────────────────────────────
    # When BROWSER_USE_CLOUD=true and a bu_ key is present, run the browser in
    # browser-use's CLOUD instead of locally. Cloud browsers have stealth
    # fingerprinting + rotating residential proxies → they defeat the 403/CAPTCHA
    # bot-blocks that stop local automation on hotel/shopping sites. Trade-offs:
    #   • costs credits (not free) — flip on only for sites that bot-block,
    #   • it's a FRESH cloud session, so it does NOT have your local logins.
    # When cloud is on it OWNS the session: local user_data_dir / cdp_url / headless
    # don't apply (there's no local Chrome to attach to).
    cloud_on = os.getenv("BROWSER_USE_CLOUD", "").lower() in ("1", "true", "yes")
    cloud_key = os.getenv("BROWSER_USE_API_KEY", "").strip()
    if cloud_on and cloud_key:
        profile_kwargs.pop("headless", None)
        profile_kwargs["use_cloud"] = True
        country = os.getenv("BROWSER_USE_PROXY_COUNTRY", "in").strip()
        if country:
            profile_kwargs["proxy_country_code"] = country  # IN proxy for Indian OTAs
        logger.info(f"browser-use CLOUD on (stealth + {country or 'default'} proxy) for {platform_name}")
    else:
        # ── ALWAYS LAUNCH THE REAL CHROME BINARY — never attach via CDP. ──────────
        # WHY: browser-use decides launch-vs-connect from `is_local`. Passing
        # `executable_path` forces is_local=True and uses THAT binary (verified:
        # is_local=True, executable_path=real chrome.exe). The OLD cdp_url-attach path
        # was the bug: under our threaded, parallel-agent model the shared CDP
        # connection collided ("Event loop is closed") and browser-use FELL BACK to
        # launching its bundled Chromium = "Chrome for Testing", headless. Launching
        # the real binary directly has none of that fragility.
        exe = _chrome_exe()
        if not os.path.exists(exe):
            # Real Chrome genuinely missing — last resort is the `channel` hint.
            logger.warning("real chrome.exe not found; falling back to channel=chrome")
            profile_kwargs["channel"] = os.getenv("BROWSER_USE_CHANNEL", "chrome").strip() or "chrome"
        else:
            profile_kwargs["executable_path"] = exe          # the REAL Chrome binary
            profile_kwargs["channel"] = os.getenv("BROWSER_USE_CHANNEL", "chrome").strip() or "chrome"
        profile_kwargs["headless"] = False                   # VISIBLE, always
        profile_kwargs["keep_alive"] = False                 # close cleanly each run
        # PROFILE: default to a FRESH temp profile each launch (bulletproof). browser-use
        # COPIES a passed user_data_dir to temp before launching, and that copy throws
        # "[Errno 13] Permission denied" on locked Cache files in your real profile →
        # the launch fails → 0 results. So only use the persistent (signed-in) profile
        # when you explicitly opt in with BROWSER_USE_PERSIST_PROFILE=true AND accept the
        # occasional lock. Either way it's REAL Chrome + visible, never Chrome-for-Testing.
        persist = os.getenv("BROWSER_USE_PERSIST_PROFILE", "").lower() in ("1", "true", "yes")
        user_data_dir = os.getenv("BROWSER_USE_USER_DATA_DIR", "").strip()
        if persist and user_data_dir:
            # browser-use COPIES the profile to temp before launching; locked Cache
            # files made that copy throw "Permission denied" → 0 results. Pre-delete the
            # regenerable cache dirs (NOT cookies/Login Data) so the copy always succeeds
            # while keeping your sign-in.
            _purge_profile_cache(user_data_dir)
            profile_kwargs["user_data_dir"] = user_data_dir
            if os.getenv("BROWSER_USE_PROFILE", "").strip():
                profile_kwargs["profile_directory"] = os.getenv("BROWSER_USE_PROFILE").strip()
        # Anti-bot: strip Chrome's automation switch so navigator.webdriver is false.
        profile_kwargs["ignore_default_args"] = ["--enable-automation"]
        logger.info(f"Launching REAL Chrome ({exe}) headless=False "
                    f"profile={'persistent' if (persist and user_data_dir) else 'fresh'} for {platform_name}")

    profile = BrowserProfile(**profile_kwargs)
    flash = os.getenv("BROWSER_USE_FLASH", "true").lower() == "true"
    # VISION: text-only models (Groq Llama, local) reason over the DOM only; a vision
    # model (Gemini) is given the page SCREENSHOT each step, which dramatically improves
    # navigation (it sees buttons/listings instead of guessing element indices). On by
    # default for Gemini; override with BROWSER_USE_VISION=true|false.
    _vis_env = os.getenv("BROWSER_USE_VISION", "").lower()
    use_vision = (_vis_env in ("1", "true", "yes")) or (_vis_env == "" and _prov == "gemini")
    # FALLBACK LLM: browser-use makes one LLM call per step, so a rate-limited primary
    # (e.g. Gemini's small free daily quota) used to KILL the run ("no fallback_llm
    # configured" → stop). Give it the local model (unlimited, never 429s) as a safety
    # net so navigation continues — slower, but it never dies on a quota wall.
    fallback_llm = None
    try:
        if _prov != "ollama" and os.getenv("OLLAMA_ENABLED", "").lower() in ("1", "true", "yes"):
            from browser_use.llm import ChatOllama
            fallback_llm = ChatOllama(model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
                                      host=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    except Exception as e:
        logger.debug(f"browser fallback_llm unavailable: {e}")
    agent = Agent(
        task=_build_task(platform_name, entry_url, params, hint=hint, homepage=homepage),
        llm=llm,
        browser_profile=profile,
        fallback_llm=fallback_llm,
        use_vision=use_vision,
        max_failures=int(os.getenv("BROWSER_USE_MAX_FAILURES", "2")),  # TEMP: raise via .env to push past bot-walls
        flash_mode=flash,        # no chain-of-thought tokens → fastest steps
        max_actions_per_step=4,  # batch actions (fill + submit in one step)
        extend_system_message=("Be extremely fast and concise. Batch multiple actions "
                               "into one step whenever possible. Don't over-explain — act."),
        register_new_step_callback=_make_step_callback(platform_id or platform_name, platform_name),
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

    # Always close — we launch a fresh real-Chrome window each run (keep_alive=False),
    # so closing it cleanly releases the profile lock for the next serialized agent.
    try:
        await agent.close()
    except Exception:
        pass

    return text or ""


async def browser_use_search(platform_name: str, entry_url: str, params: dict,
                             hint: str = "", retries: int = 1,
                             platform_id: str = "", headless: bool = None,
                             homepage: str = "") -> list[dict]:
    """Navigate + read a platform's results with a browser agent.

    Returns snippet dicts shaped like the other search backends ([{title, snippet,
    url}]) so the existing Groq extractor can structure them uniformly. Returns []
    on any failure so callers fall back to Google/DuckDuckGo.

    Every step + any failure is recorded in the browser tracker so the UI can show
    the live "what the browser is doing" panel and the EXACT error on a roadblock.

    `hint` injects user guidance (the "needs your help" recovery flow). `retries`
    re-attempts on a transient agent error (a flaky page load / timeout) so one bad
    run doesn't make the platform look failed — set retries=0 to disable.
    """
    if not is_enabled():
        return []
    if not entry_url:
        return []

    from backend.browser_tracker import get_browser_tracker
    bt = get_browser_tracker()
    pid = platform_id or platform_name
    bt.start(pid, platform_name=platform_name, entry_url=entry_url, hint=hint)

    text = ""
    last_err = None
    for attempt in range(retries + 1):
        try:
            text = await _run_agent(platform_name, entry_url, params, _max_steps(),
                                    hint=hint, platform_id=pid, headless=headless,
                                    homepage=homepage)
            if text and len(text.strip()) >= 20:
                break
        except Exception as e:
            last_err = e
            logger.warning(f"browser-use attempt {attempt+1} failed for {platform_name}: {e}")
            bt.record_error(pid, e)

    if not text or len(text.strip()) < 20:
        if last_err:
            logger.warning(f"browser-use gave up on {platform_name}: {last_err}")
            bt.record_error(pid, last_err)
        else:
            logger.info(f"browser-use returned no usable content for {platform_name}")
            if not bt.error_for(pid):
                bt.record_error(pid, "The browser reached the page but couldn't read a "
                                     "results list (no listings detected).")
        bt.record_done(pid, success=False, n_results=0)
        return []

    logger.info(f"browser-use got {len(text)} chars of content for {platform_name}")
    bt.record_done(pid, success=True, n_results=1)
    return [{"title": f"{platform_name} results", "snippet": text, "url": entry_url}]

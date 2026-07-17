import os, sys, time, threading, base64, httpx, streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

# Shared with the backend so the UI's notion of "booking type" never drifts from
# what the comparison engine actually groups/compares on.
from backend.booking_type import BOOKING_TYPE_FIELDS as _BOOKING_TYPE_FIELDS, extract_booking_type as _booking_type
from backend.memory.reliability import get_reliability as _get_reliability
from backend.progress import get_tracker as _get_tracker
from backend.diagnostics import get_diagnostics as _get_diag
from backend.browser_tracker import get_browser_tracker as _get_browser
from backend.agent_bus import get_bus as _get_bus
from frontend.auth import require_login, render_user_chip
from frontend.slack_ui import render_slack_panel

# Plain-English + visual treatment for each reliability label the backend can return.
# "unknown" renders nothing — we only speak up once we actually have a track record,
# so a brand-new platform isn't unfairly badged as untrustworthy on day one.
_RELIABILITY_BADGE = {
    "reliable":   ("✓ Usually comes through", "rel-good"),
    "mixed":      ("~ Hit or miss lately",          "rel-mixed"),
    "unreliable": ("⚠ Often unavailable",       "rel-bad"),
}

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Agent-Aware", page_icon="🔍", layout="wide",
                   initial_sidebar_state="expanded")

# Load CSS from file — avoids the raw-text-leak bug in Streamlit
_css_path = os.path.join(os.path.dirname(__file__), "style.css")
with open(_css_path, encoding="utf-8") as _f:
    st.markdown(f"<style>{_f.read()}</style>", unsafe_allow_html=True)

# Load full platform config once at startup
import yaml as _yaml, re as _re, json as _json
from urllib.parse import quote_plus as _qp
_platforms_yaml = os.path.join(os.path.dirname(__file__), "../config/platforms.yaml")
with open(os.path.normpath(_platforms_yaml), encoding="utf-8") as _f:
    _ALL_PLATFORMS: dict[str, dict] = {
        p["id"]: p
        for p in _yaml.safe_load(_f).get("platforms", [])
    }
_PLATFORM_WEBSITES: dict[str, str] = {pid: p.get("website","") for pid, p in _ALL_PLATFORMS.items()}


def _build_deep_link(platform_id: str, intent_params: dict) -> str:
    """Fill the platform's search_url_template with actual search params."""
    p = _ALL_PLATFORMS.get(platform_id, {})
    template = p.get("search_url_template", "")
    website  = p.get("website", "")
    if not template:
        return website
    flat: dict[str, str] = {}
    for k, v in intent_params.items():
        if isinstance(v, dict):
            flat[k] = _qp(str(list(v.values())[0])) if v else ""
        else:
            flat[k] = _qp(str(v)) if v is not None else ""
    flat.setdefault("check_in",      flat.get("checkin", flat.get("date", "")))
    flat.setdefault("check_out",     flat.get("checkout", flat.get("return_date", "")))
    flat.setdefault("event_type",    flat.get("query", ""))
    flat.setdefault("product_name",  flat.get("query", flat.get("product", "")))
    flat.setdefault("cuisine",       flat.get("query", ""))
    try:
        filled = template.format_map(flat)
        # If any {placeholder} remains, fall back to homepage
        if _re.search(r"\{[^}]+\}", filled):
            return website
        return filled
    except Exception:
        return website


# ── API call ──────────────────────────────────────────────────
def call_api(query: str) -> dict:
    try:
        r = httpx.post(f"{API_URL}/search", json={"query": query}, timeout=120)
        r.raise_for_status()
        return r.json()
    except Exception:
        from backend.graph import run_search
        s = run_search(query)
        return {
            "query": s["query"], "status": s.get("status", "done"),
            "intent": s.get("intent"),
            "platform_results": {
                pid: {"platform_name": r.get("platform_name", pid),
                      "icon": r.get("icon", "🔍"), "results": r.get("results", []),
                      "error": r.get("error"), "elapsed_seconds": r.get("elapsed_seconds", 0),
                      "tier": r.get("tier", ""), "roadblock": r.get("roadblock")}
                for pid, r in s.get("platform_results", {}).items()
            },
            "comparison": s.get("comparison"),
            "segments": s.get("segments"),
            "insights": s.get("insights"),
            "recommendation": s.get("recommendation"),
            "diagnostics": s.get("diagnostics"),
            "browser_runs": s.get("browser_runs"),
            "agent_comms": s.get("agent_comms"),
            "validation": s.get("validation"),
            "remediation_log": s.get("remediation_log"),
            "error": s.get("error"),
        }


# ── Live progress runner ──────────────────────────────────────
# Instead of a generic "Searching…" spinner, run the pipeline in a worker thread
# and stream the real background steps (each platform search, comparison, AI
# insights) into a status panel as they happen. The backend nodes push events
# into a shared, thread-safe tracker (backend/progress.py); we poll it here.
_KIND_ICON = {"start": "🔄", "ok": "✅", "warn": "⚠️", "done": "🏁", "info": "•"}


def _render_progress(slot, events: list[dict]):
    """Show only the *current* step as a single, live-updating line."""
    if not events:
        msg, t = "Starting…", 0.0
    else:
        ev = events[-1]
        msg = str(ev.get("message", "")).replace("<", "&lt;").replace(">", "&gt;")
        t = ev.get("t", 0) or 0
    slot.markdown(
        f'<div class="prog-line"><span class="prog-ico">🔄</span>'
        f'<span class="prog-msg">{msg}</span>'
        f'<span class="prog-t">{t:.0f}s</span></div>',
        unsafe_allow_html=True,
    )


# ── Two always-on engine panels: eval checklist + live browser-use ────────────
# These two side-by-side boxes are present all the time and stream in real time
# during a search: the left one is a checklist of every stage (and exactly where
# it's breaking); the right one shows the automated browser working step by step,
# with the precise error when it hits a roadblock.
def _esc(s, n: int = 90) -> str:
    return str(s).replace("<", "&lt;").replace(">", "&gt;")[:n]


# The pipeline stages, in order, mapped to their backend node names + a friendly label.
_STAGES = [
    ("parse_intent",     "Understand the request"),
    ("search_platforms", "Search every platform"),
    ("aggregate",        "Aggregate results"),
    ("compare",          "Compare like-for-like"),
    ("segregate",        "Group by type"),
    ("insights",         "Build comparison + insights"),
    ("recommend",        "Pick the best option"),
]


def _eval_checklist_html(diagnostics: dict, live: bool) -> str:
    """Left panel: a checklist of pipeline stages (done / running / pending) with
    per-platform pass/fail and an explicit list of where things are breaking."""
    dot = ('<span style="color:#22c55e;font-weight:700;">● live</span>' if live
           else '<span style="color:#9A7E58;font-weight:600;">idle</span>')
    head = (f'<div style="font-size:0.74rem;font-weight:800;letter-spacing:.06em;'
            f'text-transform:uppercase;color:#6B5338;display:flex;justify-content:space-between;'
            f'align-items:center;margin-bottom:8px;"><span>✅ Eval checklist</span>{dot}</div>')

    diagnostics = diagnostics or {}
    done = {n["name"]: n["seconds"] for n in diagnostics.get("nodes", [])}
    plats = diagnostics.get("platforms", [])

    rows = ""
    for node, label in _STAGES:
        if node in done:
            mark, color, extra = "✓", "#16a34a", f'<span style="color:#9A7E58;">{done[node]}s</span>'
        elif live:
            mark, color, extra = "◷", "#d97706", '<span style="color:#9A7E58;">…</span>'
        else:
            mark, color, extra = "·", "#cbd5e1", ""
        rows += (f'<div style="display:flex;gap:8px;align-items:baseline;font-size:0.8rem;padding:2px 0;">'
                 f'<span style="color:{color};font-weight:700;width:14px;">{mark}</span>'
                 f'<span style="flex:1;color:#4A3623;">{label}</span>{extra}</div>')
        # Under "search platforms", list each platform's pass/fail.
        if node == "search_platforms" and plats:
            for p in plats:
                n = p.get("n_results", 0)
                ok = n > 0
                tier = _TIER_LABEL.get(p.get("tier", ""), p.get("tier") or "—").split(" ")[0]
                badge = (f'<span style="color:#16a34a;">✓ {n} · {tier}</span>' if ok
                         else '<span style="color:#dc2626;">✗ 0</span>')
                rows += (f'<div style="display:flex;gap:6px;font-size:0.74rem;padding:1px 0 1px 22px;'
                         f'color:#9A7E58;"><span style="flex:1;overflow:hidden;text-overflow:ellipsis;'
                         f'white-space:nowrap;">{_esc(p.get("platform_name",""),22)}</span>'
                         f'{badge}<span style="color:#9A7E58;width:42px;text-align:right;">'
                         f'{p.get("elapsed",0)}s</span></div>')

    # Breakpoints — explicit "where it's breaking" callouts.
    breaks = []
    for p in plats:
        if not p.get("n_results"):
            where = p.get("tier") or (p.get("tiers_tried") or ["search"])[-1] if p.get("tiers_tried") else "search"
            rb = p.get("roadblock") or {}
            breaks.append((p.get("platform_name", "?"), rb.get("reason", "no results")))
    brk_html = ""
    if breaks:
        items = "".join(
            f'<div style="font-size:0.74rem;color:#9f1239;padding:2px 0;">⚠ <b>{_esc(nm,18)}</b> — {_esc(reason,80)}</div>'
            for nm, reason in breaks[:6]
        )
        brk_html = (f'<div style="margin-top:10px;border-top:1px solid #fee2e2;padding-top:8px;">'
                    f'<div style="font-size:0.68rem;font-weight:700;letter-spacing:.05em;'
                    f'text-transform:uppercase;color:#dc2626;margin-bottom:4px;">Breakpoints</div>'
                    f'{items}</div>')
    elif not live and done:
        brk_html = ('<div style="margin-top:10px;font-size:0.76rem;color:#16a34a;">'
                    '✓ No breakpoints — every stage completed.</div>')

    total = diagnostics.get("total_seconds", 0) or 0
    foot = (f'<div style="margin-top:8px;font-size:0.72rem;color:#9A7E58;">total {total:.1f}s</div>'
            if total else "")
    if not done and not live:
        body = ('<div style="font-size:0.8rem;color:#9A7E58;border:1px dashed #EADFCB;'
                'border-radius:8px;padding:12px;">Run a search — each stage and any '
                'breakpoint will check off here in real time.</div>')
        return head + body
    return head + rows + brk_html + foot


_STATUS_META = {
    "running": ("◷ working", "#d97706"),
    "ok":      ("✓ done",    "#16a34a"),
    "stuck":   ("✗ stuck",   "#dc2626"),
}
_CAT_COLOR = {"bot_block": "#dc2626", "navigation": "#d97706", "timeout": "#0891b2",
              "no_results": "#9A7E58", "unknown": "#9A7E58"}


def _step_mark(eval_str: str) -> tuple[str, str]:
    """Per-step status from browser-use's evaluation_previous_goal."""
    e = (eval_str or "").lower()
    if "success" in e:
        return ("✓", "#16a34a")
    if "fail" in e:
        return ("✗", "#dc2626")
    return ("›", "#C05800")


def _platform_activity_events(events: list, platform_name: str) -> list:
    """Progress-tracker events that mention this platform by name — the running
    log of what the agent for this tab is doing (search.py emits these with the
    platform's display name baked into the message)."""
    name_l = (platform_name or "").lower()
    if not name_l:
        return []
    return [e for e in (events or []) if name_l in (e.get("message", "") or "").lower()]


_EVENT_KIND_META = {
    "start": ("▶", "#C05800"),
    "ok":    ("✓", "#16a34a"),
    "done":  ("✓", "#16a34a"),
    "warn":  ("⚠", "#dc2626"),
    "info":  ("·", "#9A7E58"),
}


def _render_platform_tab_html(platform_name: str, browser_run: dict | None,
                               diag_entry: dict | None, events: list) -> str:
    """Everything happening for ONE platform's agent: live status, the activity
    log (what each step is doing), browser steps with ✓/✗ marks, monitor
    diagnosis on failure, and the final result count."""
    status = (browser_run or {}).get("status")
    if not status:
        if diag_entry is not None:
            status = "ok" if diag_entry.get("n_results") else "stuck"
        else:
            status = "running"
    s_label, s_color = _STATUS_META.get(status, ("◷ queued", "#B3996E"))

    html = (f'<div style="font-size:0.8rem;font-weight:700;color:{s_color};'
            f'margin-bottom:6px;">{s_label}</div>')

    # Activity log — every emit() that mentioned this platform, in order.
    plat_events = _platform_activity_events(events, platform_name)
    if plat_events:
        rows = ""
        for e in plat_events:
            icon, color = _EVENT_KIND_META.get(e.get("kind"), ("·", "#9A7E58"))
            rows += (f'<div style="font-size:0.74rem;padding:1px 0;color:#4A3623;">'
                     f'<span style="color:{color};font-weight:700;">{icon}</span> '
                     f'{_esc(e.get("message",""), 90)}'
                     f'<span style="color:#B3996E;float:right;">{e.get("t",0)}s</span></div>')
        html += (f'<div style="margin-bottom:8px;"><div style="font-size:0.68rem;font-weight:700;'
                 f'letter-spacing:.05em;text-transform:uppercase;color:#6B5338;margin-bottom:3px;">'
                 f'Agent activity</div><div style="background:#FCF8EE;border:1px solid #F1E8D8;'
                 f'border-radius:6px;padding:5px 7px;">{rows}</div></div>')

    # Browser steps — only present when this platform needed live browser automation.
    if browser_run:
        url = _esc(browser_run.get("url") or browser_run.get("entry_url", ""), 64)
        if url:
            html += f'<div style="font-size:0.7rem;color:#9A7E58;margin-bottom:4px;">{url}</div>'
        steps_html = ""
        for s in browser_run.get("steps", [])[-10:]:
            mk, mc = _step_mark(s.get("eval", ""))
            goal = _esc(s.get("goal") or s.get("action", ""), 70)
            act = _esc(s.get("action", ""), 36)
            steps_html += (f'<div style="font-size:0.72rem;color:#4A3623;padding:1px 0;display:flex;gap:5px;">'
                           f'<span style="color:{mc};font-weight:700;width:14px;">{s.get("n","")}</span>'
                           f'<span style="color:{mc};">{mk}</span>'
                           f'<span style="flex:1;">{goal}'
                           + (f' <span style="color:#9A7E58;">[{act}]</span>' if act else '')
                           + f'</span><span style="color:#9A7E58;">{s.get("t",0)}s</span></div>')
        if steps_html:
            html += (f'<div style="margin-bottom:8px;"><div style="font-size:0.68rem;font-weight:700;'
                     f'letter-spacing:.05em;text-transform:uppercase;color:#6B5338;margin-bottom:3px;">'
                     f'Browser steps</div><div style="max-height:180px;overflow-y:auto;background:#FCF8EE;'
                     f'border:1px solid #F1E8D8;border-radius:6px;padding:5px 7px;">{steps_html}</div></div>')

        a = browser_run.get("analysis")
        if status == "stuck" and a:
            cat = a.get("category", "unknown")
            cc = _CAT_COLOR.get(cat, "#9A7E58")
            fix = a.get("suggested_hint")
            fix_html = (f'<div style="margin-top:4px;color:#0f766e;">💡 <b>Fix:</b> {_esc(fix,160)}</div>'
                        if fix else '<div style="margin-top:4px;color:#991b1b;">Not fixable by guidance.</div>')
            html += (f'<div style="background:#FBEEDD;border:1px solid #EAD3AC;border-radius:6px;'
                     f'padding:6px 8px;font-size:0.72rem;color:#3730a3;margin-bottom:6px;">'
                     f'<div style="display:flex;justify-content:space-between;">'
                     f'<b>🩺 Monitor agent</b><span style="color:{cc};font-weight:700;">{cat}</span></div>'
                     f'<div style="color:#9A3B00;margin-top:2px;">{_esc(a.get("diagnosis",""),180)}</div>'
                     f'{fix_html}</div>')
        elif status == "stuck" and browser_run.get("error"):
            html += (f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;'
                     f'padding:5px 8px;font-size:0.71rem;color:#991b1b;margin-bottom:6px;font-family:monospace;">'
                     f'{_esc(browser_run["error"],180)}</div>')

        if status == "stuck":
            html += ('<div style="font-size:0.71rem;color:#0f766e;">↓ Guide this one in '
                     '<b>Needs your input</b> below.</div>')

    # Final tally from the diagnostics, once the platform has finished.
    if diag_entry:
        n = diag_entry.get("n_results", 0)
        tier = _TIER_LABEL.get(diag_entry.get("tier", ""), diag_entry.get("tier") or "—").split(" ")[0]
        rb = diag_entry.get("roadblock") or {}
        summary = f'{n} result{"s" if n != 1 else ""} · {tier} · {diag_entry.get("elapsed",0)}s'
        if rb.get("reason"):
            summary += f' · ⚠ {_esc(rb["reason"], 80)}'
        html += f'<div style="font-size:0.72rem;color:#9A7E58;margin-top:6px;">{summary}</div>'

    if not plat_events and not browser_run and not diag_entry:
        html += '<div style="font-size:0.8rem;color:#9A7E58;">Waiting to start…</div>'

    return html


def _render_platform_tabs(slot, browser_runs: list, diagnostics: dict, events: list, live: bool) -> None:
    """Right panel: ONE TAB PER PLATFORM the agent is searching. Pick a tab to see
    that platform's full activity — agent communication (what it's doing and why),
    browser steps with ✓/✗ status, monitor diagnosis on failure, and result count."""
    diagnostics = diagnostics or {}
    plats = diagnostics.get("platforms", [])
    diag_by_name = {p.get("platform_name"): p for p in plats}
    runs_by_name = {(r.get("platform_name") or ""): r for r in (browser_runs or [])}

    names: list[str] = [p.get("platform_name") for p in plats if p.get("platform_name")]
    for r in (browser_runs or []):
        nm = r.get("platform_name")
        if nm and nm not in names:
            names.append(nm)

    dot = ('<span style="color:#22c55e;font-weight:700;">● live</span>' if live
           else '<span style="color:#9A7E58;font-weight:600;">idle</span>')
    with slot.container():
        st.markdown(
            f'<div style="font-size:0.74rem;font-weight:800;letter-spacing:.06em;'
            f'text-transform:uppercase;color:#6B5338;display:flex;justify-content:space-between;'
            f'align-items:center;margin-bottom:8px;"><span>🌐 Platform agents ({len(names)})</span>{dot}</div>',
            unsafe_allow_html=True)

        if not names:
            st.markdown(
                '<div style="font-size:0.8rem;color:#9A7E58;border:1px dashed #EADFCB;'
                'border-radius:8px;padding:12px;">Run a search — each platform gets its own tab here, '
                'showing everything its agent does, step by step, with a ✓/✗ status.</div>',
                unsafe_allow_html=True)
            return

        tab_labels = []
        for nm in names:
            run = runs_by_name.get(nm)
            diag = diag_by_name.get(nm)
            status = (run or {}).get("status")
            if not status:
                status = "ok" if diag and diag.get("n_results") else ("stuck" if diag else "running")
            icon = {"ok": "✓", "stuck": "✗", "running": "◷"}.get(status, "◷")
            tab_labels.append(f"{icon} {nm[:16]}")

        tabs = st.tabs(tab_labels)
        for tab, nm in zip(tabs, names):
            with tab:
                st.markdown(
                    _render_platform_tab_html(nm, runs_by_name.get(nm), diag_by_name.get(nm), events),
                    unsafe_allow_html=True)


def render_engine_panels(eval_slot, browser_slot, diagnostics, browser_runs, live: bool, events=None) -> None:
    eval_slot.markdown(_eval_checklist_html(diagnostics, live), unsafe_allow_html=True)
    _render_platform_tabs(browser_slot, browser_runs or [], diagnostics, events or [], live)


# ── Agent Communication feed ──────────────────────────────────────────────────
# A chat-like transcript of the ACTUAL messages agents send each other during a
# search: the request each agent sends the LLM and the plan it gets back, the
# instructions Intent hands to the Search coordinator, the params dispatched to
# each platform agent and the results returned, the data each pipeline stage passes
# on, and the Monitor agent's diagnosis when a tab gets stuck. This is the
# "show me how the agents talk to each other" view.
def _esc_full(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_COMMS_KIND = {
    "request":   ("➡️", "#C05800", "request"),
    "response":  ("⬅️", "#0891b2", "response"),
    "handoff":   ("🤝", "#7c3aed", "handoff"),
    "dispatch":  ("📤", "#0d9488", "dispatch"),
    "data":      ("📦", "#16a34a", "data"),
    "diagnosis": ("🩺", "#9A3B00", "diagnosis"),
    "error":     ("⚠️", "#dc2626", "error"),
    "message":   ("💬", "#9A7E58", "message"),
}


def _agent_color(name: str) -> str:
    n = (name or "").lower()
    if n in ("you", "user"):
        return "#2A1A0A"
    if n.startswith("llm"):
        return "#0891b2"
    if "monitor" in n:
        return "#9A3B00"
    if "coordinator" in n or "intent" in n:
        return "#7c3aed"
    return "#0d9488"


def _agent_comms_html(events: list, live: bool) -> str:
    dot = ('<span style="color:#22c55e;font-weight:700;">● live</span>' if live
           else '<span style="color:#9A7E58;font-weight:600;">idle</span>')
    n = len(events or [])
    # `comms-shell` carries the animated aurora background (style.css); `live`
    # speeds it up while a search is streaming so the panel visibly "breathes".
    shell_open = f'<div class="comms-shell{" comms-live" if live else ""}"><div class="comms-inner">'
    shell_close = '</div></div>'
    head = (shell_open +
            f'<div style="font-size:0.74rem;font-weight:800;letter-spacing:.06em;'
            f'text-transform:uppercase;color:#6B5338;display:flex;justify-content:space-between;'
            f'align-items:center;margin-bottom:10px;">'
            f'<span>🛰 Agent communication ({n})</span>{dot}</div>')

    if not events:
        return head + ('<div style="font-size:0.8rem;color:#9A7E58;border:1px dashed #EADFCB;'
                       'border-radius:8px;padding:12px;">Run a search — every message agents send '
                       'each other appears here: the request to the LLM and the plan it returns, '
                       'the instructions handed to the search coordinator, the params dispatched to '
                       'each website agent and the results they send back, and the monitor agent’s '
                       'diagnosis when a tab gets stuck. Expand any message to read the exact payload.</div>'
                       + shell_close)

    rows = ""
    for e in events:
        icon, color, label = _COMMS_KIND.get(e.get("kind"), _COMMS_KIND["message"])
        frm = _esc_full(e.get("frm", "")); to = _esc_full(e.get("to", ""))
        fc = _agent_color(e.get("frm", "")); tc = _agent_color(e.get("to", ""))
        title = _esc_full(e.get("title", ""))
        content = e.get("content", "")
        t = e.get("t", 0)
        details = ""
        if content:
            details = (f'<details style="margin-top:5px;"><summary style="cursor:pointer;'
                       f'font-size:0.72rem;color:#C05800;">view message</summary>'
                       f'<pre style="white-space:pre-wrap;word-break:break-word;font-size:0.72rem;'
                       f'background:#F7F1E4;border:1px solid #F1E8D8;border-radius:6px;'
                       f'padding:8px 10px;margin:5px 0 0;color:#4A3623;max-height:280px;'
                       f'overflow:auto;">{_esc_full(content)}</pre></details>')
        rows += (
            f'<div style="border-left:3px solid {color};background:#fff;border:1px solid #F1E8D8;'
            f'border-left-width:3px;border-radius:8px;padding:8px 11px;margin-bottom:7px;">'
            f'<div style="display:flex;align-items:center;gap:6px;font-size:0.78rem;flex-wrap:wrap;">'
            f'<b style="color:{fc};">{frm}</b>'
            f'<span style="color:#B3996E;">→</span>'
            f'<b style="color:{tc};">{to}</b>'
            f'<span style="background:{color}1a;color:{color};font-weight:700;font-size:0.62rem;'
            f'text-transform:uppercase;letter-spacing:.04em;padding:1px 7px;border-radius:999px;">'
            f'{icon} {label}</span>'
            f'<span style="margin-left:auto;color:#B3996E;font-size:0.7rem;">{t}s</span></div>'
            f'<div style="font-size:0.8rem;color:#2A1A0A;margin-top:3px;">{title}</div>'
            f'{details}</div>'
        )
    return head + rows + shell_close


def render_agent_comms(slot, events, live: bool) -> None:
    slot.markdown(_agent_comms_html(events or [], live), unsafe_allow_html=True)


# ── Validation & Remediation panel ────────────────────────────────────────────
# Shows the autonomous Validation Agent's work: the verdict, every check it ran
# (pass/fail + the real evidence), any fix it applied (before → after), honest
# notes when a hard constraint couldn't be met (with proof), and the round-by-round
# remediation timeline. This is the "show the validation, the fix, and the details
# of the fix" surface — fully automated, zero user interaction.
_VERDICT_STYLE = {
    "valid":         ("✅", "#16a34a", "Validated"),
    "fixed":         ("🛠️", "#7c3aed", "Auto-fixed"),
    "best_effort":   ("⚖️", "#d97706", "Best available"),
    "issues_remain": ("⚠️", "#dc2626", "Issues flagged"),
}


def _validation_html(validation: dict, remediation_log: list) -> str:
    if not validation:
        return ('<div style="font-size:0.74rem;font-weight:800;letter-spacing:.06em;'
                'text-transform:uppercase;color:#6B5338;margin-bottom:8px;">🛡 Validation</div>'
                '<div style="font-size:0.8rem;color:#9A7E58;border:1px dashed #EADFCB;'
                'border-radius:8px;padding:12px;">Run a search — the Validation Agent automatically '
                'checks the result against the real data, fixes the recommendation if it’s wrong, '
                'and shows exactly what it changed and why.</div>')

    icon, color, label = _VERDICT_STYLE.get(validation.get("verdict"), _VERDICT_STYLE["valid"])
    checks = validation.get("checks", [])
    n_pass = sum(1 for c in checks if c.get("passed"))
    head = (f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
            f'<span style="font-size:0.74rem;font-weight:800;letter-spacing:.06em;'
            f'text-transform:uppercase;color:#6B5338;">🛡 Validation</span>'
            f'<span style="background:{color}1a;color:{color};font-weight:800;font-size:0.74rem;'
            f'padding:2px 12px;border-radius:999px;">{icon} {label}</span>'
            f'<span style="margin-left:auto;color:#9A7E58;font-size:0.74rem;">'
            f'{n_pass}/{len(checks)} checks passed · {validation.get("elapsed_seconds",0)}s</span></div>')

    # Checks
    rows = ""
    for c in checks:
        ok = c.get("passed")
        mark = "✓" if ok else "✗"
        mc = "#16a34a" if ok else "#dc2626"
        proof = c.get("proof")
        proof_html = ""
        if proof:
            proof_html = (f'<details style="margin-top:3px;"><summary style="cursor:pointer;'
                          f'font-size:0.68rem;color:#C05800;">proof</summary>'
                          f'<pre style="white-space:pre-wrap;font-size:0.68rem;background:#F7F1E4;'
                          f'border:1px solid #F1E8D8;border-radius:6px;padding:6px 8px;margin:4px 0 0;'
                          f'color:#4A3623;">{_esc_full(_json.dumps(proof, indent=2, default=str))}</pre></details>')
        rows += (f'<div style="display:flex;gap:8px;padding:5px 0;border-bottom:1px solid #F4ECD9;">'
                 f'<span style="color:{mc};font-weight:800;">{mark}</span>'
                 f'<div style="flex:1;"><span style="font-weight:700;font-size:0.78rem;color:#2A1A0A;">'
                 f'{_esc_full(c.get("name",""))}</span>'
                 f'<span style="font-size:0.78rem;color:#6B5338;"> — {_esc_full(c.get("detail",""))}</span>'
                 f'{proof_html}</div></div>')

    # Fix details
    fix_html = ""
    fd = validation.get("fix_details")
    if fd:
        b, a = fd.get("before", {}), fd.get("after", {})
        fix_html = (
            f'<div style="background:#faf5ff;border:1px solid #e9d5ff;border-radius:8px;'
            f'padding:9px 12px;margin-top:9px;">'
            f'<div style="font-weight:800;font-size:0.74rem;color:#7c3aed;margin-bottom:4px;">'
            f'🛠️ Fix applied — {_esc_full(fd.get("what",""))}</div>'
            f'<div style="font-size:0.78rem;color:#4A3623;">'
            f'<b>Before:</b> {_esc_full(b.get("platform"))} ₹{_esc_full(b.get("price"))}'
            + (f' <span style="color:#dc2626;">({_esc_full(", ".join(b.get("violations") or []))})</span>' if b.get("violations") else "")
            + f'<br><b>After:</b> <span style="color:#16a34a;">{_esc_full(a.get("platform_name") or a.get("platform"))} ₹{_esc_full(a.get("price"))}</span>'
            f'<br><b>Why:</b> {_esc_full(fd.get("why",""))}</div></div>')

    # Constraint notes (honest, with proof)
    notes_html = ""
    for n in validation.get("constraint_notes", []):
        pr = n.get("proof", {})
        notes_html += (
            f'<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;'
            f'padding:9px 12px;margin-top:9px;">'
            f'<div style="font-weight:800;font-size:0.74rem;color:#b45309;margin-bottom:4px;">'
            f'⚖️ Couldn’t fully meet: {_esc_full(n.get("constraint",""))}</div>'
            f'<div style="font-size:0.78rem;color:#4A3623;">{_esc_full(n.get("issue",""))}<br>'
            f'<b>Best available:</b> {_esc_full(n.get("best_available",""))} · '
            f'<b>What we did:</b> {_esc_full(n.get("what_we_did",""))}'
            f'<details style="margin-top:4px;"><summary style="cursor:pointer;font-size:0.68rem;'
            f'color:#C05800;">proof</summary>'
            f'<pre style="white-space:pre-wrap;font-size:0.68rem;background:#fff;border:1px solid #fde68a;'
            f'border-radius:6px;padding:6px 8px;margin:4px 0 0;color:#4A3623;">'
            f'{_esc_full(_json.dumps(pr, indent=2, default=str))}</pre></details></div></div>')

    # Remediation timeline
    rem_html = ""
    if remediation_log:
        items = ""
        for entry in remediation_log:
            acts = "; ".join(f'{a.get("action")}({a.get("target") or ""})→{a.get("outcome")}'
                             for a in entry.get("actions", []))
            items += (f'<div style="font-size:0.76rem;color:#4A3623;padding:3px 0;">'
                      f'<b>Round {entry.get("round")}:</b> fixing {_esc_full(", ".join(entry.get("issues") or []))} '
                      f'→ {_esc_full(acts)}</div>')
        rem_html = (f'<div style="margin-top:9px;border-top:1px dashed #EADFCB;padding-top:7px;">'
                    f'<div style="font-weight:800;font-size:0.72rem;color:#6B5338;margin-bottom:3px;">'
                    f'🔁 Remediation timeline ({len(remediation_log)} round(s))</div>{items}</div>')

    return head + rows + fix_html + notes_html + rem_html


def render_validation_panel(slot, validation, remediation_log, live: bool = False) -> None:
    inner = _validation_html(validation or {}, remediation_log or [])
    slot.markdown(
        f'<div style="background:#fff;border:1.5px solid #EADFCB;border-radius:14px;'
        f'padding:14px 16px;margin-top:6px;">{inner}</div>', unsafe_allow_html=True)


# ── Computer-use stage: when a browser tab is being controlled, show its LIVE screen
#    full-width (like screen-share), with the agents' current actions in a clean strip
#    beneath it — no cramped side column. ──
def _agent_strip_html(browser_runs: list) -> str:
    """A horizontal row of agent chips (name · current step · status) under the screen."""
    chips = ""
    for r in browser_runs or []:
        if r.get("status") not in ("running", "stuck"):
            continue
        last = (r.get("steps") or [{}])[-1]
        stuck = r.get("status") == "stuck"
        sc = "#dc2626" if stuck else "#d97706"
        sl = "✗ stuck" if stuck else "◷ working"
        step = _esc(last.get("goal") or last.get("action", ""), 48)
        chips += (f'<div style="flex:1 1 220px;min-width:200px;border:1px solid #EADFCB;'
                  f'border-radius:10px;padding:8px 12px;background:#fff;">'
                  f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                  f'<b style="font-size:0.85rem;color:#2A1A0A;">{_esc(r.get("platform_name",""),24)}</b>'
                  f'<span style="color:{sc};font-weight:700;font-size:0.75rem;">{sl}</span></div>'
                  f'<div style="font-size:0.8rem;color:#6B5338;margin-top:4px;white-space:nowrap;'
                  f'overflow:hidden;text-overflow:ellipsis;">'
                  f'<span style="color:#C05800;font-weight:700;">{last.get("n","")}›</span> {step}</div></div>')
    if not chips:
        chips = '<div style="font-size:0.85rem;color:#16a34a;">Reading results…</div>'
    return (f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;">{chips}</div>')


def _render_stage(slot, shot, browser_runs) -> None:
    """Full-width live 'screen-share' of the browser being controlled + agent strip."""
    name, b64 = shot
    try:
        img = base64.b64decode(b64.split(",")[-1])
    except Exception:
        return
    with slot.container():
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:#2A1A0A;margin:6px 0 8px;">'
            f'🖥 Live browser — controlling <b>{_esc(name,30)}</b> '
            f'<span style="color:#22c55e;">● live</span></div>',
            unsafe_allow_html=True)
        # Caption = text alternative for the screenshot (a screen reader can't see the
        # image; the caption + the agent strip below describe what's on screen).
        st.image(img, use_container_width=True,
                 caption=f"Live screenshot of the {name} page the agent is currently controlling.")
        st.markdown(_agent_strip_html(browser_runs), unsafe_allow_html=True)


def run_search_live(query: str, eval_slot=None, browser_slot=None, comms_slot=None) -> dict:
    """Run a search while streaming the live panels.

    `eval_slot` / `browser_slot` are the two side-by-side engine containers and
    `comms_slot` is the full-width Agent Communication feed. During the run we poll
    the diagnostics + browser tracker + agent bus every tick and stream all three:
    the checklist fills in stage by stage, the platform tabs show each step the
    agents take, and the communication feed shows every message agents send each
    other (requests to the LLM, the plan, dispatches, results, diagnoses)."""
    tracker = _get_tracker()
    tracker.start()
    holder: dict = {}

    def _worker():
        try:
            holder["data"] = call_api(query)
        except Exception as e:  # surfaced after the loop
            holder["error"] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    # Full-width computer-use stage, created at the TOP LEVEL (outside st.status, which
    # would otherwise narrow it). This is where the live browser screen streams.
    stage_slot = st.empty()
    with st.status("Working on your search…", expanded=True) as status:
        slot = st.empty()
        from backend.progress import is_cancelled as _is_cancelled
        while t.is_alive():
            if _is_cancelled():
                break  # New-search/Stop was requested — stop streaming this run
            evs = tracker.snapshot()
            _render_progress(slot, evs)
            bsnap = _get_browser().snapshot()
            if eval_slot is not None and browser_slot is not None:
                try:
                    render_engine_panels(eval_slot, browser_slot,
                                         _get_diag().snapshot(), bsnap, live=True, events=evs)
                except Exception:
                    pass
            if comms_slot is not None:
                try:
                    render_agent_comms(comms_slot, _get_bus().snapshot(), live=True)
                except Exception:
                    pass
            # When a browser tab is actively being controlled, stream its live screen
            # into the full-width stage above, with the agent strip beneath it.
            try:
                shot = _get_browser().latest_screenshot()
                if shot:
                    _render_stage(stage_slot, shot, bsnap)
            except Exception:
                pass
            time.sleep(0.4)
        t.join(timeout=1)
        tracker.finish()
        st.session_state["_last_events"] = tracker.snapshot()
        slot.empty()
        if holder.get("error"):
            status.update(label="Search hit a problem", state="error", expanded=False)
        else:
            status.update(label="Search complete ✓", state="complete", expanded=False)
    stage_slot.empty()

    if holder.get("error"):
        raise holder["error"]
    return holder.get("data", {})


def preview_platform_page(url: str) -> str:
    """Open a URL and return a base64 screenshot — so the user SEES the page before
    telling the agent what to do. Runs in a thread (Playwright needs its own loop)."""
    import asyncio
    from backend.tools.browser import screenshot_page
    holder: dict = {}

    def _w():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            holder["b64"] = loop.run_until_complete(screenshot_page(url))
            loop.close()
        except Exception:
            holder["b64"] = ""

    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join(timeout=40)
    return holder.get("b64", "")


def run_browser_fix_live(pid: str, params: dict, intent_type: str, hint: str) -> dict:
    """Run the guided browser fix HEADLESS and stream its live screen INTO the app.

    No separate Chrome window ever opens — the agent's page is shown right here in the
    computer-use stage, so the user never has to leave (or switch back to) the app."""
    from backend.nodes.search import retry_platform_with_hint
    holder: dict = {}

    def _worker():
        try:
            holder["res"] = retry_platform_with_hint(pid, params, intent_type, hint, headed=False)
        except Exception as e:
            holder["err"] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    stage = st.empty()   # full-width, top-level (outside st.status)
    with st.status("Fixing it in-app — watch the agent drive the page…",
                   expanded=True) as status:
        while t.is_alive():
            try:
                bsnap = _get_browser().snapshot()
                shot = _get_browser().latest_screenshot()
                if shot:
                    _render_stage(stage, shot, bsnap)
            except Exception:
                pass
            time.sleep(0.4)
        t.join(timeout=1)
        status.update(label="Fix attempt complete ✓", state="complete", expanded=False)
    stage.empty()

    if holder.get("err"):
        return {"results": [], "error": str(holder["err"])}
    return holder.get("res") or {"results": []}


# ── Render helpers ────────────────────────────────────────────
# Fields never shown as tags — already shown as price, name, booking-type badge, or internal.
# Booking-type fields (room_type, cabin_class, bus_type, …) come from the shared
# BOOKING_TYPE_FIELDS map so the "shown as a badge" set can never drift from what
# the backend groups comparisons on.
_SKIP = {
    "name","car_model","operator","vehicle","title","train_name",
    "price","price_per_night","total_price","price_per_day","fare","rate",
    "url","href","_platform_id","_platform_name","_platform_icon","_price_numeric",
    "_booking_type","description",
    # host/listing analytics (Airbnb/AirDNA) — owner economics, not booking detail
    "daily_rate","occupancy","occupancy_rate","revenue","adr","revpar",
} | {f for fields in _BOOKING_TYPE_FIELDS.values() for f in fields}

# ── Value normalization ───────────────────────────────────────
# Raw extracted data is inconsistent ("02 h 10 m" vs "2h 10m", "6:25 AM" vs
# "23:35", "CHECK INCABIN"). These helpers make every chip read the same way so
# the page looks composed rather than dumped.
_BOOL_TRUE  = {"true", "yes", "1", "included", "free", "available"}
_BOOL_FALSE = {"false", "no", "0", "not included", "unavailable", "n/a", ""}

# field key → (icon, display label). Label shown for booleans; icon prefixes text.
_FIELD_META = {
    "duration":          ("⏱", "Duration"),
    "departure_time":    ("🕑", "Departs"),
    "departure":         ("🕑", "Departs"),
    "arrival":           ("🛬", "Arrives"),
    "airline":           ("✈️", "Airline"),
    "stops":             ("🔁", "Stops"),
    "baggage":           ("🧳", "Baggage"),
    "cabin_class":       ("💺", "Class"),
    "class":             ("💺", "Class"),
    "rating":            ("⭐", "Rating"),
    "wifi":              ("📶", "WiFi"),
    "breakfast":         ("🍳", "Breakfast"),
    "pool":              ("🏊", "Pool"),
    "parking":           ("🅿️", "Parking"),
    "refundable":        ("↩️", "Refundable"),
    "free_cancellation": ("↩️", "Free cancellation"),
    "meal":              ("🍽️", "Meal"),
    "location":          ("📍", "Location"),
    "seller":            ("🏬", "Seller"),
    "warranty":          ("🛡️", "Warranty"),
    "delivery":          ("🚚", "Delivery"),
    "delivery_time":     ("🚚", "Delivery"),
    "emi":               ("💳", "EMI"),
    "veg":               ("🥗", "Veg"),
    "cuisine":           ("🍴", "Cuisine"),
    "offers":            ("🏷️", "Offer"),
    "venue":             ("📍", "Venue"),
    "transmission":      ("⚙️", "Transmission"),
    "fuel":              ("⛽", "Fuel"),
    "bus_type":          ("🚌", "Bus type"),
    "operator":          ("🏢", "Operator"),
    "availability":      ("🎟️", "Availability"),
    "confirmation_chance": ("✅", "Confirm chance"),
    "date":              ("📅", "Date"),
    "departure_date":    ("📅", "Date"),
    "layover":           ("🔁", "Layover"),
    "seat_pitch":        ("📏", "Seat pitch"),
    "refund_policy":     ("↩️", "Refund policy"),
    "departure_terminal": ("🛫", "Terminal"),
    "aircraft_type":     ("✈️", "Aircraft"),
    "meal_included":     ("🍽️", "Meal"),
    "bed_type":          ("🛏️", "Bed type"),
    "room_size":         ("📐", "Room size"),
    "view":              ("🌄", "View"),
    "max_occupancy":     ("👥", "Max occupancy"),
    "cancellation_window": ("↩️", "Cancellation"),
    "check_in_time":     ("🕑", "Check-in"),
    "check_out_time":    ("🕑", "Check-out"),
    "distance_to_landmark": ("📍", "Distance"),
    "brand":             ("🏷️", "Brand"),
    "model_number":      ("🔢", "Model"),
    "key_specs":         ("📋", "Specs"),
    "delivery_estimate": ("🚚", "Delivery"),
    "return_policy":     ("↩️", "Return policy"),
    "seating_type":      ("🪑", "Seating"),
    "dress_code":        ("👔", "Dress code"),
    "opening_hours":     ("🕑", "Hours"),
    "variant":           ("📦", "Variant"),
    "storage":           ("💾", "Storage"),
    "ram":               ("💾", "RAM"),
    "color":             ("🎨", "Color"),
    "size":              ("📏", "Size"),
    "exchange_offer":    ("🔄", "Exchange"),
    # trains
    "train_number":      ("🔢", "Train no."),
    "coach_class":       ("🚃", "Coach"),
    "quota":             ("🎫", "Quota"),
    "boarding_station":  ("📍", "Boarding"),
    "pantry":            ("🍽️", "Pantry"),
    "running_days":      ("📅", "Runs on"),
    # buses
    "boarding_point":    ("📍", "Boarding"),
    "dropping_point":    ("📍", "Drop"),
    "seat_type":         ("🪑", "Seat type"),
    "live_tracking":     ("📡", "Live tracking"),
    # car rental
    "seats":             ("💺", "Seats"),
    "mileage":           ("⛽", "Mileage"),
    "provider":          ("🏢", "Provider"),
    # events / restaurants
    "artist":            ("🎤", "Artist"),
    "age_limit":         ("🔞", "Age limit"),
    "tier":              ("🎟️", "Tier"),
    "price_range":       ("💰", "Price range"),
    "avg_cost_for_two":  ("💰", "Cost for two"),
}


def _as_bool_or_none(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _BOOL_TRUE:
        return True
    if s in _BOOL_FALSE:
        return False
    return None


def _fmt_duration(v) -> str:
    s = str(v).lower()
    h = _re.search(r"(\d+)\s*h", s)
    m = _re.search(r"(\d+)\s*m", s)
    if h or m:
        hh = int(h.group(1)) if h else 0
        mm = int(m.group(1)) if m else 0
    else:
        c = _re.search(r"(\d+):(\d+)", s)
        if not c:
            return str(v).strip()
        hh, mm = int(c.group(1)), int(c.group(2))
    out = (f"{hh}h " if hh else "") + (f"{mm}m" if mm else "")
    return out.strip() or str(v).strip()


def _fmt_time(v) -> str:
    s = str(v).strip()
    m = _re.match(r"(\d{1,2}):(\d{2})\s*(am|pm)?", s, _re.I)
    if not m:
        return s
    hh, mm, ap = int(m.group(1)), m.group(2), (m.group(3) or "").lower()
    if ap == "pm" and hh != 12:
        hh += 12
    if ap == "am" and hh == 12:
        hh = 0
    return f"{hh:02d}:{mm}"


def _fmt_value(key: str, v) -> str:
    if key == "duration":
        return _fmt_duration(v)
    if key in ("departure_time", "departure", "arrival"):
        return _fmt_time(v)
    if key == "stops":
        sv = str(v).strip().lower()
        if sv in ("0", "nonstop", "non-stop", "direct", "non stop"):
            return "Nonstop"
        n = _re.search(r"\d+", sv)
        if n:
            cnt = int(n.group())
            return f"{cnt} stop" + ("s" if cnt != 1 else "")
        return str(v).title()
    s = str(v).strip()
    # De-shout ALL-CAPS values like "CHECK INCABIN"
    if s.isupper() and len(s) > 3:
        s = s.title()
    return s


def _clean_chips(result: dict, limit=5, css="chip") -> str:
    """Render a result's secondary fields as clean iconified chips (no raw
    'key: value' text). Booleans show only when present/true; text shows with an
    icon. Order follows _FIELD_META priority where possible."""
    out = []
    # Preferred field order = meta order, then anything else.
    ordered = [k for k in _FIELD_META if k in result] + \
              [k for k in result if k not in _FIELD_META]
    for k in ordered:
        if k in _SKIP or len(out) >= limit:
            continue
        v = result.get(k)
        if v is None or str(v).strip() == "":
            continue
        icon, label = _FIELD_META.get(k, ("", k.replace("_", " ").title()))
        b = _as_bool_or_none(v)
        if b is True:
            out.append(f'<span class="{css}"><span class="chip-i">{icon}</span>{label}</span>')
        elif b is False:
            continue  # don't clutter with absent amenities in compact chips
        else:
            val = _fmt_value(k, v)
            ic = f'<span class="chip-i">{icon}</span>' if icon else ""
            out.append(f'<span class="{css}">{ic}{val}</span>')
    return "".join(out)


def _extra_tags(result: dict, limit=6) -> str:
    return _clean_chips(result, limit=limit, css="chip")

def _bp_tags(result: dict, limit=7) -> str:
    return _clean_chips(result, limit=limit, css="chip chip-on-light")

_PRICE_FIELDS = ["price", "price_per_night", "total_price", "price_per_day", "fare", "rate"]
_NAME_FIELDS  = ["name", "car_model", "train_name", "title", "operator", "vehicle"]

_CURRENCY_RE = {
    "$": 83, "usd": 83, "€": 90, "eur": 90,
    "£": 105, "gbp": 105, "sgd": 62, "aed": 22,
}

# Absolute minimum plausible INR price per category (for sanity conversion)
_DOMAIN_MIN_INR = {
    "hotel": 250, "flight": 400, "event": 30,
    "car_rental": 100, "train": 20, "bus": 20,
    "restaurant": 15, "product": 5,
}

def _price(r, intent_type: str = "general") -> str:
    """Return price as a clean ₹ INR integer string. Converts foreign currencies."""
    if not r or not isinstance(r, dict):
        return ""
    import re as _re2
    for f in _PRICE_FIELDS:
        v = r.get(f)
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue

        # Detect currency in the stored value string (catches "$9.39" stored as string)
        rate = 1
        s_lower = s.lower()
        for sym, fx in _CURRENCY_RE.items():
            if sym in s_lower:
                rate = fx
                break

        # Extract numeric part
        nums = _re2.findall(r"\d[\d,]*\.?\d*", s)
        if not nums:
            continue
        try:
            val = float(nums[0].replace(",", ""))
        except ValueError:
            continue

        # Apply detected FX rate
        inr = val * rate

        # Sanity: if still absurdly low for domain, assume USD
        min_inr = _DOMAIN_MIN_INR.get(intent_type, 5)
        if 0 < inr < min_inr:
            inr = inr * 83

        return str(int(round(inr)))
    return ""

def _price_label(r) -> str:
    """Return the label for the price field (e.g. '/day', '/night')."""
    if not r or not isinstance(r, dict):
        return ""
    if r.get("price_per_day"): return "/day"
    if r.get("price_per_night"): return "/night"
    return ""

def _name(r, fallback="—") -> str:
    if not r or not isinstance(r, dict):
        return fallback
    for f in _NAME_FIELDS:
        v = r.get(f)
        if v and str(v).strip() and not str(v).lower().startswith(("option","result","listing")):
            return str(v)
    # Build a descriptive fallback from available fields instead of "Result N"
    parts = []
    for f in ("category", "car_type", "airline", "bus_type", "class", "type", "vehicle_type"):
        v = r.get(f)
        if v and str(v).strip():
            parts.append(str(v))
    if parts:
        return " · ".join(parts[:2])
    return fallback


def render_result_row(r: dict, idx: int, platform_website: str = "",
                      platform_name: str = "", intent_type: str = "general"):
    name        = _name(r, f"Result {idx+1}")
    price       = _price(r, intent_type)
    plabel      = _price_label(r)
    btype       = _booking_type(r, intent_type)
    direct_url  = r.get("url") or r.get("href") or ""
    link_target = (direct_url if direct_url.startswith("http") else "") or platform_website
    is_direct   = bool(direct_url and direct_url.startswith("http"))
    btn_label   = (f"View on {platform_name}" if is_direct
                   else f"Search on {platform_name}" if platform_name
                   else "Visit →")
    price_html  = f'<span class="rrow-price">₹{price}<span style="font-size:0.65rem;font-weight:500;color:#9A7E58;">{plabel}</span></span>' if price else ""
    type_html   = f'<span class="rrow-type">{btype}</span>' if btype else ""
    view_html   = f'<a class="rrow-view" href="{link_target}" target="_blank">{btn_label} →</a>' if link_target else ""
    st.markdown(
        f'<div class="rrow">'
          f'<div class="rrow-top">'
            f'<div class="rrow-name-group">'
              f'<span class="rrow-name" title="{name}">{name}</span>'
              f'{type_html}'
            f'</div>'
            f'{price_html}'
          f'</div>'
          f'<div class="rrow-tags">{_extra_tags(r)}</div>'
          f'{view_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_platform_card(pid: str, data: dict, is_winner: bool, platform_website: str = "",
                         intent_type: str = "general", badges: list = None, value_score=None):
    name    = data.get("platform_name", pid)
    icon    = data.get("icon", "🔍")
    results = data.get("results", [])
    error   = data.get("error")
    elapsed = data.get("elapsed_seconds", 0)

    best_chip   = '<span class="pcard-best-chip">★ best</span>' if is_winner else ""
    rel_html    = ""
    try:
        rel = _get_reliability(pid)
        badge = _RELIABILITY_BADGE.get(rel.get("label", "unknown"))
        if badge:
            text, css_class = badge
            rel_html = f'<span class="pcard-reliability {css_class}" title="Based on {rel["samples"]} recent searches on this platform">{text}</span>'
    except Exception:
        pass
    err_note    = f'<div class="pcard-err">⚠ {error}</div>' if (error and not results) else ""
    empty       = '<div class="rrow-empty">No verified results — could not confirm data from this platform.</div>' if not results and not error else ""
    site_link   = (f'<a href="{platform_website}" target="_blank" class="rrow-link" '
                   f'style="font-size:0.7rem;">Visit {name} →</a>') if platform_website else ""

    # Smart badges + value-score meter
    badges_html = ""
    if badges:
        badges_html = '<div class="pcard-badges">' + "".join(
            f'<span class="pcard-badge">{b}</span>' for b in badges
        ) + "</div>"
    score_html = ""
    if value_score is not None:
        score_html = (
            f'<div class="value-meter"><div class="value-meter-bar" '
            f'style="width:{value_score}%"></div></div>'
            f'<div class="value-meter-label">Value score {value_score}/100</div>'
        )

    st.markdown(
        f'<div class="pcard {"best" if is_winner else ""}">'
          f'<div class="pcard-head">'
            f'<span class="pcard-name">{icon} {name} {best_chip}</span>'
            f'<span class="pcard-meta">{rel_html}{len(results)} result{"s" if len(results)!=1 else ""}</span>'
          f'</div>'
          f'{badges_html}{score_html}'
          f'{err_note}{empty}',
        unsafe_allow_html=True,
    )

    for i, r in enumerate(results[:4]):
        render_result_row(r, i, platform_website, name, intent_type)

    if platform_website:
        st.markdown(site_link, unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


def render_segments(segments: dict, intent_type: str = "general", intent_params: dict = None):
    """Render results separated into type-groups (room type / cabin class / …),
    each compared side-by-side across platforms with prices + amenities."""
    intent_params = intent_params or {}
    if not segments or not segments.get("available"):
        return
    groups = segments.get("groups", [])
    if not groups:
        return
    label = segments.get("group_label", "Type")

    st.markdown(
        f'<div class="section-head" style="margin-top:0.4rem;">'
        f'Compared by {label}</div>'
        f'<div class="seg-note">Results separated by {label.lower()} so you compare '
        f'like-for-like across every platform — with amenities side by side.</div>',
        unsafe_allow_html=True,
    )

    for g in groups:
        gtype   = str(g.get("type", "")).replace("<", "&lt;")
        total   = g.get("total", 0)
        rows    = g.get("rows", [])
        summary = str(g.get("summary", "")).replace("<", "&lt;")
        cheap_p = g.get("cheapest_platform")
        cheap_v = g.get("cheapest_price")

        cheap_chip = (f'<span class="seg-cheap">cheapest ₹{cheap_v} · {cheap_p}</span>'
                      if cheap_v is not None else "")
        head = (
            f'<div class="seg-ghead">'
            f'<span class="seg-gtype">{gtype}</span>'
            f'<span class="seg-gcount">{total} option{"s" if total != 1 else ""} · '
            f'{len(rows)} platform{"s" if len(rows) != 1 else ""}</span>'
            f'{cheap_chip}</div>'
        )
        summary_html = f'<div class="seg-summary">💡 {summary}</div>' if summary else ""

        # One card per platform in this group.
        cards = ""
        best_pid = None
        if cheap_v is not None:
            for r in rows:
                if r.get("price") == cheap_v and r.get("platform_name") == cheap_p:
                    best_pid = r.get("platform_id")
                    break
        for r in rows:
            is_best = (r.get("platform_id") == best_pid)
            price   = r.get("price")
            price_html = (f'<span class="seg-price">₹{price}</span>'
                          if price is not None else '<span class="seg-price seg-na">—</span>')
            name = str(r.get("name", "")).replace("<", "&lt;")[:40]
            # Prefer the listing's own page; if it has none (common for flights —
            # there's no stable per-flight permalink), fall back to a pre-filled
            # search/results deep-link for that platform+route+date, NOT the bare
            # homepage. Label reflects which one the user is about to open.
            url    = r.get("url") or ""
            direct = url.startswith("http")
            if not direct:
                url = _build_deep_link(r.get("platform_id", ""), intent_params)
            link = (f'<a class="seg-link" href="{url}" target="_blank">'
                    f'{"view" if direct else "search"} →</a>'
                    if url.startswith("http") else "")

            # Amenity chips: ✓ green / ✗ muted for booleans, plain chip for text.
            am_html = ""
            for a in r.get("amenities", [])[:6]:
                lbl = str(a.get("label", "")).replace("<", "&lt;")
                if a.get("type") == "bool":
                    if a.get("value") is True:
                        am_html += f'<span class="seg-am seg-am-yes">✓ {lbl}</span>'
                    else:
                        am_html += f'<span class="seg-am seg-am-no">✗ {lbl}</span>'
                else:
                    val = _fmt_value(a.get("key", ""), a.get("value", ""))
                    val = str(val).replace("<", "&lt;")[:22]
                    am_html += f'<span class="seg-am seg-am-txt">{lbl}: {val}</span>'
            if not am_html:
                am_html = '<span class="seg-am seg-am-txt" style="opacity:.5;">no amenity data</span>'

            cards += (
                f'<div class="seg-card{" seg-card-best" if is_best else ""}">'
                f'<div class="seg-card-top">'
                f'<span class="seg-plat">{r.get("icon","🔍")} {r.get("platform_name","")}'
                f'{" ★" if is_best else ""}</span>{price_html}</div>'
                f'<div class="seg-cardname" title="{name}">{name}</div>'
                f'<div class="seg-ams">{am_html}</div>'
                f'{link}'
                f'</div>'
            )

        st.markdown(
            f'<div class="seg-group">{head}{summary_html}'
            f'<div class="seg-cards">{cards}</div></div>',
            unsafe_allow_html=True,
        )


def render_best_pick_banner(rec: dict, ranked: list, intent_type: str = "general",
                            intent_params: dict = None):
    if not rec:
        return

    pid    = rec.get("winner_platform") or ""
    winner = rec.get("winner_result") or {}

    # Don't render if no real winner found
    if not pid or pid.lower() in ("", "none", "null"):
        st.markdown("""
        <div style="background:#F7F1E4;border:1.5px solid #EADFCB;border-radius:14px;
             padding:1.2rem 1.4rem;color:#9A7E58;font-size:0.85rem;text-align:center;">
          No recommendation available — platforms returned no comparable results.
        </div>""", unsafe_allow_html=True)
        return

    reasoning  = (rec.get("reasoning") or "").replace("<","&lt;").replace(">","&gt;")
    price_note = (rec.get("price_analysis") or "").replace("<","&lt;").replace(">","&gt;")
    confidence = rec.get("confidence") or "medium"
    tips       = (rec.get("tips") or "").replace("<","&lt;").replace(">","&gt;")
    alts       = rec.get("alternatives") or []

    pname = next((p["platform_name"] for p in ranked if p["platform_id"]==pid), pid)
    picon = next((p["icon"]          for p in ranked if p["platform_id"]==pid), "🏆")
    price = _price(winner, intent_type)
    name  = _name(winner, "Best Option")
    url   = (winner.get("url") or "") if isinstance(winner, dict) else ""

    # If the winning listing has no direct page, send "Book Now" to the platform's
    # pre-filled search/results endpoint for this route/date — not the homepage.
    is_direct = url.startswith("http")
    if not is_direct:
        url = _build_deep_link(pid, intent_params or {})
    conf_cls  = {"high":"conf-high","medium":"conf-medium","low":"conf-low"}.get(confidence,"conf-medium")
    book_label = "Book Now" if is_direct else "Search & Book"
    book_html = f'<a class="bp-book" href="{url}" target="_blank">{book_label} →</a>' if url.startswith("http") else ""
    tips_html = f'<div style="font-size:0.78rem;color:#6B5338;margin-top:0.5rem;">💡 {tips}</div>' if tips else ""

    # Build alts as separate lines — avoid nesting complex HTML in f-string
    alts_lines = ""
    for a in (alts or [])[:2]:
        ap = str(a.get("platform","")).replace("<","&lt;")
        aw = str(a.get("why","")).replace("<","&lt;")
        alts_lines += f'<div style="font-size:0.76rem;color:#6B5338;padding:3px 0;"><span style="color:#9A7E58;font-weight:600;">{ap}</span> — {aw}</div>'

    alts_html = ""
    if alts_lines:
        alts_html = (
            '<div style="margin-top:0.75rem;border-top:1px solid #2A1A0A;padding-top:0.75rem;">'
            '<div style="font-size:0.65rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
            'color:#4A3623;margin-bottom:4px;">Also consider</div>'
            + alts_lines + "</div>"
        )

    st.markdown(
        '<div class="bp-banner">'
          '<div class="bp-left">'
            '<div class="bp-eyebrow">⬡ Best Pick</div>'
            f'<div class="bp-platform">{picon} {pname}</div>'
            f'<div class="bp-name">{name}</div>'
            f'<div class="bp-tags">{_bp_tags(winner)}</div>'
            f'<div class="bp-reasoning">{reasoning}</div>'
            f'{tips_html}'
            f'{alts_html}'
          '</div>'
          '<div class="bp-right">'
            f'<div class="bp-price">{"₹"+price if price else "—"}</div>'
            f'<div><span class="bp-conf {conf_cls}">{confidence} confidence</span></div>'
            f'<div style="margin-top:0.75rem;">{book_html}</div>'
            f'<div style="font-size:0.72rem;color:#4A3623;margin-top:0.5rem;max-width:180px;">{price_note}</div>'
          '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _fmt_cell(cell: dict, dtype: str) -> str:
    """Format one comparison-matrix cell for display."""
    raw = cell.get("raw")
    if raw is None or str(raw).strip() == "":
        return '<span class="cmx-na">—</span>'
    if dtype == "bool":
        comp = cell.get("comparable")
        if comp is True:
            return '<span class="cmx-yes">✓</span>'
        if comp is False:
            return '<span class="cmx-no">✗</span>'
        return '<span class="cmx-na">—</span>'
    if dtype == "price":
        n = cell.get("comparable")
        return f"₹{int(n)}" if n else str(raw)
    if dtype == "rating":
        n = cell.get("comparable")
        return f"{n}★" if n else str(raw)
    if dtype == "duration":
        return _fmt_duration(raw)
    s = str(raw)
    if s.isupper() and len(s) > 3:
        s = s.title()
    return s if len(s) <= 22 else s[:20] + "…"


def render_insights(insights: dict):
    """Render key takeaways + the cross-platform comparison matrix."""
    if not insights or not insights.get("available"):
        return

    takeaways = insights.get("takeaways", [])
    verdict   = insights.get("verdict", "")

    # ── Key takeaways panel ──
    if takeaways or verdict:
        items = "".join(
            f'<div class="ti-item"><span class="ti-dot">▸</span>{t}</div>'
            for t in takeaways[:5]
        )
        verdict_html = (
            f'<div class="ti-verdict"><span class="ti-verdict-label">Bottom line</span> {verdict}</div>'
            if verdict else ""
        )
        st.markdown(
            f'<div class="ti-panel">'
              f'<div class="ti-head">🧠 AI Comparison Insights</div>'
              f'{items}{verdict_html}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Comparison matrix ──
    dims      = insights.get("dimensions", [])
    matrix    = insights.get("matrix", {})
    order     = insights.get("platforms_order", [])
    names     = insights.get("platform_names", {})
    icons     = insights.get("platform_icons", {})
    winners   = insights.get("dimension_winners", {})

    if not dims or not order:
        return

    # Header row
    head = '<th class="cmx-dim-head">Factor</th>' + "".join(
        f'<th class="cmx-plat-head">{icons.get(p,"")} {names.get(p,p)}</th>'
        for p in order
    )

    # Body rows — one per dimension
    rows = ""
    for dim in dims:
        key, label, dtype = dim["key"], dim["label"], dim["type"]
        row_cells = f'<td class="cmx-dim">{label}</td>'
        win_pid = winners.get(key)
        # Skip dimensions with no data at all
        dim_row = matrix.get(key, {})
        if not any(c.get("raw") not in (None, "") for c in dim_row.values()):
            continue
        for p in order:
            cell = dim_row.get(p, {})
            is_win = (p == win_pid)
            cls = "cmx-cell cmx-win" if is_win else "cmx-cell"
            trophy = '<span class="cmx-trophy">🏆</span>' if is_win else ""
            row_cells += f'<td class="{cls}">{_fmt_cell(cell, dtype)}{trophy}</td>'
        rows += f'<tr>{row_cells}</tr>'

    st.markdown(
        f'<div class="cmx-wrap">'
          f'<div class="section-head" style="margin-bottom:0.6rem;">Side-by-Side Comparison</div>'
          f'<table class="cmx-table"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Roadblocks — human-in-the-loop recovery ───────────────────
_TIER_LABEL = {
    "cache": "⚡ Cached (recent search)",
    "tavily": "Tavily (fast web)", "tavily-open": "Tavily (web)",
    "scrape": "Direct scrape", "selenium": "Selenium scrape",
    "browser-use": "Live browser",
    "universal": "Form automation",
    "google": "Google", "ddg": "DuckDuckGo", "": "—",
}


def render_roadblocks(platform_results: dict, intent_type: str, intent_params: dict):
    """Show platforms that came back empty, explain why, and let the user guide a
    retry with a hint ('click Search', 'open the Flights tab', a direct URL).

    This is the 'I don't want it to feel like the app failed' surface: every dead
    end becomes an explanation + a next step the user can take, not a blank."""
    stuck = [(pid, r) for pid, r in platform_results.items()
             if not r.get("results") and r.get("roadblock")]
    if not stuck:
        return

    st.markdown(
        f'<div class="section-head" style="margin-top:0.6rem;">🚧 {len(stuck)} '
        f'roadblock{"s" if len(stuck)!=1 else ""} — your input needed</div>'
        f'<div class="seg-note">These platforms got stuck (the breakpoints above). Tell the browser the exact step '
        f'click <b>Open &amp; show me the page</b> — the agent opens the site and shows it to you, '
        f'then you tell it the exact step to take (e.g. <i>"click the green Search button"</i>) and '
        f'watch it finish and bring the results back.</div>',
        unsafe_allow_html=True,
    )

    for pid, r in stuck:
        rb    = r.get("roadblock") or {}
        name  = r.get("platform_name", pid)
        icon  = r.get("icon", "🔍")
        rec   = st.session_state.get(f"_recovered_{pid}")  # a prior successful retry this session

        with st.container(border=True):
            st.markdown(f"**{icon} {name}**")
            st.markdown(
                f'<div style="font-size:0.82rem;color:#6B5338;">{rb.get("reason","")}</div>'
                f'<div style="font-size:0.8rem;color:#0f766e;margin-top:4px;">💡 {rb.get("suggestion","")}</div>',
                unsafe_allow_html=True,
            )
            # The exact error/blocker, shown verbatim so the user knows what to address.
            specific = rb.get("error")
            if specific:
                st.markdown(
                    f'<div style="font-size:0.74rem;color:#991b1b;background:#fef2f2;'
                    f'border:1px solid #fecaca;border-radius:6px;padding:6px 8px;margin-top:6px;">'
                    f'<b>Exact error:</b> <span style="font-family:monospace;">'
                    f'{str(specific)[:240].replace("<","&lt;")}</span></div>',
                    unsafe_allow_html=True,
                )
            # Monitor agent's diagnosis — what it thinks went wrong + its proposed fix.
            analysis = rb.get("analysis") or {}
            if analysis:
                st.markdown(
                    f'<div style="font-size:0.76rem;background:#FBEEDD;border:1px solid #EAD3AC;'
                    f'border-radius:8px;padding:8px 10px;margin-top:6px;color:#3730a3;">'
                    f'<b>🩺 Monitor agent</b> · <span style="color:#C05800;">{analysis.get("category","")}</span><br>'
                    f'<span style="color:#9A3B00;">{str(analysis.get("diagnosis","")).replace("<","&lt;")}</span></div>',
                    unsafe_allow_html=True,
                )
            if rec:
                # A retry already worked — show the recovered listings inline.
                st.success(f"Recovered {len(rec)} result(s) with your hint:")
                for item in rec[:5]:
                    nm = _name(item, "Result")
                    pr = _price(item, intent_type)
                    st.markdown(f"- **{nm}** {'· ₹'+pr if pr else ''}")
            elif st.session_state.get(f"_preview_{pid}"):
                # ── Phase 2: the page is open and shown — now ask what to do ──
                preview_b64 = st.session_state[f"_preview_{pid}"]
                try:
                    st.image(base64.b64decode(preview_b64), use_container_width=True,
                             caption=f"This is {name} right now. Tell the agent what to do on it.")
                except Exception:
                    pass
                suggested = (analysis.get("suggested_hint") or "") if analysis else ""
                if suggested and f"hint_{pid}" not in st.session_state:
                    st.session_state[f"hint_{pid}"] = suggested
                hint = st.text_input(
                    f"What should the agent do on {name}?", key=f"hint_{pid}",
                    placeholder="e.g. click the green Search button, then read the first 5 results",
                )
                run_col, cancel_col = st.columns([3, 1])
                with run_col:
                    do_run = st.button(f"▶ Do this on {name} (watch here)", key=f"retry_{pid}", type="primary")
                with cancel_col:
                    if st.button("Cancel", key=f"cancel_{pid}"):
                        st.session_state.pop(f"_preview_{pid}", None)
                        st.rerun()
                if do_run:
                    new_r = run_browser_fix_live(pid, intent_params, intent_type, hint)
                    got = new_r.get("results") or []
                    st.session_state["last_result"]["platform_results"][pid] = new_r
                    try:
                        st.session_state["last_result"]["browser_runs"] = _get_browser().snapshot()
                    except Exception:
                        pass
                    st.session_state.pop(f"_preview_{pid}", None)
                    if got:
                        st.session_state[f"_recovered_{pid}"] = got
                        st.rerun()
                    else:
                        st.warning(f"Still no luck on {name}. "
                                   f"{(new_r.get('roadblock') or {}).get('suggestion','')}")
            else:
                # ── Phase 1: open the website and show it, THEN we ask for input ──
                if st.button(f"👁 Open {name} & show me the page", key=f"open_{pid}", type="primary"):
                    # Avoid the malformed deep link (empty params → "site can't be reached").
                    _deep = _build_deep_link(pid, intent_params)
                    _web = _PLATFORM_WEBSITES.get(pid, "")
                    _bad = (not _deep) or ("=&" in _deep) or _deep.rstrip().endswith("=")
                    url = _web if (_bad and _web) else (_deep or _web)
                    if not url:
                        st.warning("No link available for this platform.")
                    else:
                        with st.spinner(f"Opening {name} so you can see it…"):
                            b64 = preview_platform_page(url)
                        if b64:
                            st.session_state[f"_preview_{pid}"] = b64
                            st.rerun()
                        else:
                            st.warning(f"Couldn't load {name} to show it. You can still open it "
                                       f"directly from its link in the results below.")

    if any(st.session_state.get(f"_recovered_{pid}") for pid, _ in stuck):
        st.caption("Recovered results show above. Re-run the full search to fold them "
                   "into the comparison and recommendation.")


# ── Diagnostics — where did the time go? ──────────────────────
def render_diagnostics(diagnostics: dict):
    """Performance panel: total time, time per pipeline stage, and per-platform
    timing + which search tier ran. Answers 'what's slow?' at a glance."""
    if not diagnostics:
        return
    total   = diagnostics.get("total_seconds", 0)
    nodes   = diagnostics.get("nodes", [])
    plats   = diagnostics.get("platforms", [])
    slow_n  = diagnostics.get("slowest_node") or {}
    slow_p  = diagnostics.get("slowest_platform") or {}

    with st.expander(f"⚙️ Performance & diagnostics — {total:.1f}s total", expanded=False):
        if slow_n or slow_p:
            bits = []
            if slow_n:
                bits.append(f"slowest stage: **{slow_n.get('name')}** ({slow_n.get('seconds')}s)")
            if slow_p:
                bits.append(f"slowest platform: **{slow_p.get('platform_name')}** ({slow_p.get('elapsed')}s)")
            st.markdown("· ".join(bits))

        # Per-stage timing bars
        if nodes:
            st.markdown("**Pipeline stages**")
            mx = max((n["seconds"] for n in nodes), default=1) or 1
            for n in nodes:
                pct = int(100 * n["seconds"] / mx)
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;font-size:0.78rem;margin:2px 0;">'
                    f'<span style="width:130px;color:#6B5338;">{n["name"]}</span>'
                    f'<span style="flex:1;background:#F1E8D8;border-radius:6px;overflow:hidden;height:14px;">'
                    f'<span style="display:block;height:100%;width:{pct}%;background:#C05800;"></span></span>'
                    f'<span style="width:54px;text-align:right;color:#2A1A0A;">{n["seconds"]}s</span>'
                    f'</div>', unsafe_allow_html=True,
                )

        # Per-platform table: tier used + timing + outcome
        if plats:
            st.markdown("**Per platform**")
            rows = ""
            for p in sorted(plats, key=lambda x: -x.get("elapsed", 0)):
                tier = _TIER_LABEL.get(p.get("tier", ""), p.get("tier") or "—")
                n = p.get("n_results", 0)
                status = (f'<span style="color:#16a34a;">✓ {n}</span>' if n
                          else '<span style="color:#dc2626;">0</span>')
                legs = " → ".join(
                    f'{_TIER_LABEL.get(t["tier"], t["tier"]).split(" ")[0]} {t["seconds"]}s'
                    for t in p.get("tiers_tried", [])
                )
                rows += (
                    f'<tr><td style="padding:3px 8px;">{p.get("platform_name")}</td>'
                    f'<td style="padding:3px 8px;">{status}</td>'
                    f'<td style="padding:3px 8px;color:#6B5338;">{tier}</td>'
                    f'<td style="padding:3px 8px;text-align:right;">{p.get("elapsed")}s</td>'
                    f'<td style="padding:3px 8px;color:#9A7E58;font-size:0.72rem;">{legs}</td></tr>'
                )
            st.markdown(
                '<table style="width:100%;font-size:0.78rem;border-collapse:collapse;">'
                '<thead><tr style="text-align:left;color:#9A7E58;font-size:0.7rem;">'
                '<th style="padding:3px 8px;">Platform</th><th style="padding:3px 8px;">Results</th>'
                '<th style="padding:3px 8px;">Tier used</th><th style="padding:3px 8px;text-align:right;">Time</th>'
                '<th style="padding:3px 8px;">Legs tried</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>',
                unsafe_allow_html=True,
            )


def _flatten_rows(rows: list) -> list:
    """Cypher rows can come back as {'r': {full node dict}} (RETURN r) or already-flat
    columns (RETURN r.name, r.price). Flatten the former so st.dataframe renders a
    clean table either way."""
    out = []
    for row in rows:
        if len(row) == 1:
            only = next(iter(row.values()))
            out.append(only if isinstance(only, dict) else row)
        else:
            out.append(row)
    return out


# ── Main ──────────────────────────────────────────────────────
def _reset_search_state():
    """Wipe everything tied to the current/last search so a brand-new one starts clean.
    Fixes the 'the cache won't let me start a new search' problem and backs the
    New-search/Stop button."""
    for k in ("last_result", "active_query", "_run_search_now", "_chat_refined_search",
              "chat_history", "_chat_seeded_run", "_search_run_id", "_last_events",
              "search_query"):
        st.session_state.pop(k, None)


def main():

    # ── Landing page ──
    # First visit shows the animated homepage (frontend/landing.py). "Launch app"
    # links reload the top window with ?app=1, which flips us into the search UI
    # for the rest of the session.
    if "home" in st.query_params:          # topbar "Home" → back to landing
        st.session_state.pop("_entered_app", None)
        st.query_params.clear()
    if "app" in st.query_params:
        st.session_state["_entered_app"] = True
    if not st.session_state.get("_entered_app"):
        from frontend.landing import render_landing
        render_landing()
        st.stop()

    # ── Access gate ──
    # Require Google (Gmail) sign-in before anything else renders. Returns the
    # signed-in user (or {} when auth isn't configured yet / open mode). If the
    # visitor isn't allowed, require_login() renders the sign-in screen and
    # halts here, so nothing below runs for an anonymous user.
    user = require_login()

    # ═══ SIDEBAR — navigation, recents, workspace tools (Claude-app-style shell) ═══
    with st.sidebar:
        st.markdown("""
        <div class="sb-logo"><span class="sb-dot"></span> Agent‑Aware</div>
        <a class="sb-home" href="/?home=1" target="_self">← Back to homepage</a>
        """, unsafe_allow_html=True)

        if st.button("＋  New search", key="sb_new", use_container_width=True):
            try:
                from backend.progress import request_cancel
                request_cancel()
            except Exception:
                pass
            _reset_search_state()
            st.rerun()

        # Recent searches — this session's history, newest first, click to re-run
        st.markdown('<div class="sb-cap">Recents</div>', unsafe_allow_html=True)
        recents = st.session_state.get("_recent_searches", [])
        if not recents:
            st.markdown('<div class="sb-empty">Your searches will appear here.</div>',
                        unsafe_allow_html=True)
        for i, rq in enumerate(recents[:8]):
            if st.button(f"🕘 {rq[:34]}{'…' if len(rq) > 34 else ''}",
                         key=f"sb_recent_{i}", use_container_width=True):
                _reset_search_state()
                st.session_state["active_query"] = rq
                st.session_state["_run_search_now"] = True
                st.rerun()

        # Quick starts
        st.markdown('<div class="sb-cap">Try one</div>', unsafe_allow_html=True)
        examples = ["✈ Flights Mumbai → Delhi", "🏨 Hotels in Manali", "🎬 Coldplay India 2025",
                    "🍕 Pizza near Bangalore", "📱 iPhone 15 price", "🚂 Delhi–Agra trains"]
        for i, ex in enumerate(examples):
            if st.button(ex, key=f"chip_{i}", use_container_width=True):
                _reset_search_state()
                st.session_state["active_query"] = ex.split(" ", 1)[1].strip()
                st.session_state["_run_search_now"] = True
                st.rerun()

        # Workspace extras live down here, out of the main flow
        st.markdown('<div class="sb-cap">Workspace</div>', unsafe_allow_html=True)
        render_slack_panel()
        render_user_chip(user)

    # ── Slim topbar ──
    st.markdown("""
    <div class="topbar">
      <div class="topbar-logo"><div class="topbar-logo-dot"></div> Agent-Aware</div>
      <span class="topbar-badge">multi-agent search</span>
    </div>
    """, unsafe_allow_html=True)

    data_exists = bool(st.session_state.get("last_result"))

    # ── Composer — hero-sized on the empty page, compact once results exist ──
    if not data_exists:
        st.markdown("""
        <div class="search-section">
          <h1 class="search-headline">Where should the agents look?</h1>
          <p class="search-sub">One question — flights, hotels, gadgets, gigs — searched everywhere at once.</p>
        </div>
        """, unsafe_allow_html=True)

    with st.form(key="search_form", clear_on_submit=False, border=False):
        q_col, btn_col = st.columns([5, 1])
        with q_col:
            query = st.text_input(
                # Descriptive label — visually hidden but read aloud by screen readers.
                "Search for flights, hotels, products, trains, and more",
                placeholder='Try: "flights Delhi to Goa this Friday under ₹5000"',
                label_visibility="collapsed",
                key="search_query",
            )
        with btn_col:
            st.markdown('<div class="search-btn-col">', unsafe_allow_html=True)
            search_clicked = st.form_submit_button("Search →", type="primary")
            st.markdown("</div>", unsafe_allow_html=True)

    # ── Trigger ──
    # active_query is set programmatically (sidebar chips, clarification answers)
    # search_query is owned by the widget — never write to it directly
    widget_query = st.session_state.get("search_query", "").strip()
    just_set = st.session_state.pop("_run_search_now", False)
    if just_set:
        active = (st.session_state.get("active_query", "") or widget_query).strip()
    else:
        active = widget_query or st.session_state.get("active_query", "").strip()

    if (search_clicked or just_set) and active:
        # A search triggered by the chat ("show only non-stop", etc.) keeps the
        # conversation going — only a brand-new typed/clicked search wipes the chat.
        chat_refine = st.session_state.pop("_chat_refined_search", False)
        st.session_state.pop("last_result", None)
        if not chat_refine:
            st.session_state.pop("chat_history", None)
            st.session_state.pop("_chat_seeded_run", None)

        # ── LIVE RUN VIEW — full-width mission control while agents work.
        # Engine checklist + live browser side by side, comms feed streaming below.
        # Once the run finishes we st.rerun() so the page reflows into the
        # organized answer + tabs layout.
        st.markdown('<div class="live-cap">⚡ Agents working — live</div>',
                    unsafe_allow_html=True)
        _ep_left, _ep_right = st.columns(2)
        _eval_slot = _ep_left.empty()
        _browser_slot = _ep_right.empty()
        _comms_slot = st.empty()

        st.session_state["last_result"] = run_search_live(active, _eval_slot, _browser_slot, _comms_slot)
        st.session_state["active_query"] = active
        st.session_state["_search_run_id"] = st.session_state.get("_search_run_id", 0) + 1
        # Session history for the sidebar (dedup, newest first)
        rec = st.session_state.setdefault("_recent_searches", [])
        if active in rec:
            rec.remove(active)
        rec.insert(0, active)
        del rec[12:]
        st.rerun()

    # ── Results ──
    data = st.session_state.get("last_result")

    if not data:
        st.markdown("""
        <div class="empty-state">
          <div class="empty-icon">🔍</div>
          <div class="empty-title">Start with a search above</div>
          <div class="empty-sub">Flights · Hotels · Events · Restaurants · Products · Trains · Buses</div>
        </div>""", unsafe_allow_html=True)
        return

    if data.get("error"):
        raw_err = str(data["error"])
        if "rate_limit" in raw_err or "429" in raw_err:
            # Surface the AI provider's daily-quota limit as a plain, friendly notice
            # instead of dumping the raw JSON error body from the API.
            wait_match = _re.search(r"try again in ([0-9hms.]+)", raw_err)
            wait_for = wait_match.group(1) if wait_match else "a few minutes"
            st.markdown(f"""<div style="background:#FFF7E6;border:1.5px solid #fcd34d;
              border-radius:12px;padding:1.1rem 1.3rem;color:#92400e;font-size:0.9rem;line-height:1.5;">
              ⏳ <b>We've hit today's AI usage limit.</b><br/>
              <span style="color:#b45309;font-size:0.85rem;">
              Our search assistant has used up its daily quota of AI requests. It refills on its own —
              try again in about <b>{wait_for}</b>.
              </span></div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""<div style="background:#FEF2F2;border:1.5px solid #fca5a5;
              border-radius:12px;padding:1rem 1.2rem;color:#b91c1c;font-size:0.85rem;">
              ⚠ Something went wrong while searching. Please try again in a moment.</div>""",
              unsafe_allow_html=True)
        return

    # ── No results — the pipeline short-circuits here, so NO comparison/insights/
    # recommendation were computed on empty data. Show a clean message + how to retry. ──
    _tot = sum(len((r or {}).get("results") or [])
               for r in (data.get("platform_results") or {}).values() if isinstance(r, dict))
    if data.get("status") == "no_results" or _tot == 0:
        names = ", ".join((r or {}).get("platform_name", pid)
                          for pid, r in (data.get("platform_results") or {}).items()) or "the platforms"
        st.markdown(f"""<div style="background:#fffbeb;border:1.5px solid #fcd34d;border-radius:14px;
          padding:1.3rem 1.5rem;color:#92400e;">
          <div style="font-size:1rem;font-weight:700;margin-bottom:4px;">No results found</div>
          <div style="font-size:0.86rem;line-height:1.55;color:#a16207;">
          None of the platforms ({names}) returned usable results for this search, so there's nothing
          to compare or recommend. Try rephrasing, adding details (dates, location), or use
          <b>⟲ New search</b> above to start over.</div>
        </div>""", unsafe_allow_html=True)
        return

    intent          = data.get("intent", {}) or {}
    comparison      = data.get("comparison", {}) or {}
    recommendation  = data.get("recommendation", {}) or {}
    platform_results= data.get("platform_results", {}) or {}

    # ── Intent bar ──
    if intent:
        params = intent.get("params", {})
        param_bits = []
        for k, v in list(params.items())[:6]:
            if not v or k == "cabin_class":
                continue
            if isinstance(v, dict):
                v = list(v.values())[0] if v else ""
            label = str(v).strip()
            if not label:
                continue
            param_bits.append(f'<span class="i-param">{label}</span>')
        params_html = "".join(param_bits)
        cabin = params.get("cabin_class")
        cabin_html = f'<span class="i-cabin">💺 {cabin}</span>' if cabin else ""
        st.markdown(f"""<div class="intent-bar">
          <span class="i-type">{intent.get('type','')}</span>{cabin_html}{params_html}
        </div>""", unsafe_allow_html=True)

        if intent.get("clarification_needed"):
            cq = intent.get("clarification_question", "Could you clarify?")
            st.markdown(f"""<div class="clarify">
              <span>💬</span>
              <span>{cq}</span>
            </div>""", unsafe_allow_html=True)
            # Date-aware placeholder so users know the format to answer in.
            is_date_q = "date" in cq.lower() or "check-in" in cq.lower()
            ph = ("e.g. 13 June to 15 June  ·  2 guests"
                  if is_date_q else "Type your answer here…")
            # Inline answer box — user types reply and search continues
            cl_col, cl_btn = st.columns([5, 1])
            with cl_col:
                clarify_answer = st.text_input(
                    "Your answer to the clarifying question",
                    placeholder=ph,
                    label_visibility="collapsed",
                    key="clarify_input",
                )
            with cl_btn:
                if st.button("Continue →", key="clarify_submit") and clarify_answer:
                    # Route through the live-run branch so the user gets the full
                    # mission-control view for the follow-up search too.
                    st.session_state["active_query"] = f"{active} — {clarify_answer}"
                    st.session_state["_run_search_now"] = True
                    st.rerun()
            return

    if not platform_results:
        st.markdown('<div class="rrow-empty" style="padding:3rem;">No results — try rephrasing.</div>',
                    unsafe_allow_html=True)
        return

    ranked      = comparison.get("ranked_platforms", [])
    winner_id   = recommendation.get("winner_platform", "") if recommendation else ""
    intent_type = (intent.get("type") or "general") if intent else "general"
    insights    = data.get("insights") or {}
    badges_map  = insights.get("badges", {}) if insights.get("available") else {}
    scores_map  = insights.get("value_scores", {}) if insights.get("available") else {}

    intent_params = intent.get("params", {}) if intent else {}

    # ── 0. Roadblocks FIRST — if any platform got stuck, ask the user for input
    #       up front, full-width and unmissable (right under the breakpoints), rather
    #       than buried below the results in a narrow column. ──
    render_roadblocks(platform_results, intent_type, intent_params)

    # ── 1. Key metrics strip (inverted-pyramid: most decision-critical facts up
    #       top, big and scannable, before any detail) ──
    lo = comparison.get("overall_min_price")
    av = comparison.get("overall_avg_price")
    pwr = comparison.get("platforms_with_results", 0)
    psr = comparison.get("platforms_searched", 0)
    st.markdown(f"""
    <div class="kpi-strip">
      <div class="kpi"><div class="kpi-v">{comparison.get('total_results',0)}</div>
        <div class="kpi-l">Results</div></div>
      <div class="kpi"><div class="kpi-v">{pwr}<span class="kpi-sub">/{psr}</span></div>
        <div class="kpi-l">Platforms</div></div>
      <div class="kpi kpi-accent"><div class="kpi-v">{'₹'+format(int(lo),',') if lo else '—'}</div>
        <div class="kpi-l">Lowest price</div></div>
      <div class="kpi"><div class="kpi-v">{'₹'+format(int(av),',') if av else '—'}</div>
        <div class="kpi-l">Average</div></div>
    </div>
    """, unsafe_allow_html=True)

    # ── 2. Best Pick — the headline answer ──
    if recommendation:
        render_best_pick_banner(recommendation, ranked, intent_type, intent_params)

    # ═══ ORGANIZED DETAIL — everything below the headline answer lives in tabs,
    # one surface per concern: results, AI insights, the agent conversation,
    # the engine internals, the critic's audit, and the knowledge graph. ═══
    # Graph indexing happens up front so the Graph tab reflects this run.
    _graph_on = False
    try:
        from backend import graph_chat as _gc
        _graph_on = _gc.available()
        _run = st.session_state.get("_search_run_id", 0)
        if _graph_on and st.session_state.get("_graph_indexed_run") != _run:
            _gc.index_search(active, platform_results)
            st.session_state["_graph_indexed_run"] = _run
    except Exception:
        _graph_on = False

    tab_results, tab_insights, tab_agents, tab_engine, tab_valid, tab_graph = st.tabs([
        "🏷 Results", "🧠 AI insights", "🛰 Agent comms",
        "🖥 Engine room", "🛡 Validation", "🔗 Graph",
    ])

    with tab_results:
        compare_type = comparison.get("compare_type", "")
        if compare_type:
            unmatched = [p["platform_name"] for p in ranked if not p.get("type_matched", True)]
            warn_html = (f'<span class="compare-note-warn">No {compare_type} on '
                         f'{", ".join(unmatched)} — showing the closest alternative.</span>'
                         if unmatched else "")
            st.markdown(
                f'<div class="compare-note">⚖️ Comparing <b>{compare_type}</b> like-for-like '
                f'across every platform.{warn_html}</div>',
                unsafe_allow_html=True,
            )

        segments = data.get("segments") or {}
        if segments.get("available"):
            render_segments(segments, intent_type, intent_params)

        pids = list(platform_results.keys())
        total_n = comparison.get("total_results", 0)
        with st.expander(f"Browse all {total_n} results across {len(pids)} platforms", expanded=False):
            cols = st.columns(len(pids))
            for col, pid in zip(cols, pids):
                deep_url = _build_deep_link(pid, intent_params) or _PLATFORM_WEBSITES.get(pid, "")
                with col:
                    render_platform_card(
                        pid, platform_results[pid],
                        is_winner=(pid == winner_id),
                        platform_website=deep_url,
                        intent_type=intent_type,
                        badges=badges_map.get(pid, []),
                        value_score=scores_map.get(pid),
                    )

    with tab_insights:
        if insights.get("available"):
            render_insights(insights)
        else:
            st.markdown('<div class="rrow-empty" style="padding:2rem;">No AI insights for this run.</div>',
                        unsafe_allow_html=True)

    with tab_agents:
        # The full inter-agent conversation from this run, on its animated shell.
        render_agent_comms(st.empty(), data.get("agent_comms"), live=False)

    with tab_engine:
        _ep_left, _ep_right = st.columns(2)
        render_engine_panels(_ep_left.empty(), _ep_right.empty(),
                             data.get("diagnostics"), data.get("browser_runs"), live=False,
                             events=st.session_state.get("_last_events"))
        render_diagnostics(data.get("diagnostics"))

    with tab_valid:
        render_validation_panel(st.empty(), data.get("validation"), data.get("remediation_log"))

    with tab_graph:
        if not _graph_on:
            st.markdown('<div class="rrow-empty" style="padding:2rem;">Neo4j isn\'t running — '
                        'start it to query results as a graph. (See NEO4J setup docs.)</div>',
                        unsafe_allow_html=True)
        else:
            # ── Direct graph query — write your own Cypher, no LLM involved ──
            with st.expander("🔗 Query the graph directly (raw Cypher)", expanded=True):
                st.markdown(
                    "<div style='font-size:0.78rem;color:#9A7E58;margin-bottom:0.4rem;'>"
                    "Schema: <code>(:CurSearch {query})-[:RESULT]->(:CurResult "
                    "{name, price, stops, airline, cabin, duration, rating, platform, url})</code>"
                    "<br>Read-only — write/delete clauses are blocked.</div>",
                    unsafe_allow_html=True,
                )
                cy_default = "MATCH (r:CurResult) RETURN r.name AS flight, r.price AS price, r.stops AS stops ORDER BY r.price ASC"
                cy_text = st.text_area("Cypher", value=cy_default, height=90,
                                       key="_direct_cypher", label_visibility="collapsed")
                if st.button("▶ Run query", key="_run_cypher"):
                    res = _gc.run_cypher(cy_text)
                    if res["ok"]:
                        if res["rows"]:
                            st.dataframe(_flatten_rows(res["rows"]), use_container_width=True, hide_index=True)
                            st.caption(f"{len(res['rows'])} row(s) · columns: {', '.join(res['columns'])}")
                        else:
                            st.info("Query ran fine — 0 rows.")
                    else:
                        st.error(res["error"])

            # ── Ask in plain English — watch the LLM WRITE the Cypher, then run it ──
            with st.expander("🧠 Ask in plain English (LLM writes the Cypher)"):
                st.markdown(
                    "<div style='font-size:0.78rem;color:#9A7E58;margin-bottom:0.4rem;'>"
                    "Type a question about your current results. The LLM translates it into "
                    "Cypher — shown below before running — then Neo4j answers it exactly.</div>",
                    unsafe_allow_html=True,
                )
                nl_q = st.text_input("Question", placeholder="e.g. top 3 cheapest flights, which airline appears most",
                                     key="_nl_question", label_visibility="collapsed")
                if st.button("✨ Generate & run", key="_run_nl") and nl_q.strip():
                    with st.spinner("LLM is writing the Cypher…"):
                        res = _gc.ask_llm(nl_q)
                    if res["cypher"]:
                        st.code(res["cypher"], language="cypher")
                    if res["ok"]:
                        if res["rows"]:
                            st.dataframe(_flatten_rows(res["rows"]), use_container_width=True, hide_index=True)
                            st.caption(f"{len(res['rows'])} row(s)")
                        else:
                            st.info("Query ran fine — 0 rows.")
                    else:
                        st.error(res["error"])

    # ── Persistent Chat ──
    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # Seed/update the assistant's summary message once per search run. A run id
    # (not the query text) drives this — the search box text can lag behind
    # `active_query` after a chat-driven refinement, which used to cause this
    # block to keep firing and wipe the conversation on every rerun.
    run_id = st.session_state.get("_search_run_id", 0)
    if run_id and run_id != st.session_state.get("_chat_seeded_run", -1):
        rec_text = ""
        if recommendation and recommendation.get("winner_platform"):
            wp = recommendation.get("winner_platform", "")
            wr = recommendation.get("winner_result") or {}
            price_str = ""
            for pf in ("price","price_per_night","price_per_day","total_price"):
                if wr.get(pf):
                    price_str = f"₹{int(float(str(wr[pf]).replace(',','')))} "
                    break
            total = comparison.get("total_results", 0)
            ps    = comparison.get("platforms_with_results", 0)
            rec_text = (f"Found **{total} results** across **{ps} platforms**. "
                        f"Best pick: **{wp}** {price_str}— {recommendation.get('reasoning','')[:120]}…")
        else:
            rec_text = "Search complete. No strong recommendation — results are above for your review."

        existing = st.session_state.get("chat_history")
        if existing:
            # Chat-driven refinement — keep the conversation, just add the update.
            existing.append({"role": "assistant", "content": rec_text})
        else:
            st.session_state["chat_history"] = [{"role": "assistant", "content": rec_text}]
        st.session_state["_chat_seeded_run"] = run_id

    # Chat container
    st.markdown("""
    <div style="background:#fff;border:1.5px solid #EADFCB;border-radius:16px;
         padding:1.2rem 1.4rem 0.5rem;margin-top:0.5rem;">
      <div style="font-size:0.68rem;font-weight:700;letter-spacing:.08em;
           text-transform:uppercase;color:#9A7E58;margin-bottom:1rem;">
        💬 Chat with your results
      </div>
    """, unsafe_allow_html=True)

    # Render chat history
    chat_history = st.session_state.get("chat_history", [])
    for msg in chat_history:
        with st.chat_message(msg["role"],
                             avatar="🤖" if msg["role"] == "assistant" else "👤"):
            st.markdown(msg["content"])

    st.markdown("</div>", unsafe_allow_html=True)

    if follow := st.chat_input("Ask a follow-up… e.g. 'show only non-stop', 'cheapest under 9000'"):
        st.session_state.setdefault("chat_history", []).append(
            {"role": "user", "content": follow}
        )

        # ── 1. Try the GRAPH first: filter/sort the current results with Cypher ──
        graphed = None
        try:
            from backend import graph_chat as _gc
            if _gc.available():
                graphed = _gc.answer_followup(follow)
        except Exception:
            graphed = None

        if graphed and graphed.get("answered"):
            # answered straight from the result graph — no re-search, no LLM reasoning
            msg = graphed["message"] + "\n\n<sub>↳ answered from your results graph (Neo4j)</sub>"
            st.session_state["chat_history"].append({"role": "assistant", "content": msg})
        else:
            # ── 2. Fall back to the normal LLM chat / refined-search path ──
            from backend.nodes.chat_agent import chat_response as _chat_response
            with st.spinner("Thinking…"):
                reply = _chat_response(
                    user_message=follow,
                    chat_history=st.session_state["chat_history"],
                    platform_results=platform_results,
                    comparison=comparison,
                    recommendation=recommendation,
                    intent=intent,
                    original_query=active,
                )
            st.session_state["chat_history"].append(
                {"role": "assistant", "content": reply["message"]}
            )
            if reply.get("should_search") and reply.get("refined_query"):
                st.session_state.pop("last_result", None)
                st.session_state["active_query"] = reply["refined_query"].strip()
                st.session_state["_run_search_now"] = True
                st.session_state["_chat_refined_search"] = True

        st.rerun()

if __name__ == "__main__":
    main()

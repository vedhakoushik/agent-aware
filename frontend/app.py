import os, sys, time, threading, httpx, streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

# Shared with the backend so the UI's notion of "booking type" never drifts from
# what the comparison engine actually groups/compares on.
from backend.booking_type import BOOKING_TYPE_FIELDS as _BOOKING_TYPE_FIELDS, extract_booking_type as _booking_type
from backend.memory.reliability import get_reliability as _get_reliability
from backend.progress import get_tracker as _get_tracker
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
                   initial_sidebar_state="collapsed")

# Load CSS from file — avoids the raw-text-leak bug in Streamlit
_css_path = os.path.join(os.path.dirname(__file__), "style.css")
with open(_css_path, encoding="utf-8") as _f:
    st.markdown(f"<style>{_f.read()}</style>", unsafe_allow_html=True)

# Load full platform config once at startup
import yaml as _yaml, re as _re
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
                      "error": r.get("error"), "elapsed_seconds": r.get("elapsed_seconds", 0)}
                for pid, r in s.get("platform_results", {}).items()
            },
            "comparison": s.get("comparison"),
            "segments": s.get("segments"),
            "insights": s.get("insights"),
            "recommendation": s.get("recommendation"),
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


def run_search_live(query: str) -> dict:
    """Run a search while streaming live background-progress to the UI."""
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

    with st.status("Working on your search…", expanded=True) as status:
        slot = st.empty()
        while t.is_alive():
            _render_progress(slot, tracker.snapshot())
            time.sleep(0.25)
        t.join(timeout=1)
        tracker.finish()
        # Clear the live line and collapse the panel — the results below are the
        # payoff; we don't leave a wall of finished steps cluttering the page.
        slot.empty()
        if holder.get("error"):
            status.update(label="Search hit a problem", state="error", expanded=False)
        else:
            status.update(label="Search complete ✓", state="complete", expanded=False)

    if holder.get("error"):
        raise holder["error"]
    return holder.get("data", {})


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


def _extra_tags(result: dict, limit=4) -> str:
    return _clean_chips(result, limit=limit, css="chip")

def _bp_tags(result: dict, limit=5) -> str:
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
    price_html  = f'<span class="rrow-price">₹{price}<span style="font-size:0.65rem;font-weight:500;color:#94a3b8;">{plabel}</span></span>' if price else ""
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


def render_segments(segments: dict, intent_type: str = "general"):
    """Render results separated into type-groups (room type / cabin class / …),
    each compared side-by-side across platforms with prices + amenities."""
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
            url  = r.get("url") or ""
            link = (f'<a class="seg-link" href="{url}" target="_blank">view →</a>'
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


def render_best_pick_banner(rec: dict, ranked: list, intent_type: str = "general"):
    if not rec:
        return

    pid    = rec.get("winner_platform") or ""
    winner = rec.get("winner_result") or {}

    # Don't render if no real winner found
    if not pid or pid.lower() in ("", "none", "null"):
        st.markdown("""
        <div style="background:#f8fafc;border:1.5px solid #e2e8f0;border-radius:14px;
             padding:1.2rem 1.4rem;color:#94a3b8;font-size:0.85rem;text-align:center;">
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

    conf_cls  = {"high":"conf-high","medium":"conf-medium","low":"conf-low"}.get(confidence,"conf-medium")
    book_html = f'<a class="bp-book" href="{url}" target="_blank">Book Now →</a>' if url else ""
    tips_html = f'<div style="font-size:0.78rem;color:#475569;margin-top:0.5rem;">💡 {tips}</div>' if tips else ""

    # Build alts as separate lines — avoid nesting complex HTML in f-string
    alts_lines = ""
    for a in (alts or [])[:2]:
        ap = str(a.get("platform","")).replace("<","&lt;")
        aw = str(a.get("why","")).replace("<","&lt;")
        alts_lines += f'<div style="font-size:0.76rem;color:#475569;padding:3px 0;"><span style="color:#64748b;font-weight:600;">{ap}</span> — {aw}</div>'

    alts_html = ""
    if alts_lines:
        alts_html = (
            '<div style="margin-top:0.75rem;border-top:1px solid #0f172a;padding-top:0.75rem;">'
            '<div style="font-size:0.65rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
            'color:#334155;margin-bottom:4px;">Also consider</div>'
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
            f'<div style="font-size:0.72rem;color:#334155;margin-top:0.5rem;max-width:180px;">{price_note}</div>'
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


# ── Main ──────────────────────────────────────────────────────
def main():

    # ── Access gate ──
    # Require Google (Gmail) sign-in before anything else renders. Returns the
    # signed-in user (or {} when auth isn't configured yet / open mode). If the
    # visitor isn't allowed, require_login() renders the sign-in screen and
    # halts here, so nothing below runs for an anonymous user.
    user = require_login()

    # ── Topbar ──
    topbar_col, user_col = st.columns([6, 1])
    with topbar_col:
        st.markdown("""
        <div class="topbar">
          <div class="topbar-logo"><div class="topbar-logo-dot"></div> Agent-Aware</div>
        </div>
        """, unsafe_allow_html=True)
    with user_col:
        render_user_chip(user)

    # ── Slack channels (read-only) — collapsible, shows only when configured ──
    render_slack_panel()

    # ── Search hero ──
    st.markdown("""
    <div class="search-section">
      <h1 class="search-headline">Search everything, everywhere.</h1>
      <p class="search-sub">AI searches across platforms simultaneously and picks the best for you.</p>
    </div>
    """, unsafe_allow_html=True)

    # Search input + button — wrapped in form so Enter key submits
    with st.form(key="search_form", clear_on_submit=False, border=False):
        q_col, btn_col = st.columns([5, 1])
        with q_col:
            query = st.text_input(
                "q",
                placeholder='Try: "flights Delhi to Goa this Friday under ₹5000"',
                label_visibility="collapsed",
                key="search_query",
            )
        with btn_col:
            st.markdown('<div class="search-btn-col">', unsafe_allow_html=True)
            search_clicked = st.form_submit_button("Search →", type="primary")
            st.markdown("</div>", unsafe_allow_html=True)

    # Example chips
    examples = ["✈ Flights Mumbai → Delhi", "🏨 Hotels in Manali", "🎬 Coldplay India 2025",
                "🍕 Pizza near Bangalore", "📱 iPhone 15 price", "🚂 Delhi–Agra trains"]
    chip_cols = st.columns(len(examples))
    for i, ex in enumerate(examples):
        with chip_cols[i]:
            if st.button(ex, key=f"chip_{i}", use_container_width=True):
                st.session_state["active_query"] = ex.split(" ",1)[1].strip()
                st.session_state["_run_search_now"] = True
                st.rerun()

    st.markdown("<hr>", unsafe_allow_html=True)

    # ── Trigger ──
    # active_query is set programmatically (chips, clarification answers)
    # search_query is owned by the widget — never write to it directly
    widget_query = st.session_state.get("search_query", "").strip()
    active = st.session_state.get("active_query", "") or widget_query

    # Fire on button click OR when active_query was just set by a chip/clarification
    just_set = st.session_state.pop("_run_search_now", False)
    if (search_clicked or just_set) and active:
        # Clear old result and chat for new search — prevents stale data
        st.session_state.pop("last_result", None)
        st.session_state.pop("chat_history", None)
        st.session_state.pop("_chat_seeded_for", None)
        st.session_state["last_result"] = run_search_live(active)
        st.session_state["active_query"] = active

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
            st.markdown(f"""<div style="background:#1a1306;border:1px solid rgba(245,158,11,0.25);
              border-radius:12px;padding:1.1rem 1.3rem;color:#fbbf24;font-size:0.9rem;line-height:1.5;">
              ⏳ <b>We've hit today's AI usage limit.</b><br/>
              <span style="color:#fcd34d;font-size:0.85rem;">
              Our search assistant has used up its daily quota of AI requests. It refills on its own —
              try again in about <b>{wait_for}</b>.
              </span></div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""<div style="background:#0f0a0a;border:1px solid rgba(239,68,68,0.2);
              border-radius:12px;padding:1rem 1.2rem;color:#f87171;font-size:0.85rem;">
              ⚠ Something went wrong while searching. Please try again in a moment.</div>""",
              unsafe_allow_html=True)
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
                    "clarify",
                    placeholder=ph,
                    label_visibility="collapsed",
                    key="clarify_input",
                )
            with cl_btn:
                if st.button("Continue →", key="clarify_submit") and clarify_answer:
                    full_query = f"{active} — {clarify_answer}"
                    st.session_state["last_result"] = run_search_live(full_query)
                    st.session_state["active_query"] = full_query
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
        render_best_pick_banner(recommendation, ranked, intent_type)

    # Explain how like-for-like comparison is anchored (above the comparison views).
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

    # ── 3. AI insights + structured side-by-side matrix ──
    if insights.get("available"):
        render_insights(insights)

    # ── 4. Browse by type (segmented comparison) ──
    segments = data.get("segments") or {}
    if segments.get("available"):
        render_segments(segments, intent_type)

    # ── 5. All results — progressive disclosure: the full per-platform browse is
    #       secondary detail, tucked into an expander so the page stays calm. ──
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

    # ── 6. Persistent Chat ──
    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # Auto-seed first assistant message when a new search result arrives
    last_query_key = st.session_state.get("_chat_seeded_for", "")
    if active and active != last_query_key:
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

        st.session_state["chat_history"] = [
            {"role": "assistant", "content": rec_text}
        ]
        st.session_state["_chat_seeded_for"] = active

    # Chat container
    st.markdown("""
    <div style="background:#fff;border:1.5px solid #e2e8f0;border-radius:16px;
         padding:1.2rem 1.4rem 0.5rem;margin-top:0.5rem;">
      <div style="font-size:0.68rem;font-weight:700;letter-spacing:.08em;
           text-transform:uppercase;color:#94a3b8;margin-bottom:1rem;">
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

    # Chat input (persistent, always visible)
    if follow := st.chat_input("Ask a follow-up… e.g. 'show only non-stop', 'what about next Saturday?'"):
        # Add user message
        st.session_state.setdefault("chat_history", []).append(
            {"role": "user", "content": follow}
        )

        # Get context-aware AI response
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

        # Add assistant reply
        st.session_state["chat_history"].append(
            {"role": "assistant", "content": reply["message"]}
        )

        # Trigger refined search if needed
        if reply.get("should_search") and reply.get("refined_query"):
            st.session_state.pop("last_result", None)
            st.session_state["active_query"] = reply["refined_query"]
            st.session_state["_run_search_now"] = True

        st.rerun()

if __name__ == "__main__":
    main()

"""Google sign-in gate for Agent-Aware.

Access control: users must authenticate with their Google (Gmail) account
before they can use the app. Built on Streamlit's native OpenID Connect auth
(`st.login` / `st.user` / `st.logout`, available in Streamlit >= 1.42) — so we
never handle passwords or raw OAuth tokens ourselves; Google does the
authentication and Streamlit manages the signed session cookie.

Configuration lives entirely in `.streamlit/secrets.toml`:
  [auth]            -> Google OAuth client credentials + redirect
  [auth_allowlist]  -> (optional) restrict to specific Gmail addresses

See `.streamlit/secrets.toml.example` and `SETUP_GOOGLE_AUTH.md` for setup.
"""
import streamlit as st


def _auth_configured() -> bool:
    """True only once real Google OAuth credentials are present in secrets.

    Until the user pastes their Client ID / Secret (replacing the PASTE_…
    placeholders), this returns False so the app stays usable in dev instead
    of hard-crashing on a half-configured auth block.
    """
    try:
        auth = st.secrets.get("auth", {})
        cid = str(auth.get("client_id", ""))
        csecret = str(auth.get("client_secret", ""))
        return bool(cid and csecret
                    and not cid.startswith("PASTE_")
                    and not csecret.startswith("PASTE_"))
    except Exception:
        return False


def _allowed_emails() -> set:
    """Optional allowlist of Gmail addresses. Empty set = any Google account."""
    try:
        raw = st.secrets.get("auth_allowlist", {}).get("emails", [])
        if isinstance(raw, str):
            raw = raw.split(",")
        return {e.strip().lower() for e in raw if str(e).strip()}
    except Exception:
        return set()


def current_user() -> dict:
    """Return {email, name, picture} for the logged-in user, or {} if none."""
    try:
        if getattr(st.user, "is_logged_in", False):
            email = getattr(st.user, "email", "") or ""
            return {
                "email": email,
                "name": getattr(st.user, "name", "") or email,
                "picture": getattr(st.user, "picture", "") or "",
            }
    except Exception:
        pass
    return {}


def _render_login_screen():
    """Full-screen sign-in gate shown to anonymous visitors."""
    st.markdown("""
    <div style="max-width:440px;margin:8vh auto 0;text-align:center;
         background:#fff;border:1px solid #e2e8f0;border-radius:18px;
         padding:2.6rem 2.2rem;box-shadow:0 8px 30px rgba(15,23,42,0.06);">
      <div style="font-size:2.2rem;">🔍</div>
      <h1 style="font-size:1.5rem;font-weight:800;color:#0f172a;margin:0.6rem 0 0.3rem;">
        Agent-Aware</h1>
      <p style="color:#64748b;font-size:0.92rem;line-height:1.5;margin:0 0 1.6rem;">
        Sign in with your Google account to search and compare across platforms.
      </p>
    </div>
    """, unsafe_allow_html=True)

    _, mid, _ = st.columns([1, 1.1, 1])
    with mid:
        if st.button("Continue with Google →", type="primary", use_container_width=True):
            st.login()  # redirects to Google's consent screen, then back to the app
        st.markdown(
            '<p style="text-align:center;color:#94a3b8;font-size:0.72rem;margin-top:0.8rem;">'
            'We only use your Google sign-in to verify who you are. '
            'No emails are read or sent.</p>',
            unsafe_allow_html=True,
        )


def _render_denied(user: dict):
    """Shown when a signed-in user isn't on the allowlist."""
    st.markdown(f"""
    <div style="max-width:460px;margin:10vh auto 0;text-align:center;
         background:#fff;border:1px solid #fecaca;border-radius:18px;padding:2.4rem 2rem;">
      <div style="font-size:2rem;">🚫</div>
      <h2 style="font-size:1.3rem;color:#0f172a;margin:0.5rem 0;">Access not enabled</h2>
      <p style="color:#64748b;font-size:0.9rem;line-height:1.5;">
        <b>{user.get('email','')}</b> isn't on the access list for this app yet.
        Ask the owner to add your Gmail address, then sign in again.
      </p>
    </div>
    """, unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1, 1])
    with mid:
        if st.button("Sign out", use_container_width=True):
            st.logout()


def require_login() -> dict:
    """Gate the whole app behind Google sign-in.

    Returns the logged-in user's info dict when access is granted. Otherwise
    renders the sign-in (or access-denied) screen and calls `st.stop()` so the
    rest of the page never renders.

    If Google auth isn't configured yet, shows a one-line setup notice and lets
    the app through (dev mode) rather than locking everyone out of a
    half-set-up install.
    """
    if not _auth_configured():
        st.info("🔓 Google sign-in isn't configured yet — running in open mode. "
                "Add your OAuth credentials to `.streamlit/secrets.toml` to require login. "
                "(See SETUP_GOOGLE_AUTH.md)")
        return {}

    if not getattr(st.user, "is_logged_in", False):
        _render_login_screen()
        st.stop()

    user = current_user()

    allow = _allowed_emails()
    if allow and user.get("email", "").lower() not in allow:
        _render_denied(user)
        st.stop()

    return user


def render_user_chip(user: dict):
    """Small signed-in identity + sign-out control for the top bar."""
    if not user:
        return
    label = user.get("name") or user.get("email") or "Account"
    with st.popover(f"👤 {label}", use_container_width=False):
        st.markdown(f"**{user.get('name','')}**")
        st.caption(user.get("email", ""))
        if st.button("Sign out", use_container_width=True):
            st.logout()

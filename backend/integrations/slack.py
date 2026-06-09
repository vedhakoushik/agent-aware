"""
Slack integration (read-only).

Lets the app show your Slack workspace's channels — and the recent messages in a
channel — right inside Agent-Aware. It talks to the Slack Web API with a Bot User
OAuth token you generate once (see SETUP_SLACK.md) and drop into `.env` as
`SLACK_BOT_TOKEN`.

Scope of this module is deliberately READ-ONLY: it lists channels, reads recent
messages, and resolves user display names. It never posts, edits, deletes, or
changes anything in your workspace. (Posting would be a separate, explicitly
opt-in feature.)

No new dependency — uses httpx (already used elsewhere). Results are cached for a
short TTL so repeated page renders don't hammer Slack's rate limits.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

_API = "https://slack.com/api"
_TIMEOUT = 12

# Tiny in-process TTL cache: {key: (expires_at, value)}
_CACHE: dict[str, tuple[float, object]] = {}
_TTL_CHANNELS = 120      # channel list changes rarely
_TTL_USERS = 600         # display names change rarely
_TTL_MESSAGES = 20       # messages are fresher


def _token() -> str:
    return os.getenv("SLACK_BOT_TOKEN", "").strip()


def is_configured() -> bool:
    """True once a real bot token (xoxb-…) is present."""
    t = _token()
    return bool(t) and t.startswith("xoxb-")


def _cached(key: str):
    hit = _CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    return None


def _store(key: str, value, ttl: float):
    _CACHE[key] = (time.monotonic() + ttl, value)


def _call(method: str, params: dict | None = None) -> dict:
    """GET a Slack Web API method. Returns the parsed JSON (with `ok`)."""
    token = _token()
    if not token:
        return {"ok": False, "error": "not_configured"}
    try:
        r = httpx.get(
            f"{_API}/{method}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
            timeout=_TIMEOUT,
        )
        data = r.json()
        if not data.get("ok"):
            logger.warning(f"Slack {method} error: {data.get('error')}")
        return data
    except Exception as e:
        logger.warning(f"Slack {method} request failed: {e}")
        return {"ok": False, "error": str(e)}


def auth_test() -> dict:
    """Verify the token and return {ok, team, user, url} for display/health."""
    cached = _cached("auth")
    if cached is not None:
        return cached
    data = _call("auth.test")
    out = {
        "ok": bool(data.get("ok")),
        "team": data.get("team", ""),
        "user": data.get("user", ""),
        "url": data.get("url", ""),
        "error": data.get("error", ""),
    }
    if out["ok"]:
        _store("auth", out, _TTL_USERS)
    return out


def list_channels(limit: int = 200) -> dict:
    """List public + private channels the workspace exposes to this bot.

    Returns {ok, channels:[{id,name,is_private,is_member,num_members,topic,purpose}], error}.
    """
    cached = _cached("channels")
    if cached is not None:
        return cached

    data = _call("conversations.list", {
        "types": "public_channel,private_channel",
        "exclude_archived": "true",
        "limit": limit,
    })
    if not data.get("ok"):
        return {"ok": False, "channels": [], "error": data.get("error", "unknown")}

    channels = []
    for c in data.get("channels", []):
        channels.append({
            "id": c.get("id"),
            "name": c.get("name", ""),
            "is_private": bool(c.get("is_private")),
            "is_member": bool(c.get("is_member")),
            "num_members": c.get("num_members", 0),
            "topic": (c.get("topic") or {}).get("value", ""),
            "purpose": (c.get("purpose") or {}).get("value", ""),
        })
    channels.sort(key=lambda c: c["name"].lower())
    out = {"ok": True, "channels": channels, "error": ""}
    _store("channels", out, _TTL_CHANNELS)
    return out


def _user_map() -> dict:
    """{user_id: display_name} — cached, best-effort."""
    cached = _cached("users")
    if cached is not None:
        return cached
    data = _call("users.list", {"limit": 500})
    umap = {}
    if data.get("ok"):
        for u in data.get("members", []):
            prof = u.get("profile", {}) or {}
            name = (prof.get("display_name") or prof.get("real_name")
                    or u.get("name") or u.get("id"))
            umap[u.get("id")] = name
    _store("users", umap, _TTL_USERS)
    return umap


def get_messages(channel_id: str, limit: int = 15) -> dict:
    """Recent messages in a channel, newest last.

    Returns {ok, messages:[{author,text,ts}], error}. If the bot isn't a member of
    the channel, Slack returns `not_in_channel` — surfaced so the UI can prompt the
    user to invite the bot.
    """
    cache_key = f"msgs:{channel_id}:{limit}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    data = _call("conversations.history", {"channel": channel_id, "limit": limit})
    if not data.get("ok"):
        return {"ok": False, "messages": [], "error": data.get("error", "unknown")}

    umap = _user_map()
    messages = []
    for m in reversed(data.get("messages", [])):  # API returns newest-first
        if m.get("subtype") in ("channel_join", "channel_leave"):
            continue
        ts = m.get("ts", "")
        author = umap.get(m.get("user", ""), m.get("username", "") or "Unknown")
        messages.append({
            "author": author,
            "text": m.get("text", ""),
            "ts": ts,
        })
    out = {"ok": True, "messages": messages, "error": ""}
    _store(cache_key, out, _TTL_MESSAGES)
    return out


def clear_cache() -> None:
    _CACHE.clear()

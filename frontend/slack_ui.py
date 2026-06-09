"""
Slack channel viewer for the Agent-Aware web UI.

Renders a panel that lists your workspace's Slack channels and shows the recent
messages in whichever channel you pick — read-only. Shown only once a bot token
is configured (SLACK_BOT_TOKEN); otherwise a slim "connect Slack" hint appears so
the feature is discoverable without cluttering the page.
"""
from __future__ import annotations

import datetime as _dt

import streamlit as st

from backend.integrations import slack


def _fmt_ts(ts: str) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(ts)).strftime("%d %b, %H:%M")
    except Exception:
        return ""


def _setup_hint():
    st.markdown(
        '<div class="slack-hint">💬 <b>Connect Slack</b> to browse your channels here. '
        'Add a bot token to <code>.env</code> as <code>SLACK_BOT_TOKEN</code> — '
        'see <code>SETUP_SLACK.md</code> for the 5-minute setup.</div>',
        unsafe_allow_html=True,
    )


def render_slack_panel():
    """Top-of-page collapsible Slack panel. No-op visual clutter when unconfigured."""
    configured = slack.is_configured()

    with st.expander("💬 Slack channels", expanded=False):
        if not configured:
            _setup_hint()
            return

        auth = slack.auth_test()
        if not auth.get("ok"):
            st.markdown(
                f'<div class="slack-err">Couldn\'t connect to Slack: '
                f'<code>{auth.get("error","unknown")}</code>. '
                f'Check that <code>SLACK_BOT_TOKEN</code> is valid.</div>',
                unsafe_allow_html=True,
            )
            return

        st.markdown(
            f'<div class="slack-team">Connected to <b>{auth.get("team","your workspace")}</b> '
            f'as <b>{auth.get("user","bot")}</b></div>',
            unsafe_allow_html=True,
        )

        data = slack.list_channels()
        if not data.get("ok"):
            st.markdown(
                f'<div class="slack-err">Couldn\'t list channels: '
                f'<code>{data.get("error","unknown")}</code></div>',
                unsafe_allow_html=True,
            )
            return

        channels = data.get("channels", [])
        if not channels:
            st.markdown('<div class="slack-hint">No channels visible to the bot yet.</div>',
                        unsafe_allow_html=True)
            return

        # Channel list as clean chips (overview at a glance)
        chips = "".join(
            f'<span class="slack-chip">{"🔒" if c["is_private"] else "#"} {c["name"]}'
            f'<span class="slack-chip-n">{c["num_members"]}</span></span>'
            for c in channels[:40]
        )
        st.markdown(f'<div class="slack-chips">{chips}</div>', unsafe_allow_html=True)

        # Pick a channel → show its recent messages
        by_label = {
            f'{"🔒 " if c["is_private"] else "# "}{c["name"]}': c
            for c in channels
        }
        label = st.selectbox(
            "View messages from", list(by_label.keys()),
            key="slack_channel_pick", label_visibility="collapsed",
        )
        chan = by_label.get(label)
        if not chan:
            return

        if chan.get("topic"):
            st.markdown(f'<div class="slack-topic">📌 {chan["topic"]}</div>',
                        unsafe_allow_html=True)

        msgs = slack.get_messages(chan["id"], limit=15)
        if not msgs.get("ok"):
            err = msgs.get("error", "unknown")
            if err == "not_in_channel":
                st.markdown(
                    f'<div class="slack-err">The bot isn\'t in <b>#{chan["name"]}</b> yet. '
                    f'In Slack, open the channel and type '
                    f'<code>/invite @Agent-Aware</code> to let it read messages.</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f'<div class="slack-err">Couldn\'t load messages: '
                            f'<code>{err}</code></div>', unsafe_allow_html=True)
            return

        messages = msgs.get("messages", [])
        if not messages:
            st.markdown('<div class="slack-hint">No recent messages.</div>',
                        unsafe_allow_html=True)
            return

        rows = ""
        for m in messages:
            text = (m.get("text", "") or "").replace("<", "&lt;").replace(">", "&gt;")
            author = str(m.get("author", "")).replace("<", "&lt;")
            rows += (
                f'<div class="slack-msg">'
                f'<div class="slack-msg-head"><span class="slack-author">{author}</span>'
                f'<span class="slack-time">{_fmt_ts(m.get("ts",""))}</span></div>'
                f'<div class="slack-text">{text}</div></div>'
            )
        st.markdown(f'<div class="slack-feed">{rows}</div>', unsafe_allow_html=True)

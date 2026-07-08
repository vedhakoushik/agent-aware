"""
Live Agent Communication Bus — the ACTUAL messages agents send each other.

Where `progress.py` records terse status lines ("Searching Goibibo…") and
`browser_tracker.py` records one browser's clicks, THIS records the real
inter-agent traffic with its CONTENT:

  • the prompt the Intent agent sends the LLM, and the plan the LLM hands back
  • the search instructions the Intent agent passes to the Search coordinator
    ("search these websites with these params")
  • the params the coordinator dispatches to each per-platform agent, and the
    results each agent returns
  • the structured data each pipeline stage hands to the next
  • the "I'm stuck" report a platform agent sends the Monitor agent, and the
    diagnosis + fix the Monitor sends back
  • a provider failing over to another provider

It's the data behind the "show me how the agents talk to each other" view —
every message is a {from, to, kind, title, content} record the UI renders as a
chat-like feed you can expand to read the exact payload.

Thread-safe singleton, same lifecycle as the progress tracker: `start()` clears
the prior run; nodes/agents call `send()` from worker threads; the UI polls
`snapshot()` from the main Streamlit thread.
"""
from __future__ import annotations

import json
import threading
import time


def _stringify(content, limit: int = 6000) -> str:
    """Render any payload as readable text. Dicts/lists are pretty-printed JSON so
    the UI can show the exact structured message that crossed between agents."""
    if content is None:
        return ""
    if isinstance(content, (dict, list)):
        try:
            s = json.dumps(content, indent=2, ensure_ascii=False, default=str)
        except Exception:
            s = str(content)
    else:
        s = str(content)
    if len(s) > limit:
        s = s[:limit] + f"\n… (+{len(s) - limit} more chars)"
    return s


class AgentBus:
    # kind → (icon, friendly label) — drives the UI badge for each message.
    KINDS = {
        "request":   ("➡️", "request"),
        "response":  ("⬅️", "response"),
        "handoff":   ("🤝", "handoff"),
        "dispatch":  ("📤", "dispatch"),
        "data":      ("📦", "data"),
        "diagnosis": ("🩺", "diagnosis"),
        "error":     ("⚠️", "error"),
        "message":   ("💬", "message"),
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clock = time.monotonic
        self._events: list[dict] = []
        self._t0 = 0.0
        self._seq = 0

    # ── lifecycle ──────────────────────────────────────────────
    def start(self) -> None:
        """Begin a new run; clears the prior run's messages."""
        with self._lock:
            self._events = []
            self._t0 = self._clock()
            self._seq = 0

    # ── emit ───────────────────────────────────────────────────
    def send(self, *, frm: str, to: str, kind: str = "message",
             title: str = "", content="", meta: dict | None = None) -> None:
        """Record one agent→agent message.

        frm/to : agent display names ("Intent Agent", "LLM · groq", "Goibibo Agent")
        kind   : request | response | handoff | dispatch | data | diagnosis | error
        title  : one-line summary of the message
        content: the actual payload (str, or a dict/list that gets pretty-printed)
        """
        with self._lock:
            self._seq += 1
            self._events.append({
                "seq": self._seq,
                "frm": frm,
                "to": to,
                "kind": kind if kind in self.KINDS else "message",
                "title": (title or "").strip(),
                "content": _stringify(content),
                "meta": meta or {},
                "t": round(self._clock() - self._t0, 2) if self._t0 else 0.0,
            })

    # ── read ───────────────────────────────────────────────────
    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events = []


_bus = AgentBus()


def get_bus() -> AgentBus:
    return _bus


def send(**kwargs) -> None:
    """Module-level convenience so agents can `from backend.agent_bus import send`.
    Never raises — instrumentation must never break a real agent call."""
    try:
        _bus.send(**kwargs)
    except Exception:
        pass

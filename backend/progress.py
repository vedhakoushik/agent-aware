"""
Live progress tracker for a single search run.

The search pipeline (parse → search platforms → aggregate → compare → segregate →
insights → recommend) runs as one blocking call. To show the user the actual
background work *as it happens* instead of a generic "Searching…" spinner, every
node pushes short human-readable events here, and the frontend polls `snapshot()`
on a timer while the pipeline runs in a worker thread.

Thread-safe: the pipeline emits from worker threads (each platform search runs in
its own thread); the UI reads from the main Streamlit thread.

This is a process-global singleton. The app runs the graph in-process (one search
at a time per Streamlit session), so a single shared tracker is sufficient. Each
run calls start() first, which clears the previous run's events.
"""
from __future__ import annotations

import threading
import time
from typing import Optional


class ProgressTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._active = False
        self._run_id = 0
        self._t0 = 0.0
        # Monotonic clock captured at start so we can timestamp events relative
        # to the run beginning (Date.now()-free, works fine here unlike workflows).
        self._clock = time.monotonic

    # ── lifecycle ──────────────────────────────────────────────
    def start(self) -> int:
        """Begin a new run; clears prior events and returns the new run id."""
        with self._lock:
            self._run_id += 1
            self._events = []
            self._active = True
            self._t0 = self._clock()
            return self._run_id

    def finish(self) -> None:
        with self._lock:
            self._active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    # ── emit ───────────────────────────────────────────────────
    def emit(self, message: str, *, stage: str = "", kind: str = "info") -> None:
        """Record one progress event.

        kind: "info" | "start" | "ok" | "warn" | "done"  (drives the UI icon)
        stage: coarse phase key ("intent", "search", "compare", …) for grouping.
        """
        with self._lock:
            elapsed = round(self._clock() - self._t0, 1) if self._t0 else 0.0
            self._events.append({
                "message": message,
                "stage": stage,
                "kind": kind,
                "t": elapsed,
            })

    # ── read ───────────────────────────────────────────────────
    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._events)


_tracker = ProgressTracker()


def get_tracker() -> ProgressTracker:
    return _tracker


def emit(message: str, *, stage: str = "", kind: str = "info") -> None:
    """Module-level convenience so nodes can `from backend.progress import emit`."""
    _tracker.emit(message, stage=stage, kind=kind)

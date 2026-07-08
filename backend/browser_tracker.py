"""
Live tracker for the browser-use agent — what the automated browser is doing, step
by step, and the SPECIFIC error when it hits a roadblock.

The browser agent runs in its own thread. Its `register_new_step_callback` writes each
step here; the UI polls `snapshot()` to render the real-time "browser-use" panel. Keyed
by platform so several parallel browser runs are tracked independently. Thread-safe.

This is the data behind: "when it hits a roadblock, the user sees the exact error and
tells it what to do" — record_error() captures the real exception/blocker, and the UI
turns the steps + error into the live view + the guidance prompt.
"""
from __future__ import annotations

import threading
import time
from typing import Optional


class BrowserTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clock = time.monotonic
        self._runs: dict[str, dict] = {}   # platform_id -> run info

    def start(self, platform_id: str, platform_name: str = "", entry_url: str = "",
              hint: str = "") -> None:
        with self._lock:
            self._runs[platform_id] = {
                "platform_id": platform_id,
                "platform_name": platform_name or platform_id,
                "entry_url": entry_url,
                "hint": hint,
                "steps": [],
                "status": "running",   # running | ok | stuck
                "error": None,
                "n_results": 0,
                "t0": self._clock(),
            }

    def record_step(self, platform_id: str, *, n: int, goal: str = "", action: str = "",
                    url: str = "", eval_prev: str = "") -> None:
        with self._lock:
            run = self._runs.get(platform_id)
            if not run:
                return
            run["steps"].append({
                "n": n,
                "goal": (goal or "").strip()[:160],
                "action": (action or "").strip()[:120],
                "url": (url or "").strip()[:140],
                "eval": (eval_prev or "").strip()[:160],
                "t": round(self._clock() - run["t0"], 1),
            })

    def record_screenshot(self, platform_id: str, b64: str) -> None:
        """Store the latest live page screenshot (base64 PNG) for the in-app view."""
        with self._lock:
            run = self._runs.get(platform_id)
            if run:
                run["screenshot"] = b64

    def latest_screenshot(self) -> Optional[tuple[str, str]]:
        """(platform_name, b64) of the most-recently-active run that has a shot."""
        with self._lock:
            running = [r for r in self._runs.values() if r.get("screenshot")]
            if not running:
                return None
            # Prefer a still-running tab; else the most recent.
            running.sort(key=lambda r: (r.get("status") != "running", -r["t0"]))
            r = running[0]
            return (r.get("platform_name", ""), r["screenshot"])

    def record_error(self, platform_id: str, error) -> None:
        with self._lock:
            run = self._runs.get(platform_id)
            if run:
                run["error"] = str(error)[:600]

    def record_done(self, platform_id: str, *, success: bool, n_results: int = 0) -> None:
        with self._lock:
            run = self._runs.get(platform_id)
            if run:
                run["status"] = "ok" if success else "stuck"
                run["n_results"] = n_results

    def error_for(self, platform_id: str) -> Optional[str]:
        with self._lock:
            run = self._runs.get(platform_id)
            return run.get("error") if run else None

    def get(self, platform_id: str) -> Optional[dict]:
        """Full run info (with a copied step list) for one platform, or None."""
        with self._lock:
            run = self._runs.get(platform_id)
            return dict(run, steps=list(run["steps"])) if run else None

    def set_analysis(self, platform_id: str, analysis: dict) -> None:
        """Attach the monitor agent's diagnosis to a run."""
        with self._lock:
            run = self._runs.get(platform_id)
            if run:
                run["analysis"] = analysis

    def snapshot(self) -> list[dict]:
        """All browser runs (most recently started first), with copied step lists.
        Screenshots are stripped — they're large and read live via latest_screenshot()."""
        with self._lock:
            runs = [{k: v for k, v in dict(r, steps=list(r["steps"])).items() if k != "screenshot"}
                    for r in self._runs.values()]
        return list(reversed(runs))

    def clear(self) -> None:
        with self._lock:
            self._runs = {}


_bt = BrowserTracker()


def get_browser_tracker() -> BrowserTracker:
    return _bt

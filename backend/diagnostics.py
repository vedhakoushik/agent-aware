"""
Per-run performance diagnostics — "where did the time go?".

While `progress.py` streams human-readable status lines to the UI *during* a run,
this module records structured TIMING + OUTCOME data *about* the run so we can answer
"which stage / which platform is slow?" and "what failed and why?".

Two layers are captured:
  • node timing   — how long each LangGraph node took (parse → search → … → recommend)
  • platform legs — per platform: which search tier ran (Tavily / browser-use / Google /
                    DDG), how long it took, how many results it yielded, and — if it came
                    back empty — a plain-English "roadblock" the user can act on.

Process-global singleton like the progress tracker: one search runs at a time per
Streamlit session, and start() clears the previous run. Thread-safe because each
platform search runs in its own thread.
"""
from __future__ import annotations

import threading
import time
from typing import Optional


class RunDiagnostics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clock = time.monotonic
        self._t0 = 0.0
        self._nodes: list[dict] = []      # [{name, seconds}]
        self._platforms: dict[str, dict] = {}  # platform_id -> leg info
        self._active = False

    # ── lifecycle ──────────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            self._t0 = self._clock()
            self._nodes = []
            self._platforms = {}
            self._active = True

    def finish(self) -> None:
        with self._lock:
            self._active = False

    # ── node timing ────────────────────────────────────────────
    def record_node(self, name: str, seconds: float) -> None:
        with self._lock:
            self._nodes.append({"name": name, "seconds": round(seconds, 2)})

    # ── per-platform legs ──────────────────────────────────────
    def record_platform(self, platform_id: str, *, platform_name: str = "",
                        tier: str = "", n_results: int = 0, elapsed: float = 0.0,
                        error: Optional[str] = None, tiers_tried: Optional[list] = None,
                        roadblock: Optional[dict] = None) -> None:
        with self._lock:
            self._platforms[platform_id] = {
                "platform_id": platform_id,
                "platform_name": platform_name or platform_id,
                "tier": tier,                       # the tier that produced the kept results
                "tiers_tried": tiers_tried or [],   # [{tier, seconds, n}]
                "n_results": n_results,
                "elapsed": round(elapsed, 2),
                "error": error,
                "roadblock": roadblock,             # {reason, suggestion} or None
            }

    # ── read ───────────────────────────────────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            total = round(self._clock() - self._t0, 2) if self._t0 else 0.0
            nodes = list(self._nodes)
            platforms = list(self._platforms.values())
            # Slowest node and slowest platform — the headline "what's slow" facts.
            slowest_node = max(nodes, key=lambda n: n["seconds"], default=None)
            slowest_platform = max(platforms, key=lambda p: p["elapsed"], default=None)
            return {
                "total_seconds": total,
                "nodes": nodes,
                "platforms": platforms,
                "slowest_node": slowest_node,
                "slowest_platform": slowest_platform,
            }


_diag = RunDiagnostics()


def get_diagnostics() -> RunDiagnostics:
    return _diag

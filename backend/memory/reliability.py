"""Per-platform reliability tracking.

Every time the agent searches a platform, we record whether that attempt
returned usable results, errored, or timed out — plus how long it took.
Over time this builds a rolling success-rate per platform, which lets the
agent:

  1. Prefer platforms that consistently return good data when choosing
     which ones to search for a given query (see `rank_by_reliability`),
     instead of re-trying ones that reliably get blocked/time out.
  2. Show the user an honest "how well does this source usually work"
     signal in the UI (see `get_reliability` / `label_for`).

Storage is a small local SQLite database (stdlib `sqlite3`, no new
dependency) at `./data/reliability.db`. Mirrors the defensive style of
`backend/memory/store.py` — failures here are logged and swallowed, never
allowed to break a search.
"""
import os
import sqlite3
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("RELIABILITY_DB_PATH", "./data/reliability.db")

# A platform needs at least this many recent attempts before its track
# record is trusted enough to influence ranking/selection — protects
# new or rarely-used platforms from being judged on a single bad run.
MIN_SAMPLES = 4

# Only attempts within this window count toward the "current" score, so a
# platform that was blocked last month but works today isn't punished
# forever (anti-bot measures, site redesigns, and API keys change).
WINDOW_DAYS = 14

# How many of the most recent attempts (within the window) to weigh.
MAX_SAMPLES = 50

_lock = threading.Lock()
_initialized = False


def _connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn):
    global _initialized
    if _initialized:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS platform_outcomes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            platform_id     TEXT NOT NULL,
            platform_name   TEXT,
            success         INTEGER NOT NULL,   -- 1 = returned usable results, 0 = did not
            error           TEXT,               -- error string if any (NULL on clean success)
            result_count    INTEGER NOT NULL DEFAULT 0,
            elapsed_seconds REAL,
            recorded_at     TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_platform ON platform_outcomes(platform_id, recorded_at)")
    conn.commit()
    _initialized = True


def record_outcome(platform_id: str, platform_name: str, success: bool,
                   error: str | None = None, result_count: int = 0,
                   elapsed_seconds: float = 0.0):
    """Record one search attempt's outcome for a platform. Never raises."""
    if not platform_id:
        return
    try:
        with _lock, _connect() as conn:
            _ensure_schema(conn)
            conn.execute(
                """INSERT INTO platform_outcomes
                   (platform_id, platform_name, success, error, result_count,
                    elapsed_seconds, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (platform_id, platform_name, 1 if success else 0, error,
                 int(result_count or 0), float(elapsed_seconds or 0.0),
                 datetime.utcnow().isoformat()),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"Reliability record failed ({platform_id}): {e}")


def get_reliability(platform_id: str) -> dict:
    """Return this platform's recent track record.

    {
      "platform_id": "...",
      "samples": <int>,            # attempts considered (within window, capped)
      "success_rate": <0-100 or None>,  # None = not enough data yet
      "avg_elapsed": <float or None>,
      "trusted": <bool>,           # True once we have >= MIN_SAMPLES
      "label": "reliable" | "mixed" | "unreliable" | "unknown",
    }
    """
    out = {"platform_id": platform_id, "samples": 0, "success_rate": None,
           "avg_elapsed": None, "trusted": False, "label": "unknown"}
    try:
        cutoff = (datetime.utcnow() - timedelta(days=WINDOW_DAYS)).isoformat()
        with _lock, _connect() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """SELECT success, elapsed_seconds FROM platform_outcomes
                   WHERE platform_id = ? AND recorded_at >= ?
                   ORDER BY recorded_at DESC LIMIT ?""",
                (platform_id, cutoff, MAX_SAMPLES),
            ).fetchall()
    except Exception as e:
        logger.warning(f"Reliability lookup failed ({platform_id}): {e}")
        return out

    if not rows:
        return out

    n = len(rows)
    successes = sum(r["success"] for r in rows)
    rate = round(100 * successes / n, 1)
    elapsed_vals = [r["elapsed_seconds"] for r in rows if r["elapsed_seconds"]]
    out.update({
        "samples": n,
        "success_rate": rate,
        "avg_elapsed": round(sum(elapsed_vals) / len(elapsed_vals), 1) if elapsed_vals else None,
        "trusted": n >= MIN_SAMPLES,
    })
    out["label"] = label_for(rate, n)
    return out


def label_for(success_rate: float | None, samples: int) -> str:
    """Translate a success rate into a plain-English reliability label."""
    if success_rate is None or samples < MIN_SAMPLES:
        return "unknown"
    if success_rate >= 60:
        return "reliable"
    if success_rate >= 30:
        return "mixed"
    return "unreliable"


def get_all_reliability(platform_ids: list) -> dict:
    """Batch version of `get_reliability` for a list of platform IDs."""
    return {pid: get_reliability(pid) for pid in platform_ids}


def rank_by_reliability(platform_ids: list) -> list:
    """Reorder a list of platform IDs, putting the most reliable ones first.

    Platforms with no/insufficient track record (`trusted=False`) are treated
    as neutral (score 50) so newcomers still get a fair shot — only platforms
    we've *proven* are unreliable get pushed toward the back. This means a
    platform that's currently struggling (e.g. rate-limited) but has a
    long-run-good record won't be abandoned over one bad streak, while a
    platform that consistently fails gets naturally deprioritized over time.
    """
    if not platform_ids:
        return platform_ids
    scored = []
    for pid in platform_ids:
        info = get_reliability(pid)
        score = info["success_rate"] if info["trusted"] else 50.0
        scored.append((score, pid))
    # Stable sort preserves the original (LLM-chosen) relative order for ties —
    # so reliability nudges the order without overriding intent-based relevance.
    scored.sort(key=lambda x: -x[0])
    return [pid for _, pid in scored]

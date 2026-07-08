"""
Offline eval harness — measure the search pipeline systematically.

Runs a fixed set of representative queries (one per category) through the real
pipeline, captures the per-run diagnostics each search now attaches (stage timing,
per-platform timing + tier + result counts), and prints a summary plus saves a JSON
snapshot to data/evals/ so you can compare runs over time and catch regressions.

Run it:
    cd C:\\Users\\bella\\agent-aware
    .venv\\Scripts\\activate
    python evals/run_evals.py                      # full default suite
    python evals/run_evals.py "hotels in Goa"      # a single ad-hoc query

What to look at:
  • "slowest stage"     — almost always search_platforms; the rest is milliseconds.
  • per-platform Time   — which sites drag (usually the ones that fall to browser-use).
  • Tier used / Results — which platforms actually return data vs. fall through to 0.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

# Make the project importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from backend.graph import run_search


# One query per category — edit freely to match what you want to benchmark.
DEFAULT_SUITE = [
    ("flight",     "flights Mumbai to Delhi on 22 June"),
    ("hotel",      "hotels in Manali under 3000"),
    ("train",      "trains from Delhi to Agra"),
    ("bus",        "bus Bangalore to Chennai tonight"),
    ("product",    "iPhone 15 128GB price"),
    ("restaurant", "best pizza in Bangalore"),
]


def _summarize(label: str, query: str, state: dict) -> dict:
    diag = state.get("diagnostics") or {}
    platforms = diag.get("platforms", [])
    nodes = diag.get("nodes", [])
    with_results = [p for p in platforms if p.get("n_results")]
    return {
        "label": label,
        "query": query,
        "total_seconds": diag.get("total_seconds", 0),
        "slowest_node": diag.get("slowest_node"),
        "slowest_platform": diag.get("slowest_platform"),
        "platforms_searched": len(platforms),
        "platforms_with_results": len(with_results),
        "total_results": sum(p.get("n_results", 0) for p in platforms),
        "nodes": nodes,
        "platforms": platforms,
    }


def _print_run(s: dict) -> None:
    print(f"\n{'='*72}")
    print(f"[{s['label']}] {s['query']}")
    print(f"  total: {s['total_seconds']}s · "
          f"platforms with results: {s['platforms_with_results']}/{s['platforms_searched']} · "
          f"results: {s['total_results']}")
    sn = s.get("slowest_node") or {}
    print(f"  slowest stage: {sn.get('name','?')} ({sn.get('seconds','?')}s)")
    print(f"  {'platform':<22}{'results':>8}{'time':>8}  tier")
    for p in sorted(s["platforms"], key=lambda x: -x.get("elapsed", 0)):
        print(f"  {p.get('platform_name',''):<22}{p.get('n_results',0):>8}"
              f"{p.get('elapsed',0):>7}s  {p.get('tier') or '—'}")


def main() -> None:
    # Allow a single ad-hoc query from the command line.
    if len(sys.argv) > 1:
        suite = [("adhoc", " ".join(sys.argv[1:]))]
    else:
        suite = DEFAULT_SUITE

    runs = []
    for label, query in suite:
        print(f"\n▶ running [{label}] {query!r} …")
        try:
            state = run_search(query)
            s = _summarize(label, query, state)
        except Exception as e:
            s = {"label": label, "query": query, "error": str(e),
                 "total_seconds": 0, "platforms": [], "nodes": []}
            print(f"  ERROR: {e}")
        runs.append(s)
        if "error" not in s:
            _print_run(s)

    # Aggregate headline numbers across the suite.
    ok = [r for r in runs if "error" not in r]
    print(f"\n{'#'*72}\nSUITE SUMMARY ({len(ok)}/{len(runs)} ran clean)")
    if ok:
        avg_total = round(sum(r["total_seconds"] for r in ok) / len(ok), 1)
        worst = max(ok, key=lambda r: r["total_seconds"])
        print(f"  avg search time: {avg_total}s")
        print(f"  slowest query:   [{worst['label']}] {worst['total_seconds']}s")
        # Which platforms most often return nothing — reliability signal.
        empties: dict[str, int] = {}
        seen: dict[str, int] = {}
        for r in ok:
            for p in r["platforms"]:
                nm = p.get("platform_name", "?")
                seen[nm] = seen.get(nm, 0) + 1
                if not p.get("n_results"):
                    empties[nm] = empties.get(nm, 0) + 1
        if empties:
            print("  platforms returning 0 (count / times seen):")
            for nm, c in sorted(empties.items(), key=lambda kv: -kv[1]):
                print(f"    {nm:<22} {c}/{seen.get(nm,0)}")

    # Persist a timestamped snapshot for run-to-run comparison.
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "evals")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"eval_{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": stamp, "runs": runs}, f, indent=2)
    print(f"\nsaved → {out_path}")


if __name__ == "__main__":
    main()

"""
Dynamic comparison-dimension discovery.

The old approach used a *static* list of comparison columns per category in
config/platforms.yaml — the exact same factors for every flight search, every
hotel search, no matter what the results actually contained. That meant:
  - real attributes the listings DID expose but weren't pre-listed (seat pitch,
    in-flight meal, cancellation window, …) never showed up in the comparison, and
  - pre-listed factors that nothing in this particular result set mentioned still
    shaped the schema.

This module instead looks at the results that actually came back and decides what
is worth comparing:
  - a factor is included only if at least two results expose it (so there's
    genuinely something to line up side-by-side),
  - its data type (price / rating / duration / number / bool / text) and its
    "better" direction (lower / higher / true / none) are inferred from the values,
  - the static YAML metadata is still used — but only as an authoritative source of
    nice labels and known directions for factors it recognizes, never as a limit on
    what can appear.

The returned dicts match the shape the rest of the pipeline already expects
({key, label, type, better}), so insights/segregation consume them unchanged.
"""
from __future__ import annotations

import os
import re

import yaml

_CFG_CACHE = None

# Fields that carry the price — collapsed into a single "price" dimension.
_PRICE_KEYS = {"price", "price_per_night", "total_price", "price_per_day", "fare", "rate"}

# Never offered as a comparison factor (identity / linking / internal).
_SKIP_KEYS = {
    "name", "title", "car_model", "train_name", "operator", "vehicle",
    "url", "href", "link", "description", "price_raw", "raw", "snippet", "id",
}

_BOOL_TRUE = {"true", "yes", "1", "included", "free", "available", "refundable"}
_BOOL_FALSE = {"false", "no", "0", "not included", "unavailable", "n/a", "", "none"}

# Display priority by inferred type — higher sorts earlier in the matrix.
_TYPE_RANK = {"price": 6, "rating": 5, "duration": 4, "number": 3, "bool": 2, "text": 1}


def _load_cfg() -> dict:
    global _CFG_CACHE
    if _CFG_CACHE is None:
        path = os.path.join(os.path.dirname(__file__), "../config/platforms.yaml")
        with open(os.path.normpath(path), encoding="utf-8") as f:
            _CFG_CACHE = yaml.safe_load(f)
    return _CFG_CACHE


def _static_meta(intent_type: str) -> dict:
    """{key: {label, type, better}} from YAML — used only for labels/directions."""
    cat = _load_cfg().get("categories", {}).get(intent_type, {})
    return {d["key"]: d for d in cat.get("comparison_dimensions", [])}


# ── Value-shape detectors ─────────────────────────────────────

def _is_number(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    s = re.sub(r"[^\d.]", "", str(v))
    return bool(s) and s.count(".") <= 1


def _is_bool_like(v) -> bool:
    if isinstance(v, bool):
        return True
    return str(v).strip().lower() in (_BOOL_TRUE | _BOOL_FALSE)


def _is_duration_like(v) -> bool:
    s = str(v).lower()
    return bool(re.search(r"\d+\s*h", s) or re.search(r"\d+\s*hr", s)
                or re.search(r"\bh\s*\d+\s*m", s))


def _frac(vals, pred) -> float:
    vals = [v for v in vals if v is not None and str(v).strip() != ""]
    if not vals:
        return 0.0
    return sum(1 for v in vals if pred(v)) / len(vals)


def _infer_type(key: str, vals: list) -> str:
    kl = key.lower()
    if any(w in kl for w in ("rating", "stars", "score")) and _frac(vals, _is_number) >= 0.6:
        return "rating"
    if "duration" in kl and _frac(vals, _is_duration_like) >= 0.5:
        return "duration"
    if _frac(vals, _is_bool_like) >= 0.6:
        return "bool"
    if _frac(vals, _is_number) >= 0.6:
        return "number"
    return "text"


def _infer_better(key: str, dtype: str) -> str:
    kl = key.lower()
    if dtype == "bool":
        return "true"
    if dtype == "rating":
        return "higher"
    if dtype == "duration":
        return "lower"
    if any(w in kl for w in ("stop", "price", "fare", "cost", "delay")):
        return "lower"
    return "none"


def _label(key: str) -> str:
    return key.replace("_", " ").strip().title()


# ── Discovery ─────────────────────────────────────────────────

def discover_dimensions(results: list[dict], intent_type: str,
                        max_dims: int = 8, min_coverage: int = 2) -> list[dict]:
    """Return the comparison factors actually worth showing for THIS result set.

    `results` is the list of result dicts being compared (e.g. each platform's
    best option, or all normalized results). A factor must appear in at least
    `min_coverage` results to be included. At most `max_dims` factors are returned.
    """
    if not results:
        return []

    static = _static_meta(intent_type)
    dims: list[dict] = []

    # 1. Price always leads, if any result carries one.
    if any(any(r.get(k) not in (None, "") for k in _PRICE_KEYS) for r in results):
        pd = next((static[k] for k in ("price", "price_per_night", "price_per_day")
                   if k in static), None)
        dims.append(dict(pd) if pd else
                    {"key": "price", "label": "Price", "type": "price", "better": "lower"})

    # 2. Tally every other field's values across the result set.
    buckets: dict[str, list] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            if k.startswith("_") or k in _PRICE_KEYS or k in _SKIP_KEYS:
                continue
            if v is None or str(v).strip() == "":
                continue
            buckets.setdefault(k, []).append(v)

    # 3. Keep factors with enough coverage; type/direction from data or static meta.
    discovered = []
    for k, vals in buckets.items():
        if len(vals) < min_coverage:
            continue
        if k in static:
            dim = dict(static[k])
        else:
            dtype = _infer_type(k, vals)
            dim = {"key": k, "label": _label(k), "type": dtype,
                   "better": _infer_better(k, dtype)}
        dim["_coverage"] = len(vals)
        discovered.append(dim)

    # 4. Order: most informative types first, then by how many results expose it.
    discovered.sort(key=lambda d: (-_TYPE_RANK.get(d.get("type", "text"), 1),
                                   -d.get("_coverage", 0)))
    dims += discovered

    # 5. Cap, and strip the internal coverage marker.
    out = dims[:max_dims]
    for d in out:
        d.pop("_coverage", None)
    return out

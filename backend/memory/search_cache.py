"""RAG-based search result cache — reuse a recent, semantically-similar past search
instead of re-running the full tier cascade (Tavily → browser-use → parse LLM call).

Why this exists: browser-use is the slowest AND most LLM-hungry tier (one call per
navigation step), and `_parse_with_groq` fires an LLM call on every tier that returns
snippets. Re-running both for a query that's effectively identical to one we already
answered minutes ago wastes time and quota. This cache short-circuits the ENTIRE
per-platform tier cascade on a hit, so it's the only optimization here that cuts both
wall-clock and LLM usage at once — a bare result-dict cache would still cost a parse
call on every read.

Design constraints (this app's existing "never fabricate" principle applies to staleness
too — a cached price silently shown as live would be dishonest):
  - Scoped EXACTLY to (platform_id, intent_type) via a `where` filter — never serves
    Booking.com's cache for an Airbnb query, even if the embedding is close.
  - Short TTL for price-volatile categories (flight/hotel/train/bus/car_rental),
    longer for slower-moving ones (product/event/restaurant/general).
  - High similarity bar (cosine distance) — near-identical params only, not "similar".
  - Every call is defensive: any failure here must never break a live search.
"""
import os
import json
import logging
import hashlib
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CHROMA_PATH = os.getenv("CHROMADB_PATH", "./data/chroma")
_COLLECTION_NAME = "search_cache"

_VOLATILE_CATEGORIES = {"flight", "hotel", "train", "bus", "car_rental"}


def _enabled() -> bool:
    return os.getenv("SEARCH_CACHE_ENABLED", "true").lower() in ("1", "true", "yes")


def _ttl_minutes(intent_type: str) -> float:
    key = "SEARCH_CACHE_TTL_MINUTES" if intent_type in _VOLATILE_CATEGORIES \
        else "SEARCH_CACHE_TTL_MINUTES_STABLE"
    default = "20" if intent_type in _VOLATILE_CATEGORIES else "60"
    try:
        return float(os.getenv(key, default))
    except ValueError:
        return float(default)


def _max_distance() -> float:
    """Cosine distance ceiling for a hit — lower = stricter. 0 = identical embedding."""
    try:
        return float(os.getenv("SEARCH_CACHE_MAX_DISTANCE", "0.15"))
    except ValueError:
        return 0.15


_collection = None


def _get_collection():
    global _collection
    if _collection is not None:
        return _collection
    import chromadb
    from chromadb.utils import embedding_functions
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    _collection = client.get_or_create_collection(
        _COLLECTION_NAME, embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    return _collection


def _canonical_text(intent_type: str, platform_id: str, params: dict) -> str:
    kv = " ".join(f"{k}={v}" for k, v in sorted((params or {}).items())
                  if v not in (None, "", [], {}))
    return f"{intent_type} on {platform_id}: {kv}"


def _doc_id(intent_type: str, platform_id: str, params: dict) -> str:
    canon = _canonical_text(intent_type, platform_id, params)
    return hashlib.md5(canon.encode()).hexdigest()


def get_cached(intent_type: str, platform_id: str, params: dict) -> list[dict] | None:
    """Return cached results for a near-identical (platform, params) search made
    within the freshness window, or None on a miss / any failure (never raises)."""
    if not _enabled():
        return None
    try:
        collection = _get_collection()
        text = _canonical_text(intent_type, platform_id, params)
        res = collection.query(
            query_texts=[text], n_results=1,
            where={"$and": [{"platform_id": platform_id}, {"intent_type": intent_type}]},
        )
        if not res["ids"] or not res["ids"][0]:
            return None
        distance = res["distances"][0][0]
        if distance > _max_distance():
            return None
        meta = res["metadatas"][0][0]
        cached_at = datetime.fromisoformat(meta["cached_at"])
        age_minutes = (datetime.now(timezone.utc) - cached_at).total_seconds() / 60.0
        if age_minutes > _ttl_minutes(intent_type):
            return None
        results = json.loads(meta["results_json"])
        logger.info(f"  Cache HIT for {platform_id} ({intent_type}): "
                    f"{len(results)} results, {age_minutes:.1f}m old, distance={distance:.3f}")
        return results
    except Exception as e:
        logger.debug(f"Search cache lookup skipped ({platform_id}): {e}")
        return None


def set_cached(intent_type: str, platform_id: str, params: dict, results: list[dict]) -> None:
    """Store this platform's results for future reuse. Overwrites any prior entry for
    the same (platform, params) — the cache holds one fresh copy per query, not history."""
    if not _enabled() or not results:
        return
    try:
        collection = _get_collection()
        text = _canonical_text(intent_type, platform_id, params)
        doc_id = _doc_id(intent_type, platform_id, params)
        collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[{
                "platform_id": platform_id,
                "intent_type": intent_type,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "results_json": json.dumps(results),
            }],
        )
    except Exception as e:
        logger.debug(f"Search cache write skipped ({platform_id}): {e}")

import os
import json
import logging
import hashlib
from datetime import datetime
from typing import Optional

from backend.security.redact import redact_dict, wrap_untrusted

logger = logging.getLogger(__name__)

CHROMA_PATH = os.getenv("CHROMADB_PATH", "./data/chroma")


def _get_collection():
    import chromadb
    from chromadb.utils import embedding_functions
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    return client.get_or_create_collection("price_history", embedding_function=ef)


def store_results(intent_type: str, query_params: dict, platform_id: str, results: list):
    """Persist search results to ChromaDB for future price context."""
    if not results:
        return
    try:
        collection = _get_collection()
        # Scraped result content is untrusted web text — redact PII before it's
        # persisted to disk (ChromaDB is unencrypted local files) or ever fed back
        # into an LLM prompt via get_price_context().
        safe_params = redact_dict(query_params)
        for i, result in enumerate(results[:5]):  # store top 5 per platform
            safe_result = redact_dict(result)
            doc_text = f"{intent_type} | {json.dumps(safe_params)} | {platform_id} | {json.dumps(safe_result)}"
            doc_id = hashlib.md5(doc_text.encode()).hexdigest()
            collection.upsert(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{
                    "intent_type": intent_type,
                    "platform_id": platform_id,
                    "price": str(safe_result.get("price", "")),
                    "date": datetime.utcnow().isoformat(),
                    "query_params": json.dumps(safe_params),
                }],
            )
    except Exception as e:
        logger.warning(f"ChromaDB store failed: {e}")


def get_price_context(intent_type: str, query_params: dict, n: int = 10) -> str:
    """Retrieve historical price data for context in recommendations."""
    try:
        collection = _get_collection()
        query_text = f"{intent_type} {json.dumps(query_params)}"
        results = collection.query(query_texts=[query_text], n_results=n)
        if not results["documents"] or not results["documents"][0]:
            return ""
        docs = results["documents"][0]
        raw = "Historical price context:\n" + "\n".join(f"- {d}" for d in docs[:5])
        # PII was already redacted at write-time (store_results), but injection
        # phrasing wasn't filtered until now — a compromised listing's "ignore
        # previous instructions" text could otherwise ride along verbatim into
        # the recommend prompt below.
        return wrap_untrusted(raw, source="retrieved price history")
    except Exception as e:
        logger.warning(f"ChromaDB query failed: {e}")
        return ""

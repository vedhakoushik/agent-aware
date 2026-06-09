import os
import json
import logging
import hashlib
from datetime import datetime
from typing import Optional

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
        for i, result in enumerate(results[:5]):  # store top 5 per platform
            doc_text = f"{intent_type} | {json.dumps(query_params)} | {platform_id} | {json.dumps(result)}"
            doc_id = hashlib.md5(doc_text.encode()).hexdigest()
            collection.upsert(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{
                    "intent_type": intent_type,
                    "platform_id": platform_id,
                    "price": str(result.get("price", "")),
                    "date": datetime.utcnow().isoformat(),
                    "query_params": json.dumps(query_params),
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
        return "Historical price context:\n" + "\n".join(f"- {d}" for d in docs[:5])
    except Exception as e:
        logger.warning(f"ChromaDB query failed: {e}")
        return ""

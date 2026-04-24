"""
Alf-E Vector Memory — semantic long-term memory via Qdrant.

Embeds conversation exchanges using Google text-embedding-004 (768 dims)
and stores them in Qdrant for cross-session semantic recall.

Gracefully degrades: if Qdrant or the embedding API is unavailable,
all operations return empty results rather than crashing Alf-E.
"""

import os
import logging
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger("alfe.vector_memory")

QDRANT_URL      = os.getenv("QDRANT_URL", "http://qdrant:6333")
COLLECTION_NAME = "alfe_memories"
VECTOR_SIZE     = 768   # text-embedding-004 output dimension


def _get_qdrant():
    try:
        from qdrant_client import QdrantClient
        return QdrantClient(url=QDRANT_URL, timeout=5)
    except Exception as e:
        logger.debug(f"Qdrant unavailable: {e}")
        return None


def _embed(text: str) -> Optional[list[float]]:
    """Embed text using Google text-embedding-004. Returns None on failure."""
    try:
        import google.generativeai as genai
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None
        genai.configure(api_key=api_key)
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=text,
            task_type="retrieval_document",
        )
        return result["embedding"]
    except Exception as e:
        logger.debug(f"Embedding failed: {e}")
        return None


def _ensure_collection(client) -> bool:
    """Create the Qdrant collection if it doesn't exist."""
    try:
        from qdrant_client.models import Distance, VectorParams
        existing = [c.name for c in client.get_collections().collections]
        if COLLECTION_NAME not in existing:
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            logger.info(f"Qdrant collection '{COLLECTION_NAME}' created")
        return True
    except Exception as e:
        logger.warning(f"Qdrant collection setup failed: {e}")
        return False


def store_memory(
    user_msg: str,
    assistant_msg: str,
    user_id: str = "fraser",
    tags: list[str] = None,
) -> bool:
    """Embed and store a conversation exchange in Qdrant. Returns True on success."""
    client = _get_qdrant()
    if not client:
        return False
    if not _ensure_collection(client):
        return False

    text = f"User: {user_msg}\nAssistant: {assistant_msg}"
    vector = _embed(text)
    if not vector:
        return False

    try:
        from qdrant_client.models import PointStruct
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={
                        "user_id":       user_id,
                        "user_msg":      user_msg[:1000],
                        "assistant_msg": assistant_msg[:2000],
                        "tags":          tags or [],
                        "timestamp":     datetime.now().isoformat(),
                    },
                )
            ],
        )
        logger.debug(f"Stored memory for {user_id}: {user_msg[:60]}…")
        return True
    except Exception as e:
        logger.warning(f"Qdrant store failed: {e}")
        return False


def search_memory(
    query: str,
    user_id: str = None,
    limit: int = 5,
) -> list[dict]:
    """Semantic search across stored memories. Returns list of matching exchanges."""
    client = _get_qdrant()
    if not client:
        return []

    vector = _embed(query)
    if not vector:
        return []

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        query_filter = None
        if user_id:
            query_filter = Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            )

        results = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=0.5,
        )

        return [
            {
                "score":         round(r.score, 3),
                "user_msg":      r.payload.get("user_msg", ""),
                "assistant_msg": r.payload.get("assistant_msg", ""),
                "timestamp":     r.payload.get("timestamp", "")[:16],
                "tags":          r.payload.get("tags", []),
            }
            for r in results
        ]
    except Exception as e:
        logger.warning(f"Qdrant search failed: {e}")
        return []


def store_fact(fact: str, user_id: str = "fraser", tags: list[str] = None) -> bool:
    """Store an explicit fact/note (not a conversation exchange)."""
    return store_memory(
        user_msg="[explicit memory]",
        assistant_msg=fact,
        user_id=user_id,
        tags=(tags or []) + ["explicit"],
    )


def get_collection_info() -> dict:
    """Return basic stats about the memory collection."""
    client = _get_qdrant()
    if not client:
        return {"status": "unavailable"}
    try:
        info = client.get_collection(COLLECTION_NAME)
        return {
            "status":  "ok",
            "vectors": info.vectors_count,
            "points":  info.points_count,
        }
    except Exception:
        return {"status": "empty"}

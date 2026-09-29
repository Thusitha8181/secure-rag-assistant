"""Qdrant collection schema shared by ingestion and retrieval."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import lru_cache

from qdrant_client import QdrantClient, models

from app.config import get_settings
from app.ingestion.models import Chunk
from app.retrieval.embeddings import Embedder

log = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "bm25"


@lru_cache
def get_qdrant() -> QdrantClient:
    s = get_settings()
    return QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key, timeout=30)


def ensure_collection(
    client: QdrantClient, name: str, dense_dim: int, recreate: bool = False
) -> None:
    if recreate and client.collection_exists(name):
        log.info("Dropping collection %s", name)
        client.delete_collection(name)
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE: models.VectorParams(size=dense_dim, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    # Payload indexes make the RBAC filter cheap and exact.
    for field in ("allowed_roles", "department", "doc_type"):
        client.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD)
    log.info("Created collection %s (dense dim=%d)", name, dense_dim)


def upsert_chunks(
    client: QdrantClient,
    name: str,
    chunks: Sequence[Chunk],
    embedder: Embedder,
    batch_size: int = 64,
) -> int:
    total = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        dense, sparse = embedder.embed_documents([c.text for c in batch])
        points = [
            models.PointStruct(
                id=c.id,
                vector={
                    DENSE: d,
                    SPARSE: models.SparseVector(indices=s.indices, values=s.values),
                },
                payload=c.payload(),
            )
            for c, d, s in zip(batch, dense, sparse, strict=True)
        ]
        client.upsert(collection_name=name, points=points, wait=True)
        total += len(points)
    return total

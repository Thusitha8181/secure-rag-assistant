"""RBAC-filtered hybrid retrieval.

Security model: the role filter is applied *inside* Qdrant (on every prefetch and on
the fused query), so unauthorized chunks are never returned to the application. A
second, independent check drops anything whose payload does not list the caller's
role - if that ever fires it is logged as a security event (it indicates a bug or
tampered data), and the chunk is discarded.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient, models

from app.config import get_settings
from app.rbac.policy import Role
from app.retrieval.embeddings import Embedder, Reranker, get_embedder, get_reranker
from app.retrieval.store import DENSE, SPARSE, get_qdrant

log = logging.getLogger(__name__)
security_log = logging.getLogger("security")


@dataclass
class RetrievedChunk:
    id: str
    text: str
    source: str
    title: str
    section: str
    department: str
    doc_type: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    # Best dense cosine similarity among *authorized* chunks (RRF scores are rank-based).
    top_similarity: float
    # Departments the caller cannot read that look highly relevant. Only the department
    # name is ever exposed - never content.
    restricted_departments: list[str] = field(default_factory=list)
    # Best dense similarity among chunks the caller may NOT read (scores only, no payload).
    restricted_similarity: float = 0.0
    # Cross-encoder scores of the best authorized and best restricted chunk (None when reranking
    # is disabled). Restricted text is scored in-process only and never leaves the retriever.
    best_rerank_score: float | None = None
    restricted_rerank_score: float | None = None


def rbac_filter(role: Role | str) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="allowed_roles", match=models.MatchValue(value=Role(role).value)
            )
        ]
    )


def _not_allowed_filter(role: Role | str) -> models.Filter:
    return models.Filter(
        must_not=[
            models.FieldCondition(
                key="allowed_roles", match=models.MatchValue(value=Role(role).value)
            )
        ]
    )


class Retriever:
    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        embedder: Embedder,
        top_k: int = 6,
        relevance_threshold: float = 0.62,
        reranker: Reranker | None = None,
        rerank_candidates: int = 20,
    ) -> None:
        self.client = client
        self.collection = collection
        self.embedder = embedder
        self.top_k = top_k
        self.relevance_threshold = relevance_threshold
        self.reranker = reranker
        self.rerank_candidates = rerank_candidates

    def search(self, query: str, role: Role | str, k: int | None = None) -> RetrievalResult:
        role = Role(role)
        k = k or self.top_k
        n_candidates = max(k, self.rerank_candidates) if self.reranker else k
        dense, sparse = self.embedder.embed_query(query)
        sparse_vec = models.SparseVector(indices=sparse.indices, values=sparse.values)
        allowed = rbac_filter(role)

        hybrid, dense_top, restricted = self.client.query_batch_points(
            self.collection,
            requests=[
                models.QueryRequest(
                    prefetch=[
                        models.Prefetch(
                            query=dense, using=DENSE, filter=allowed, limit=n_candidates * 2
                        ),
                        models.Prefetch(
                            query=sparse_vec, using=SPARSE, filter=allowed, limit=n_candidates * 2
                        ),
                    ],
                    query=models.FusionQuery(fusion=models.Fusion.RRF),
                    filter=allowed,
                    limit=n_candidates,
                    with_payload=True,
                ),
                models.QueryRequest(query=dense, using=DENSE, filter=allowed, limit=1),
                models.QueryRequest(
                    query=dense,
                    using=DENSE,
                    filter=_not_allowed_filter(role),
                    limit=3,
                    with_payload=["department", "text"] if self.reranker else ["department"],
                ),
            ],
        )

        chunks: list[RetrievedChunk] = []
        for p in hybrid.points:
            payload = p.payload or {}
            if role.value not in payload.get("allowed_roles", []):
                security_log.error(
                    "RBAC invariant violated: role=%s got chunk=%s dept=%s - dropped",
                    role.value,
                    p.id,
                    payload.get("department"),
                )
                continue
            chunks.append(
                RetrievedChunk(
                    id=str(p.id),
                    text=payload.get("text", ""),
                    source=payload.get("source", ""),
                    title=payload.get("title", ""),
                    section=payload.get("section", ""),
                    department=payload.get("department", ""),
                    doc_type=payload.get("doc_type", ""),
                    score=float(p.score),
                )
            )

        best_rerank_score: float | None = None
        restricted_rerank_score: float | None = None
        if self.reranker:
            restricted_texts = [str((p.payload or {}).get("text", "")) for p in restricted.points]
            scores = self.reranker.rerank(query, [c.text for c in chunks] + restricted_texts)
            for c, score in zip(chunks, scores, strict=False):
                c.score = score
            chunks.sort(key=lambda c: c.score, reverse=True)
            best_rerank_score = chunks[0].score if chunks else None
            restricted_rerank_score = max(scores[len(chunks) :], default=None)
        chunks = chunks[:k]

        top_similarity = float(dense_top.points[0].score) if dense_top.points else 0.0
        restricted_departments = sorted(
            {
                str((p.payload or {}).get("department"))
                for p in restricted.points
                if p.score >= self.relevance_threshold and p.score > top_similarity
            }
        )
        restricted_similarity = float(restricted.points[0].score) if restricted.points else 0.0
        return RetrievalResult(
            chunks,
            top_similarity,
            restricted_departments,
            restricted_similarity,
            best_rerank_score,
            restricted_rerank_score,
        )


@lru_cache
def get_retriever() -> Retriever:
    s = get_settings()
    return Retriever(
        get_qdrant(),
        s.collection_name,
        get_embedder(),
        s.retrieval_top_k,
        s.relevance_threshold,
        reranker=get_reranker(),
        rerank_candidates=s.rerank_candidates,
    )

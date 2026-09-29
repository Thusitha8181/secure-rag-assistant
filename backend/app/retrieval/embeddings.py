"""Local embeddings with FastEmbed (ONNX, no GPU, no API key).

Dense: BAAI/bge-small-en-v1.5 (384-d). Sparse: Qdrant/bm25 (keyword signal for hybrid search).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.config import get_settings


@dataclass(frozen=True)
class SparseVector:
    indices: list[int]
    values: list[float]


class Embedder(Protocol):
    dense_dim: int

    def embed_documents(self, texts: list[str]) -> tuple[list[list[float]], list[SparseVector]]: ...

    def embed_query(self, text: str) -> tuple[list[float], SparseVector]: ...


def _load(cls: Any, model_name: str, cache_dir: str | None) -> Any:
    # Prefer the local cache so a cached model never triggers a network round-trip.
    try:
        return cls(model_name=model_name, cache_dir=cache_dir, local_files_only=True)
    except Exception:
        return cls(model_name=model_name, cache_dir=cache_dir)


def _mark_bm25_cached(sparse: Any) -> None:
    # fastembed's BM25 declares files that do not exist in the HF repo ("mock.file",
    # "tamil.txt"), so its offline cache check always misses and it goes to the network.
    # Empty placeholders make cached loads fully offline (only English stopwords are used).
    model = getattr(sparse, "model", None)
    model_dir = getattr(model, "_model_dir", None)
    if model is None or model_dir is None:
        return
    try:
        desc = model._get_model_description(model.model_name)
        for name in [desc.model_file, *desc.additional_files]:
            (Path(model_dir) / name).touch(exist_ok=True)
    except (AttributeError, ValueError, OSError):
        pass


class FastEmbedEmbedder:
    def __init__(self, dense_model: str, sparse_model: str, cache_dir: str | None = None) -> None:
        from fastembed import SparseTextEmbedding, TextEmbedding

        self._dense = _load(TextEmbedding, dense_model, cache_dir)
        self._sparse = _load(SparseTextEmbedding, sparse_model, cache_dir)
        _mark_bm25_cached(self._sparse)
        self.dense_dim = len(next(iter(self._dense.embed(["dimension probe"]))))

    def embed_documents(self, texts: list[str]) -> tuple[list[list[float]], list[SparseVector]]:
        dense = [v.tolist() for v in self._dense.embed(texts)]
        sparse = [
            SparseVector(indices=s.indices.tolist(), values=s.values.tolist())
            for s in self._sparse.embed(texts)
        ]
        return dense, sparse

    def embed_query(self, text: str) -> tuple[list[float], SparseVector]:
        dense = next(iter(self._dense.query_embed(text))).tolist()
        s = next(iter(self._sparse.query_embed(text)))
        return dense, SparseVector(indices=s.indices.tolist(), values=s.values.tolist())


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    return FastEmbedEmbedder(s.dense_model, s.sparse_model, s.model_cache_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", action="store_true", help="Download models into the cache")
    args = parser.parse_args()
    if args.warmup:
        emb = get_embedder()
        print(f"Embedding models ready (dense dim={emb.dense_dim})")

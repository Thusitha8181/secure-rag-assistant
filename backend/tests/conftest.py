import hashlib
import math
import os
import re

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("USAGE_BACKEND", "memory")
os.environ.setdefault("EMF_ENABLED", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from app.config import get_settings
from app.retrieval.embeddings import SparseVector

get_settings.cache_clear()

DIM = 64


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _h(tok: str) -> int:
    return int(hashlib.md5(tok.encode()).hexdigest(), 16)


class FakeEmbedder:
    """Deterministic bag-of-words embedder: no model download, stable across runs."""

    dense_dim = DIM

    def _dense(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for t in _tokens(text):
            v[_h(t) % DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def _sparse(self, text: str) -> SparseVector:
        counts: dict[int, float] = {}
        for t in _tokens(text):
            i = _h(t) % 100_000
            counts[i] = counts.get(i, 0.0) + 1.0
        return SparseVector(indices=list(counts), values=list(counts.values()))

    def embed_documents(self, texts):
        return [self._dense(t) for t in texts], [self._sparse(t) for t in texts]

    def embed_query(self, text):
        return self._dense(text), self._sparse(text)


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()

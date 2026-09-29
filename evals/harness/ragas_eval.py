"""Ragas answer-quality metrics with a Groq judge and local FastEmbed embeddings.

Groq's free tier allows roughly 8K tokens per minute and 200K tokens per day per model, so
metrics are spread across two judge models that the assistant itself does not use (a separate
quota, and no model grading its own answers). Samples are scored with low concurrency, and the
OpenAI SDK honours Groq's ``retry-after`` headers, which absorbs most rate-limit responses.
"""

from __future__ import annotations

import asyncio
import logging
import os
import warnings
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from harness.client import CaseResult

log = logging.getLogger("evals.ragas")

METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _default_judges() -> tuple[str, str]:
    """``JUDGE_MODELS="strong,light"``: the strong model scores faithfulness and recall."""
    raw = os.getenv("JUDGE_MODELS", "qwen/qwen3.8-27b,openai/gpt-oss-safeguard-20b")
    strong, _, light = (m.strip() for m in raw.partition(","))
    return strong, light or strong


@dataclass
class RagasConfig:
    judge_models: tuple[str, str] = field(default_factory=_default_judges)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    concurrency: int = 2
    max_contexts: int = 4


def _fastembed_embedding(model_name: str) -> Any:
    from fastembed import TextEmbedding
    from ragas.embeddings.base import BaseRagasEmbedding

    cache_dir = os.getenv("MODEL_CACHE_DIR")
    try:
        model = TextEmbedding(model_name, cache_dir=cache_dir, local_files_only=True)
    except Exception:
        model = TextEmbedding(model_name, cache_dir=cache_dir)

    class FastEmbedEmbedding(BaseRagasEmbedding):  # type: ignore[misc]
        def embed_text(self, text: str, **kwargs: Any) -> list[float]:
            return [float(x) for x in next(iter(model.embed([text])))]

        async def aembed_text(self, text: str, **kwargs: Any) -> list[float]:
            return await asyncio.to_thread(self.embed_text, text)

        def embed_texts(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            return [[float(x) for x in v] for v in model.embed(texts)]

        async def aembed_texts(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            return await asyncio.to_thread(self.embed_texts, texts)

    return FastEmbedEmbedding()


def _judge(model: str) -> Any:
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory

    client = AsyncOpenAI(
        base_url=os.getenv("JUDGE_BASE_URL", GROQ_BASE_URL),
        api_key=os.environ["GROQ_API_KEY"],
        max_retries=8,
        timeout=120,
    )
    return llm_factory(model, provider="openai", client=client, temperature=0.0, max_tokens=4096)


def _build_metrics(cfg: RagasConfig) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextPrecisionWithReference,
            ContextRecall,
            Faithfulness,
        )
    big, small = (_judge(m) for m in cfg.judge_models)
    return {
        "faithfulness": Faithfulness(llm=big),
        "context_recall": ContextRecall(llm=big),
        "context_precision": ContextPrecisionWithReference(llm=small),
        "answer_relevancy": AnswerRelevancy(
            llm=small, embeddings=_fastembed_embedding(cfg.embedding_model), strictness=2
        ),
    }


async def _score_one(metrics: dict[str, Any], r: CaseResult, cfg: RagasConfig) -> dict[str, Any]:
    q, a, ref = r.case.question, r.answer, r.case.reference or ""
    ctx = r.contexts[: cfg.max_contexts]
    calls = {
        "faithfulness": lambda: metrics["faithfulness"].ascore(
            user_input=q, response=a, retrieved_contexts=ctx
        ),
        "answer_relevancy": lambda: metrics["answer_relevancy"].ascore(user_input=q, response=a),
        "context_precision": lambda: metrics["context_precision"].ascore(
            user_input=q, reference=ref, retrieved_contexts=ctx
        ),
        "context_recall": lambda: metrics["context_recall"].ascore(
            user_input=q, retrieved_contexts=ctx, reference=ref
        ),
    }
    scores: dict[str, Any] = {"id": r.case.id}
    for name, call in calls.items():
        try:
            res = await call()
            scores[name] = float(res.value) if res.value is not None else None
        except Exception as e:  # a single judge failure should not sink the run
            log.warning("%s: %s failed: %s", r.case.id, name, e)
            scores[name] = None
    return scores


async def _run(samples: list[CaseResult], cfg: RagasConfig) -> list[dict[str, Any]]:
    metrics = _build_metrics(cfg)
    sem = asyncio.Semaphore(cfg.concurrency)

    async def bounded(r: CaseResult) -> dict[str, Any]:
        async with sem:
            scores = await _score_one(metrics, r, cfg)
            log.info("ragas %s: %s", r.case.id, {k: v for k, v in scores.items() if k != "id"})
            return scores

    return await asyncio.gather(*(bounded(r) for r in samples))


def select_samples(results: list[CaseResult], limit: int) -> list[CaseResult]:
    eligible = [
        r
        for r in results
        if r.case.ragas and r.case.reference and r.status == "answered" and r.contexts
    ]
    return eligible[:limit]


def evaluate(samples: list[CaseResult], cfg: RagasConfig | None = None) -> dict[str, Any]:
    """Returns ``{"per_case": [...], "summary": {metric: mean, "ragas_coverage": share}}``.

    ``ragas_coverage`` is the share of judge scores that came back; a judge that runs out of
    quota would otherwise leave the quality gates silently skipped.
    """
    cfg = cfg or RagasConfig()
    if not samples:
        return {"per_case": [], "summary": dict.fromkeys(METRICS)}
    log.info("Ragas judges: %s", ", ".join(cfg.judge_models))
    per_case = asyncio.run(_run(samples, cfg))
    summary: dict[str, float | None] = {}
    scored = 0
    for m in METRICS:
        vals = [s[m] for s in per_case if s.get(m) is not None]
        summary[m] = mean(vals) if vals else None
        scored += len(vals)
    summary["ragas_coverage"] = scored / (len(per_case) * len(METRICS))
    return {"per_case": per_case, "summary": summary}

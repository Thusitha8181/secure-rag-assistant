"""Runs the pipeline for one request and wraps it with metering, metrics and usage records."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler

from app.auth.security import User
from app.config import Settings
from app.graph.pipeline import ChatState
from app.llm.pricing import RequestUsage, usage_from_langchain
from app.observability.metrics import MetricsEmitter
from app.observability.usage import UsageEvent, UsageStore

log = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    def __init__(self, used: int, quota: int) -> None:
        super().__init__(f"Daily token quota exceeded ({used}/{quota})")
        self.used = used
        self.quota = quota


@dataclass
class ChatResult:
    request_id: str
    status: str
    answer: str
    sources: list[dict[str, Any]]
    guardrails: list[dict[str, Any]]
    usage: dict[str, Any]
    latency_ms: float
    intent: str | None = None
    sql: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


class ChatService:
    def __init__(
        self, graph: Any, usage_store: UsageStore, metrics: MetricsEmitter, settings: Settings
    ) -> None:
        self.graph = graph
        self.usage_store = usage_store
        self.metrics = metrics
        self.settings = settings

    def check_quota(self, user: User) -> None:
        quota = self.settings.daily_token_quota
        if quota <= 0:
            return
        try:
            used = self.usage_store.tokens_used_today(user.username)
        except Exception:
            log.exception("Quota lookup failed; allowing request")
            return
        if used >= quota:
            raise QuotaExceededError(used, quota)

    def _inputs(self, user: User, question: str, history: list[dict[str, str]]) -> ChatState:
        return {
            "question": question,
            "history": history[-self.settings.max_history_turns * 2 :],
            "user": {
                "username": user.username,
                "name": user.name,
                "title": user.title,
                "role": user.role.value,
            },
            "guardrails": [],
        }

    def _config(
        self, user: User, request_id: str, cb: UsageMetadataCallbackHandler
    ) -> dict[str, Any]:
        return {
            "callbacks": [cb],
            "run_name": "chat",
            "tags": [f"role:{user.role.value}", f"env:{self.settings.environment}"],
            "metadata": {
                "request_id": request_id,
                "username": user.username,
                "role": user.role.value,
                "app_version": self.settings.app_version,
            },
        }

    def _finish(
        self,
        user: User,
        request_id: str,
        state: dict[str, Any],
        started: float,
        cb: UsageMetadataCallbackHandler,
    ) -> ChatResult:
        latency_ms = (time.perf_counter() - started) * 1000
        usage: RequestUsage = usage_from_langchain(cb.usage_metadata)
        status = state.get("status") or "error"
        guardrails = state.get("guardrails") or []
        result = ChatResult(
            request_id=request_id,
            status=status,
            answer=state.get("answer") or "",
            sources=state.get("sources") or [],
            guardrails=guardrails,
            usage=usage.summary(),
            latency_ms=round(latency_ms, 1),
            intent=state.get("intent"),
            sql=state.get("sql"),
        )
        try:
            self.metrics.request(
                role=user.role.value,
                status=status,
                latency_ms=latency_ms,
                usage=usage,
                guardrails=guardrails,
                request_id=request_id,
            )
            self.usage_store.record(
                UsageEvent(
                    request_id=request_id,
                    username=user.username,
                    role=user.role.value,
                    status=status,
                    latency_ms=latency_ms,
                    usage=usage,
                    guardrails=sorted({f"{g['name']}:{g['action']}" for g in guardrails}),
                )
            )
        except Exception:
            log.exception("Failed to record usage for %s", request_id)
        log.info(
            "chat request_id=%s user=%s role=%s status=%s latency_ms=%.0f tokens=%d cost_usd=%.6f",
            request_id,
            user.username,
            user.role.value,
            status,
            latency_ms,
            usage.total_tokens,
            usage.cost_usd,
        )
        return result

    async def run(self, user: User, question: str, history: list[dict[str, str]]) -> ChatResult:
        request_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        cb = UsageMetadataCallbackHandler()
        try:
            state = await self.graph.ainvoke(
                self._inputs(user, question, history), config=self._config(user, request_id, cb)
            )
        except Exception:
            log.exception("Pipeline failed for %s", request_id)
            state = {"status": "error", "answer": "Sorry, something went wrong. Please try again."}
        return self._finish(user, request_id, state, started, cb)

    async def stream(
        self, user: User, question: str, history: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        request_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        cb = UsageMetadataCallbackHandler()
        state: dict[str, Any] = {}
        streamed = False
        yield _sse("meta", {"request_id": request_id})
        try:
            async for mode, chunk in self.graph.astream(
                self._inputs(user, question, history),
                config=self._config(user, request_id, cb),
                stream_mode=["custom", "values"],
            ):
                if mode == "custom" and chunk.get("type") == "token":
                    streamed = True
                    yield _sse("token", {"text": chunk["text"]})
                elif mode == "values":
                    state = chunk
        except Exception:
            log.exception("Streaming pipeline failed for %s", request_id)
            state = {"status": "error", "answer": "Sorry, something went wrong. Please try again."}
        result = self._finish(user, request_id, state, started, cb)
        if not streamed and result.answer:
            yield _sse("token", {"text": result.answer})
        yield _sse("done", result.to_dict())

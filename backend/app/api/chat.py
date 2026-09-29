from functools import lru_cache
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user
from app.auth.security import User
from app.config import get_settings
from app.graph.pipeline import get_graph
from app.observability.metrics import MetricsEmitter
from app.observability.usage import get_usage_store
from app.services.chat import ChatService, QuotaExceededError

router = APIRouter(prefix="/api/chat", tags=["chat"])


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=40)


class ChatResponse(BaseModel):
    request_id: str
    status: str
    answer: str
    sources: list[dict[str, Any]]
    guardrails: list[dict[str, Any]]
    usage: dict[str, Any]
    latency_ms: float
    intent: str | None = None
    sql: str | None = None


@lru_cache
def get_chat_service() -> ChatService:
    s = get_settings()
    return ChatService(get_graph(), get_usage_store(), MetricsEmitter(s), s)


def _quota_guard(user: User, svc: ChatService) -> None:
    try:
        svc.check_quota(user)
    except QuotaExceededError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You have used your daily token quota ({exc.used:,}/{exc.quota:,}). It resets at 00:00 UTC.",
        ) from exc


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: User = Depends(get_current_user),
    svc: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    _quota_guard(user, svc)
    result = await svc.run(user, body.question, [t.model_dump() for t in body.history])
    return ChatResponse(**result.to_dict())


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    user: User = Depends(get_current_user),
    svc: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    _quota_guard(user, svc)
    return StreamingResponse(
        svc.stream(user, body.question, [t.model_dump() for t in body.history]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

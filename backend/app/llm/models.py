"""Groq chat model factory. All model names come from settings so they can be swapped via env."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_groq import ChatGroq

from app.config import Settings, get_settings


def chat_model(
    model: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_retries: int = 2,
    settings: Settings | None = None,
) -> ChatGroq:
    s = settings or get_settings()
    kwargs: dict[str, Any] = {}
    if "gpt-oss" in model:
        # Reasoning models: keep latency/tokens down for RAG answers.
        kwargs["reasoning_effort"] = "low"
    return ChatGroq(
        model=model,
        api_key=s.groq_api_key or None,  # type: ignore[arg-type]
        temperature=s.llm_temperature if temperature is None else temperature,
        max_tokens=max_tokens or s.llm_max_tokens,
        timeout=s.llm_timeout_s,
        max_retries=max_retries,
        **kwargs,
    )


def generator_model(settings: Settings | None = None) -> Runnable:
    """Answer generator with automatic fallback to a second model on errors / rate limits."""
    s = settings or get_settings()
    if not s.llm_fallback_model or s.llm_fallback_model == s.llm_model:
        return chat_model(s.llm_model, settings=s)
    # Fail over at once: retrying a daily-quota 429 on the primary only adds latency, and the
    # extra calls push the fallback past its per-minute limit too.
    primary = chat_model(s.llm_model, max_retries=0, settings=s)
    return primary.with_fallbacks([chat_model(s.llm_fallback_model, settings=s)])


def classifier_model(settings: Settings | None = None) -> ChatGroq:
    s = settings or get_settings()
    return chat_model(s.classifier_model, temperature=0.0, max_tokens=512, settings=s)


def prompt_guard_model(settings: Settings | None = None) -> BaseChatModel | None:
    s = settings or get_settings()
    if not s.prompt_guard_model:
        return None
    return chat_model(s.prompt_guard_model, temperature=0.0, max_tokens=16, settings=s)

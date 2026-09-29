from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, chat, health, usage
from app.config import get_settings
from app.observability.tracing import configure_tracing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("app")

INSECURE_SECRETS = {"", "dev-only-insecure-secret-change-me", "change-me-to-a-long-random-string"}


def _warm_up() -> None:
    """Load embedding models, spaCy/Presidio, the HR table and the graph before serving."""
    from app.api.chat import get_chat_service

    get_chat_service()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    if s.environment != "local" and s.jwt_secret in INSECURE_SECRETS:
        raise RuntimeError("JWT_SECRET must be set to a strong value outside local development")
    if not s.groq_api_key:
        log.warning("GROQ_API_KEY is not set - chat requests will fail")
    configure_tracing(s)
    await asyncio.to_thread(_warm_up)
    log.info("Starting secure-rag-assistant %s (%s)", s.app_version, s.environment)
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Secure RAG Assistant",
        version=s.app_version,
        description="RBAC-aware internal knowledge assistant",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(usage.router)
    return app


app = create_app()

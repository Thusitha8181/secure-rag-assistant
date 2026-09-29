# syntax=docker/dockerfile:1.7
# Build context is the repository root (so data/raw can be baked in).
#   docker build -f backend/Dockerfile --target api .
#   docker build -f backend/Dockerfile --target ingest .

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    MODEL_CACHE_DIR=/opt/models \
    HF_HOME=/opt/models/hf \
    PATH="/app/.venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./

# ---------- API image (slim: no docling / torch) ----------
FROM base AS api
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY data/raw ./data/raw
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev \
    && python -m app.retrieval.embeddings --warmup \
    && chown -R app:app /opt/models
# Models are baked in; never reach out to Hugging Face at runtime.
ENV HF_HUB_OFFLINE=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

# ---------- Ingestion job image (includes docling) ----------
FROM base AS ingest
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --group ingest --no-install-project
COPY backend/app ./app
COPY data/raw ./data/raw
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --group ingest \
    && python -m app.retrieval.embeddings --warmup \
    && chown -R app:app /opt/models
USER app
CMD ["python", "-m", "app.ingestion.run", "--recreate"]

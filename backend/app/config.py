from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


def _default_data_dir() -> Path:
    # Docker image bakes data into /app/data/raw; locally it lives at <repo>/data/raw.
    baked = BACKEND_DIR / "data" / "raw"
    return baked if baked.exists() else REPO_DIR / "data" / "raw"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "local"
    app_version: str = Field(default="dev", alias="APP_VERSION")

    # LLM
    groq_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    llm_fallback_model: str = "openai/gpt-oss-20b"
    classifier_model: str = "openai/gpt-oss-20b"
    prompt_guard_model: str = "meta-llama/llama-prompt-guard-2-86m"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1024
    llm_timeout_s: float = 60.0
    prompt_guard_threshold: float = 0.8

    # Vector store
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    collection_name: str = "company_docs"
    dense_model: str = "BAAI/bge-small-en-v1.5"
    sparse_model: str = "Qdrant/bm25"
    model_cache_dir: str | None = None
    retrieval_top_k: int = 6
    # Cosine similarity (dense) that counts as "relevant" for scope/access checks
    relevance_threshold: float = 0.62

    # Data
    data_dir: Path = Field(default_factory=_default_data_dir)
    hr_csv_relpath: str = "hr/hr_data.csv"

    # Auth
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 480
    demo_mode: bool = True
    users_file: Path = BACKEND_DIR / "app" / "auth" / "users.yaml"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Usage / cost
    usage_backend: Literal["memory", "dynamodb"] = "memory"
    usage_create_table: bool = False
    dynamodb_table: str = "secure-rag-usage"
    dynamodb_endpoint: str | None = None
    aws_region: str = "us-east-1"
    daily_token_quota: int = 200_000
    usage_retention_days: int = 90

    # Tracing (LangSmith reads os.environ, so these are exported at startup)
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "secure-rag-assistant"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # Metrics (CloudWatch Embedded Metric Format on stdout)
    emf_enabled: bool = False
    metrics_namespace: str = "SecureRagAssistant"

    # Conversation
    max_history_turns: int = 6

    @property
    def hr_csv_path(self) -> Path:
        return self.data_dir / self.hr_csv_relpath


@lru_cache
def get_settings() -> Settings:
    return Settings()

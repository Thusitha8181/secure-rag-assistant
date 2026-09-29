import logging
import os

from app.config import Settings

log = logging.getLogger(__name__)


def configure_tracing(s: Settings) -> bool:
    """Export LangSmith settings to the environment (the SDK reads os.environ, while our
    settings may come from .env or SSM). Every LangGraph node and LLM call is then traced."""
    enabled = s.langsmith_tracing and bool(s.langsmith_api_key)
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    if enabled:
        os.environ["LANGSMITH_API_KEY"] = s.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = s.langsmith_project
        os.environ["LANGSMITH_ENDPOINT"] = s.langsmith_endpoint
        log.info("LangSmith tracing enabled (project=%s)", s.langsmith_project)
    elif s.langsmith_tracing:
        log.warning("LANGSMITH_TRACING=true but LANGSMITH_API_KEY is empty; tracing disabled")
    return enabled

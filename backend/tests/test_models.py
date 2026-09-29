from langchain_core.runnables import RunnableWithFallbacks

from app.config import get_settings
from app.llm.models import generator_model


def test_generator_fails_over_without_retrying_the_primary() -> None:
    s = get_settings().model_copy(
        update={
            "groq_api_key": "test",
            "llm_model": "openai/gpt-oss-120b",
            "llm_fallback_model": "openai/gpt-oss-20b",
        }
    )
    gen = generator_model(s)
    assert isinstance(gen, RunnableWithFallbacks)
    assert gen.runnable.max_retries == 0  # type: ignore[attr-defined]
    assert gen.fallbacks[0].max_retries == 2  # type: ignore[attr-defined]


def test_generator_without_fallback_keeps_retries() -> None:
    s = get_settings().model_copy(update={"groq_api_key": "test", "llm_fallback_model": ""})
    assert generator_model(s).max_retries == 2  # type: ignore[attr-defined]

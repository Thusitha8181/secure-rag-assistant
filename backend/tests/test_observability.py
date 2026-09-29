import json

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_chat_service
from app.config import get_settings
from app.llm.pricing import cost_usd, usage_from_langchain
from app.main import create_app
from app.observability.metrics import MetricsEmitter
from app.observability.usage import MemoryUsageStore, UsageEvent, get_usage_store
from app.services.chat import ChatService


def test_cost_uses_price_table_and_default_for_unknown_models() -> None:
    assert cost_usd("openai/gpt-oss-120b", 1_000_000, 1_000_000) == pytest.approx(0.75)
    assert cost_usd("some/unknown-model", 1_000_000, 0) == pytest.approx(1.0)


def test_usage_from_langchain_aggregates_models() -> None:
    usage = usage_from_langchain(
        {
            "openai/gpt-oss-120b": {
                "input_tokens": 1000,
                "output_tokens": 200,
                "output_token_details": {"reasoning": 50},
            },
            "openai/gpt-oss-20b": {"input_tokens": 500, "output_tokens": 100},
        }
    )
    assert usage.total_tokens == 1800
    assert usage.by_model["openai/gpt-oss-120b"].reasoning_tokens == 50
    assert usage.cost_usd == pytest.approx(0.00015 + 0.00012 + 0.0000375 + 0.00003)


def test_emf_lines_are_valid(capsys) -> None:
    s = get_settings().model_copy(update={"emf_enabled": True, "environment": "test"})
    usage = usage_from_langchain({"openai/gpt-oss-120b": {"input_tokens": 10, "output_tokens": 5}})
    MetricsEmitter(s).request(
        role="finance",
        status="answered",
        latency_ms=123.4,
        usage=usage,
        guardrails=[],
        request_id="r1",
    )
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert len(lines) == 2
    main = lines[0]
    directive = main["_aws"]["CloudWatchMetrics"][0]
    assert directive["Namespace"] == s.metrics_namespace
    assert ["Environment", "Role"] in directive["Dimensions"]
    for metric in directive["Metrics"]:
        assert metric["Name"] in main
    assert main["TotalTokens"] == 15 and main["Role"] == "finance"
    assert lines[1]["Model"] == "openai/gpt-oss-120b"


def _event(user: str, role: str, tokens: int, status: str = "answered") -> UsageEvent:
    return UsageEvent(
        request_id="x",
        username=user,
        role=role,
        status=status,
        latency_ms=10,
        usage=usage_from_langchain(
            {"openai/gpt-oss-120b": {"input_tokens": tokens, "output_tokens": 0}}
        ),
    )


def test_memory_store_aggregates_and_summarises() -> None:
    store = MemoryUsageStore(quota=1000)
    store.record(_event("a", "finance", 300))
    store.record(_event("a", "finance", 200, status="blocked"))
    store.record(_event("b", "hr", 100))
    assert store.tokens_used_today("a") == 500
    summary = store.summary(7)
    assert summary.totals["requests"] == 3
    assert summary.totals["blocked"] == 1
    assert {r["role"] for r in summary.by_role} == {"finance", "hr"}
    assert summary.by_model[0]["model"] == "openai/gpt-oss-120b"
    assert len(summary.recent) == 3


class _FakeGraph:
    async def ainvoke(self, inputs, config):
        return {
            "status": "answered",
            "answer": "ok [1]",
            "sources": [],
            "guardrails": [],
            "intent": "company_question",
        }

    async def astream(self, inputs, config, stream_mode):
        yield "custom", {"type": "token", "text": "ok "}
        yield "values", {"status": "answered", "answer": "ok", "sources": [], "guardrails": []}


@pytest.fixture
def api():
    store = MemoryUsageStore(quota=250)
    settings = get_settings().model_copy(update={"daily_token_quota": 250})
    svc = ChatService(_FakeGraph(), store, MetricsEmitter(settings), settings)
    app = create_app()
    app.dependency_overrides[get_chat_service] = lambda: svc
    app.dependency_overrides[get_usage_store] = lambda: store
    client = TestClient(app)

    def token(username: str) -> str:
        return client.post(
            "/api/auth/login", json={"username": username, "password": "demo1234"}
        ).json()["access_token"]

    return client, store, token


def test_chat_requires_auth(api) -> None:
    client, _, _ = api
    assert client.post("/api/chat", json={"question": "hi"}).status_code == 401


def test_chat_json_and_stream(api) -> None:
    client, _, token = api
    h = {"Authorization": f"Bearer {token('evan.employee')}"}
    res = client.post("/api/chat", json={"question": "leave policy?"}, headers=h)
    assert res.status_code == 200 and res.json()["status"] == "answered"
    with client.stream("POST", "/api/chat/stream", json={"question": "leave?"}, headers=h) as r:
        body = "".join(r.iter_text())
    assert "event: token" in body and "event: done" in body


def test_quota_exceeded_returns_429(api) -> None:
    client, store, token = api
    store.record(_event("evan.employee", "employee", 300))
    h = {"Authorization": f"Bearer {token('evan.employee')}"}
    res = client.post("/api/chat", json={"question": "hi"}, headers=h)
    assert res.status_code == 429
    assert client.post("/api/chat/stream", json={"question": "hi"}, headers=h).status_code == 429


def test_usage_summary_is_c_level_only(api) -> None:
    client, _, token = api
    assert (
        client.get(
            "/api/usage/summary", headers={"Authorization": f"Bearer {token('fiona.finance')}"}
        ).status_code
        == 403
    )
    ok = client.get("/api/usage/summary", headers={"Authorization": f"Bearer {token('cara.ceo')}"})
    assert ok.status_code == 200 and "by_role" in ok.json()
    me = client.get("/api/usage/me", headers={"Authorization": f"Bearer {token('fiona.finance')}"})
    assert me.json()["daily_token_quota"] == get_settings().daily_token_quota

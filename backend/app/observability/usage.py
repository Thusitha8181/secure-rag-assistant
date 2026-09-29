"""Per-request usage records, daily aggregates and quota lookups.

DynamoDB single-table layout (on-demand billing, TTL on `expires_at`):
  PK=DAY#<date>     SK=USER#<username>   atomic ADD counters per user per day (quota + dashboard)
  PK=DAY#<date>     SK=MODEL#<model>     atomic ADD counters per model per day
  PK=EVENTS#<date>  SK=<iso-ts>#<id>     one item per request (no question text is stored)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.llm.pricing import RequestUsage

log = logging.getLogger(__name__)


class UsageEvent(BaseModel):
    request_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    username: str
    role: str
    status: str
    latency_ms: float
    usage: RequestUsage
    guardrails: list[str] = []


class DailyTotals(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    blocked: int = 0
    errors: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class UsageSummary(BaseModel):
    days: int
    totals: dict[str, Any]
    by_day: list[dict[str, Any]]
    by_user: list[dict[str, Any]]
    by_role: list[dict[str, Any]]
    by_model: list[dict[str, Any]]
    recent: list[dict[str, Any]]
    daily_token_quota: int


class UsageStore(Protocol):
    def record(self, event: UsageEvent) -> None: ...
    def tokens_used_today(self, username: str) -> int: ...
    def user_day(self, username: str, day: date) -> DailyTotals: ...
    def summary(self, days: int) -> UsageSummary: ...


def _today() -> date:
    return datetime.now(UTC).date()


def _is_blocked(e: UsageEvent) -> int:
    return 1 if e.status in {"blocked", "out_of_scope", "access_denied"} else 0


def _build_summary(
    days: int,
    user_rows: list[tuple[date, str, dict[str, Any]]],
    model_rows: list[tuple[date, str, dict[str, Any]]],
    recent: list[dict[str, Any]],
    quota: int,
) -> UsageSummary:
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_user: dict[str, dict[str, Any]] = {}
    by_role: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_model: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    keys = ("requests", "input_tokens", "output_tokens", "cost_usd", "blocked", "errors")
    for d, user, row in user_rows:
        for k in keys:
            v = float(row.get(k, 0) or 0)
            by_day[d.isoformat()][k] += v
            by_role[row.get("role", "unknown")][k] += v
        u = by_user.setdefault(
            user, {"username": user, "role": row.get("role", "unknown"), **dict.fromkeys(keys, 0.0)}
        )
        for k in keys:
            u[k] += float(row.get(k, 0) or 0)
    for _d, model, row in model_rows:
        for k in ("requests", "input_tokens", "output_tokens", "cost_usd"):
            by_model[model][k] += float(row.get(k, 0) or 0)

    totals = {k: sum(v[k] for v in by_day.values()) for k in keys}
    return UsageSummary(
        days=days,
        totals=totals,
        by_day=[{"date": d, **v} for d, v in sorted(by_day.items())],
        by_user=sorted(by_user.values(), key=lambda r: -r["cost_usd"]),
        by_role=[
            {"role": r, **v} for r, v in sorted(by_role.items(), key=lambda kv: -kv[1]["cost_usd"])
        ],
        by_model=[
            {"model": m, **v}
            for m, v in sorted(by_model.items(), key=lambda kv: -kv[1]["cost_usd"])
        ],
        recent=recent,
        daily_token_quota=quota,
    )


def _event_row(e: UsageEvent) -> dict[str, Any]:
    return {
        "request_id": e.request_id,
        "timestamp": e.timestamp.isoformat(),
        "username": e.username,
        "role": e.role,
        "status": e.status,
        "latency_ms": round(e.latency_ms, 1),
        "input_tokens": e.usage.input_tokens,
        "output_tokens": e.usage.output_tokens,
        "cost_usd": e.usage.cost_usd,
        "models": sorted(e.usage.by_model),
        "guardrails": e.guardrails,
    }


class MemoryUsageStore:
    def __init__(self, quota: int) -> None:
        self.quota = quota
        self._lock = threading.Lock()
        self._users: dict[tuple[date, str], dict[str, Any]] = {}
        self._models: dict[tuple[date, str], dict[str, Any]] = {}
        self._events: list[UsageEvent] = []

    def record(self, event: UsageEvent) -> None:
        d = event.timestamp.date()
        with self._lock:
            row = self._users.setdefault((d, event.username), {"role": event.role})
            for k, v in {
                "requests": 1,
                "input_tokens": event.usage.input_tokens,
                "output_tokens": event.usage.output_tokens,
                "cost_usd": event.usage.cost_usd,
                "blocked": _is_blocked(event),
                "errors": 1 if event.status == "error" else 0,
            }.items():
                row[k] = row.get(k, 0) + v
            for model, mu in event.usage.by_model.items():
                m = self._models.setdefault((d, model), {})
                for k, v in {
                    "requests": 1,
                    "input_tokens": mu.input_tokens,
                    "output_tokens": mu.output_tokens,
                    "cost_usd": mu.cost_usd,
                }.items():
                    m[k] = m.get(k, 0) + v
            self._events.append(event)
            self._events = self._events[-500:]

    def user_day(self, username: str, day: date) -> DailyTotals:
        return DailyTotals(
            **{k: v for k, v in self._users.get((day, username), {}).items() if k != "role"}
        )

    def tokens_used_today(self, username: str) -> int:
        return self.user_day(username, _today()).total_tokens

    def summary(self, days: int) -> UsageSummary:
        start = _today() - timedelta(days=days - 1)
        users = [(d, u, r) for (d, u), r in self._users.items() if d >= start]
        models = [(d, m, r) for (d, m), r in self._models.items() if d >= start]
        recent = [_event_row(e) for e in reversed(self._events[-25:])]
        return _build_summary(days, users, models, recent, self.quota)


def _dec(v: float) -> Decimal:
    return Decimal(str(round(v, 8)))


class DynamoUsageStore:
    def __init__(self, s: Settings) -> None:
        import boto3

        self.quota = s.daily_token_quota
        self.retention_s = s.usage_retention_days * 86400
        resource = boto3.resource(
            "dynamodb", region_name=s.aws_region, endpoint_url=s.dynamodb_endpoint or None
        )
        self.table = resource.Table(s.dynamodb_table)
        if s.usage_create_table:
            self._ensure_table(resource, s.dynamodb_table)

    @staticmethod
    def _ensure_table(resource: Any, name: str) -> None:
        client = resource.meta.client
        try:
            client.describe_table(TableName=name)
            return
        except client.exceptions.ResourceNotFoundException:
            pass
        log.info("Creating DynamoDB table %s", name)
        client.create_table(
            TableName=name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName=name)

    def record(self, event: UsageEvent) -> None:
        d = event.timestamp.date().isoformat()
        ttl = int(time.time()) + self.retention_s
        u = event.usage
        self.table.update_item(
            Key={"PK": f"DAY#{d}", "SK": f"USER#{event.username}"},
            UpdateExpression=(
                "ADD requests :one, input_tokens :i, output_tokens :o, cost_usd :c, blocked :b, errors :e "
                "SET #role = :role, expires_at = :ttl"
            ),
            ExpressionAttributeNames={"#role": "role"},
            ExpressionAttributeValues={
                ":one": 1,
                ":i": u.input_tokens,
                ":o": u.output_tokens,
                ":c": _dec(u.cost_usd),
                ":b": _is_blocked(event),
                ":e": 1 if event.status == "error" else 0,
                ":role": event.role,
                ":ttl": ttl,
            },
        )
        for model, mu in u.by_model.items():
            self.table.update_item(
                Key={"PK": f"DAY#{d}", "SK": f"MODEL#{model}"},
                UpdateExpression="ADD requests :one, input_tokens :i, output_tokens :o, cost_usd :c SET expires_at = :ttl",
                ExpressionAttributeValues={
                    ":one": 1,
                    ":i": mu.input_tokens,
                    ":o": mu.output_tokens,
                    ":c": _dec(mu.cost_usd),
                    ":ttl": ttl,
                },
            )
        row = _event_row(event)
        row["cost_usd"] = _dec(row["cost_usd"])
        row["latency_ms"] = _dec(row["latency_ms"])
        self.table.put_item(
            Item={
                "PK": f"EVENTS#{d}",
                "SK": f"{row['timestamp']}#{event.request_id}",
                "expires_at": ttl,
                **row,
            }
        )

    def user_day(self, username: str, day: date) -> DailyTotals:
        key = {"PK": f"DAY#{day.isoformat()}", "SK": f"USER#{username}"}
        item: dict[str, Any] = self.table.get_item(Key=key).get("Item") or {}
        # Pydantic coerces DynamoDB Decimals to the declared int/float fields.
        return DailyTotals.model_validate(
            {k: v for k, v in item.items() if k in DailyTotals.model_fields}
        )

    def tokens_used_today(self, username: str) -> int:
        return self.user_day(username, _today()).total_tokens

    def _query_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while True:
            resp = self.table.query(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                return items
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def summary(self, days: int) -> UsageSummary:
        from boto3.dynamodb.conditions import Key

        users: list[tuple[date, str, dict[str, Any]]] = []
        models: list[tuple[date, str, dict[str, Any]]] = []
        today = _today()
        for i in range(days):
            d = today - timedelta(days=i)
            for item in self._query_all(
                KeyConditionExpression=Key("PK").eq(f"DAY#{d.isoformat()}")
            ):
                sk: str = item["SK"]
                if sk.startswith("USER#"):
                    users.append((d, sk[5:], item))
                elif sk.startswith("MODEL#"):
                    models.append((d, sk[6:], item))
        resp = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"EVENTS#{today.isoformat()}"),
            ScanIndexForward=False,
            Limit=25,
        )
        recent = [
            {
                k: (float(v) if isinstance(v, Decimal) else v)
                for k, v in item.items()
                if k not in {"PK", "SK", "expires_at"}
            }
            for item in resp.get("Items", [])
        ]
        return _build_summary(days, users, models, recent, self.quota)


@lru_cache
def get_usage_store() -> UsageStore:
    s = get_settings()
    if s.usage_backend == "dynamodb":
        return DynamoUsageStore(s)
    return MemoryUsageStore(s.daily_token_quota)

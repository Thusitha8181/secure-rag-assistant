from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

PRICING_FILE = Path(__file__).with_name("pricing.yaml")


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class RequestUsage(BaseModel):
    by_model: dict[str, ModelUsage] = {}

    @property
    def input_tokens(self) -> int:
        return sum(m.input_tokens for m in self.by_model.values())

    @property
    def output_tokens(self) -> int:
        return sum(m.output_tokens for m in self.by_model.values())

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        return round(sum(m.cost_usd for m in self.by_model.values()), 8)

    def summary(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "by_model": {k: v.model_dump() for k, v in self.by_model.items()},
        }


@lru_cache
def _prices() -> dict[str, Any]:
    return yaml.safe_load(PRICING_FILE.read_text())


def price_for(model: str) -> tuple[float, float]:
    data = _prices()
    p = data["models"].get(model) or data["default"]
    return float(p["input"]), float(p["output"])


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = price_for(model)
    return round((input_tokens * pin + output_tokens * pout) / 1_000_000, 8)


def usage_from_langchain(usage_metadata: Mapping[str, Mapping[str, Any]]) -> RequestUsage:
    """Convert LangChain's per-model usage (UsageMetadataCallbackHandler) into priced usage."""
    by_model: dict[str, ModelUsage] = {}
    for model, u in usage_metadata.items():
        inp = int(u.get("input_tokens", 0) or 0)
        out = int(u.get("output_tokens", 0) or 0)
        reasoning = int((u.get("output_token_details") or {}).get("reasoning", 0) or 0)
        by_model[model] = ModelUsage(
            input_tokens=inp,
            output_tokens=out,
            reasoning_tokens=reasoning,
            cost_usd=cost_usd(model, inp, out),
        )
    return RequestUsage(by_model=by_model)

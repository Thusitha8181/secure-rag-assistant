"""CloudWatch metrics via Embedded Metric Format (EMF).

Each request writes one JSON line to stdout. On ECS the awslogs driver ships it to CloudWatch
Logs, which extracts the metrics automatically - no PutMetricData calls, no extra IAM, no
added latency. Alarms and the dashboard (see infra/terraform) are built on these metrics.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from app.config import Settings
from app.llm.pricing import RequestUsage


def _emf(
    namespace: str,
    dimensions: list[list[str]],
    metrics: dict[str, tuple[float, str]],
    props: dict[str, Any],
) -> str:
    doc: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": dimensions,
                    "Metrics": [{"Name": k, "Unit": unit} for k, (_, unit) in metrics.items()],
                }
            ],
        },
        **props,
        **{k: v for k, (v, _) in metrics.items()},
    }
    return json.dumps(doc, separators=(",", ":"))


class MetricsEmitter:
    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.emf_enabled
        self.namespace = settings.metrics_namespace
        self.environment = settings.environment

    def _write(self, line: str) -> None:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def request(
        self,
        *,
        role: str,
        status: str,
        latency_ms: float,
        usage: RequestUsage,
        guardrails: list[dict[str, Any]],
        request_id: str,
    ) -> None:
        if not self.enabled:
            return
        blocked = status in {"blocked", "out_of_scope"}
        pii = any(g["name"].startswith("pii") and g["action"] == "redacted" for g in guardrails)
        props = {
            "Environment": self.environment,
            "Role": role,
            "Status": status,
            "RequestId": request_id,
        }
        metrics: dict[str, tuple[float, str]] = {
            "Requests": (1, "Count"),
            "LatencyMs": (round(latency_ms, 1), "Milliseconds"),
            "InputTokens": (usage.input_tokens, "Count"),
            "OutputTokens": (usage.output_tokens, "Count"),
            "TotalTokens": (usage.total_tokens, "Count"),
            "CostUSD": (usage.cost_usd, "None"),
            "GuardrailBlocked": (1 if blocked else 0, "Count"),
            "AccessDenied": (1 if status == "access_denied" else 0, "Count"),
            "PIIRedacted": (1 if pii else 0, "Count"),
            "Errors": (1 if status == "error" else 0, "Count"),
        }
        self._write(
            _emf(self.namespace, [["Environment"], ["Environment", "Role"]], metrics, props)
        )
        for model, mu in usage.by_model.items():
            self._write(
                _emf(
                    self.namespace,
                    [["Environment", "Model"]],
                    {
                        "ModelInputTokens": (mu.input_tokens, "Count"),
                        "ModelOutputTokens": (mu.output_tokens, "Count"),
                        "ModelCostUSD": (mu.cost_usd, "None"),
                    },
                    {"Environment": self.environment, "Model": model, "RequestId": request_id},
                )
            )

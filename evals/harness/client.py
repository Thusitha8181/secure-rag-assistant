"""HTTP client for the assistant API: one demo login per role, retries for transient failures."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from harness.datasets import Case

log = logging.getLogger("evals.client")

DEMO_USERS = {
    "finance": "fiona.finance",
    "marketing": "mark.marketing",
    "hr": "hana.hr",
    "engineering": "eli.engineering",
    "c_level": "cara.ceo",
    "employee": "evan.employee",
}


@dataclass
class CaseResult:
    case: Case
    status: str = "error"
    answer: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    guardrails: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    total_tokens: int = 0
    request_id: str | None = None
    http_error: str | None = None

    @property
    def contexts(self) -> list[str]:
        return [s.get("text", "") for s in self.sources if s.get("text")]

    @property
    def guardrail_names(self) -> set[str]:
        return {g.get("name", "") for g in self.guardrails}


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        # 429 from the per-user quota is not transient; the message says so.
        if code == 429 and "quota" in exc.response.text.lower():
            return False
        return code == 429 or code >= 500
    return False


class AssistantClient:
    def __init__(
        self,
        base_url: str,
        password: str,
        timeout: float = 120.0,
        retry_errors: int = 2,
        error_backoff_s: float = 20.0,
    ) -> None:
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self.password = password
        self.retry_errors = retry_errors
        self.error_backoff_s = error_backoff_s
        self._tokens: dict[str, str] = {}

    def close(self) -> None:
        self.http.close()

    @retry(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=2, max=30),
        reraise=True,
    )
    def _post(self, path: str, json: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = self.http.post(path, json=json, headers=headers)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    def wait_ready(self, timeout_s: float = 180.0) -> None:
        deadline = time.monotonic() + timeout_s
        last = ""
        while time.monotonic() < deadline:
            try:
                resp = self.http.get("/api/health/ready")
                if resp.status_code == 200:
                    return
                last = resp.text
            except httpx.TransportError as e:
                last = str(e)
            time.sleep(3)
        raise RuntimeError(f"API not ready after {timeout_s:.0f}s: {last}")

    def token(self, role: str) -> str:
        if role not in self._tokens:
            data = self._post(
                "/api/auth/login", {"username": DEMO_USERS[role], "password": self.password}
            )
            self._tokens[role] = data["access_token"]
        return self._tokens[role]

    def ask(self, case: Case) -> CaseResult:
        result = CaseResult(case)
        for attempt in range(self.retry_errors + 1):
            try:
                data = self._post(
                    "/api/chat", {"question": case.question, "history": []}, self.token(case.role)
                )
            except httpx.HTTPError as e:
                result.http_error = str(e)
                log.warning("%s: HTTP failure: %s", case.id, e)
                return result
            result = CaseResult(
                case,
                status=data["status"],
                answer=data.get("answer", ""),
                sources=data.get("sources", []),
                guardrails=data.get("guardrails", []),
                latency_ms=float(data.get("latency_ms", 0.0)),
                cost_usd=float(data.get("usage", {}).get("cost_usd", 0.0)),
                total_tokens=int(data.get("usage", {}).get("total_tokens", 0)),
                request_id=data.get("request_id"),
            )
            # "error" usually means the LLM provider rate-limited us even after its own retries.
            if result.status != "error" or attempt == self.retry_errors:
                return result
            log.info("%s: status=error, retrying in %.0fs", case.id, self.error_backoff_s)
            time.sleep(self.error_backoff_s)
        return result

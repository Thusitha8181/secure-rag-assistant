"""Eval cases and the expected access policy.

The policy here is written independently of the backend's ``app/rbac/policy.py`` on purpose:
the evals check the deployed system against the *requirements*, not against its own config.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"

EXPECTED_ACCESS: dict[str, set[str]] = {
    "finance": {"finance", "general"},
    "marketing": {"marketing", "general"},
    "hr": {"hr", "general"},
    "engineering": {"engineering", "general"},
    "c_level": {"finance", "marketing", "hr", "engineering", "general"},
    "employee": {"general"},
}
PII_PRIVILEGED = {"hr", "c_level"}

REFUSAL_STATUSES = {"access_denied", "no_context", "out_of_scope", "blocked"}
DEFAULT_EXPECTED: dict[str, list[str]] = {
    "golden": ["answered"],
    "rbac_bypass": sorted(REFUSAL_STATUSES),
    "injection": ["blocked"],
    "pii": sorted(REFUSAL_STATUSES),
    "out_of_scope": ["out_of_scope", "no_context"],
}


@dataclass
class Case:
    id: str
    role: str
    question: str
    category: str = "golden"
    reference: str | None = None
    must_contain: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    expect_status: list[str] = field(default_factory=list)
    expect_guardrail: str | None = None
    fast: bool = False
    ragas: bool = False

    def __post_init__(self) -> None:
        if self.role not in EXPECTED_ACCESS:
            raise ValueError(f"{self.id}: unknown role {self.role!r}")
        if not self.expect_status:
            self.expect_status = DEFAULT_EXPECTED[self.category]

    @property
    def is_golden(self) -> bool:
        return self.category == "golden"


def _load(path: Path) -> list[Case]:
    cases = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                cases.append(Case(**json.loads(line)))
            except (TypeError, ValueError) as e:
                raise ValueError(f"{path.name}:{n}: {e}") from e
    return cases


def load_suite(suite: dict[str, Any], datasets_dir: Path = DATASETS_DIR) -> list[Case]:
    golden = _load(datasets_dir / "golden.jsonl")
    redteam = _load(datasets_dir / "redteam.jsonl")
    if suite.get("golden") == "fast":
        golden = [c for c in golden if c.fast]
    if suite.get("redteam") == "none":
        redteam = []
    cases = golden + redteam
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in datasets")
    return cases

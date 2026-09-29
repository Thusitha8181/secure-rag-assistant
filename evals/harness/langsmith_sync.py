"""Publish an eval run to LangSmith as an experiment over a versioned dataset.

The dataset is synced from the JSONL files (LangSmith versions every change). The experiment's
"target" replays responses already collected by the harness, so nothing hits the API twice.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from harness.checks import CaseCheck
from harness.client import CaseResult
from harness.datasets import Case

log = logging.getLogger("evals.langsmith")

DATASET_NAME = "secure-rag-assistant-evals"
_NS = uuid.UUID("5b0a4f3e-8f59-4c1e-9a55-6b8f3c2f1d10")


def _example_id(case: Case) -> uuid.UUID:
    return uuid.uuid5(_NS, case.id)


def _example(case: Case) -> dict[str, Any]:
    return {
        "inputs": {"case_id": case.id, "role": case.role, "question": case.question},
        "outputs": {"reference": case.reference, "expected_status": case.expect_status},
        "metadata": {"category": case.category, "fast": case.fast},
    }


def _sync_dataset(client: Any, cases: list[Case]) -> Any:
    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
    else:
        dataset = client.create_dataset(
            DATASET_NAME,
            description="Golden and red-team cases for the Secure RAG Assistant (evals/datasets)",
        )
    existing = {ex.id: ex for ex in client.list_examples(dataset_id=dataset.id)}
    for case in cases:
        ex_id = _example_id(case)
        body = _example(case)
        current = existing.get(ex_id)
        if current is None:
            client.create_example(dataset_id=dataset.id, example_id=ex_id, **body)
        elif current.inputs != body["inputs"] or current.outputs != body["outputs"]:
            client.update_example(ex_id, **body)
    return dataset


def publish(
    results: list[CaseResult],
    checks: list[CaseCheck],
    ragas_per_case: list[dict[str, Any]],
    experiment_prefix: str,
    metadata: dict[str, Any],
) -> str | None:
    from langsmith import Client, evaluate

    client = Client()
    cases = [r.case for r in results]
    _sync_dataset(client, cases)

    by_id = {r.case.id: r for r in results}
    check_by_id = {c.id: c for c in checks}
    ragas_by_id = {s["id"]: s for s in ragas_per_case}

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        r = by_id[inputs["case_id"]]
        return {
            "status": r.status,
            "answer": r.answer,
            "sources": [s.get("source") for s in r.sources],
            "latency_ms": r.latency_ms,
            "cost_usd": r.cost_usd,
        }

    def scores(run: Any, example: Any) -> dict[str, Any]:
        case_id = example.inputs["case_id"]
        chk = check_by_id[case_id]
        res: list[dict[str, Any]] = [
            {"key": "passed", "score": int(chk.passed)},
            {"key": "status_ok", "score": int(chk.status_ok)},
            {"key": "rbac_leak", "score": int(chk.rbac_leak)},
            {"key": "pii_leak", "score": int(chk.pii_leak)},
        ]
        if chk.keyword_recall is not None:
            res.append({"key": "keyword_recall", "score": chk.keyword_recall})
        for k, v in ragas_by_id.get(case_id, {}).items():
            if k != "id" and v is not None:
                res.append({"key": k, "score": v})
        return {"results": res}

    examples = [
        ex
        for ex in client.list_examples(dataset_name=DATASET_NAME)
        if ex.inputs.get("case_id") in by_id
    ]
    exp = evaluate(
        target,
        data=examples,
        evaluators=[scores],
        experiment_prefix=experiment_prefix,
        metadata=metadata,
        max_concurrency=4,
        client=client,
    )
    name: str | None = getattr(exp, "experiment_name", None)
    log.info("LangSmith experiment: %s", name)
    return name

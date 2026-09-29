"""Run the eval suite against a running Secure RAG Assistant API and enforce release gates.

    uv run python run_evals.py --api-url http://localhost:8000 --suite fast

Exit codes: 0 all gates passed, 1 a gate failed, 2 the run itself could not complete.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from harness import checks as checks_mod
from harness import ragas_eval
from harness.client import AssistantClient, CaseResult
from harness.datasets import load_suite

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("evals")


def load_dotenv(path: Path) -> None:
    """Minimal .env loader; never overrides variables already set (e.g. CI secrets)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m and not line.lstrip().startswith("#"):
            key, value = m.groups()
            os.environ.setdefault(key, value.strip().strip("'\""))


def git_sha() -> str:
    sha = os.getenv("GITHUB_SHA")
    if sha:
        return sha[:7]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT
        )
        return out.stdout.strip() or "local"
    except OSError:
        return "local"


def evaluate_gates(
    metrics: dict[str, Any], gates: dict[str, dict[str, float]]
) -> list[dict[str, Any]]:
    rows = []
    for name, rule in gates.items():
        value = metrics.get(name)
        if value is None:
            rows.append({"metric": name, "value": None, "rule": rule, "result": "skipped"})
            continue
        ok = True
        if "min" in rule and value < rule["min"]:
            ok = False
        if "max" in rule and value > rule["max"]:
            ok = False
        rows.append(
            {"metric": name, "value": value, "rule": rule, "result": "pass" if ok else "FAIL"}
        )
    return rows


def _fmt(value: Any, metric: str) -> str:
    if value is None:
        return "-"
    if metric.endswith("_usd"):
        return f"${value:.5f}"
    if metric.endswith("_ms"):
        return f"{value:,.0f} ms"
    return f"{value:.3f}"


def _rule(rule: dict[str, float], metric: str) -> str:
    parts = []
    if "min" in rule:
        parts.append(f">= {_fmt(rule['min'], metric)}")
    if "max" in rule:
        parts.append(f"<= {_fmt(rule['max'], metric)}")
    return ", ".join(parts)


def markdown_report(
    suite: str,
    api_url: str,
    gate_rows: list[dict[str, Any]],
    failures: Iterable[checks_mod.CaseCheck],
    totals: dict[str, Any],
    experiment: str | None,
) -> str:
    passed = all(r["result"] != "FAIL" for r in gate_rows)
    lines = [
        f"## Eval results: `{suite}` suite {'✅ passed' if passed else '❌ failed'}",
        "",
        f"Target `{api_url}` · {totals['cases']} cases · {totals['duration_s']:.0f}s · "
        f"LLM cost ${totals['cost_usd']:.4f} · {totals['tokens']:,} tokens"
        + (f" · LangSmith experiment `{experiment}`" if experiment else ""),
        "",
        "| Metric | Value | Gate | Result |",
        "|---|---|---|---|",
    ]
    for r in gate_rows:
        icon = {"pass": "✅", "FAIL": "❌", "skipped": "⏭️"}[r["result"]]
        m = r["metric"]
        lines.append(f"| {m} | {_fmt(r['value'], m)} | {_rule(r['rule'], m)} | {icon} |")
    failed = list(failures)
    if failed:
        lines += [
            "",
            "### Failing cases",
            "",
            "| Case | Role | Status | Problem |",
            "|---|---|---|---|",
        ]
        for c in failed:
            problems = []
            if not c.status_ok:
                problems.append("unexpected status")
            if c.restricted_sources:
                problems.append(f"restricted sources: {', '.join(c.restricted_sources)}")
            if c.forbidden_hits:
                problems.append(f"forbidden content: {', '.join(c.forbidden_hits)}")
            if c.pii:
                problems.append(f"PII: {', '.join(c.pii)}")
            if c.guardrail_ok is False:
                problems.append("expected guardrail missing")
            if c.errored:
                problems.append("error")
            lines.append(f"| {c.id} | {c.role} | {c.status} | {'; '.join(problems)} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--api-url", default=os.getenv("API_URL", "http://localhost:8000"))
    p.add_argument("--suite", choices=["fast", "full"], default="fast")
    p.add_argument("--thresholds", type=Path, default=ROOT / "thresholds.yaml")
    p.add_argument("--out", type=Path, default=ROOT / "results")
    p.add_argument("--only", help="regex over case ids, for debugging")
    p.add_argument(
        "--delay",
        type=float,
        default=float(os.getenv("EVAL_DELAY_S", "1.0")),
        help="seconds between API calls (provider rate limits)",
    )
    p.add_argument("--no-ragas", action="store_true")
    p.add_argument("--no-langsmith", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "langsmith", "instructor"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_dotenv(ROOT.parent / ".env")

    config = yaml.safe_load(args.thresholds.read_text())
    suite_cfg = config["suites"][args.suite]
    cases = load_suite(suite_cfg)
    if args.only:
        cases = [c for c in cases if re.search(args.only, c.id)]
    log.info("Suite %s: %d cases against %s", args.suite, len(cases), args.api_url)

    started = time.monotonic()
    client = AssistantClient(args.api_url, password=os.getenv("EVAL_PASSWORD", "demo1234"))
    try:
        client.wait_ready()
        results: list[CaseResult] = []
        for i, case in enumerate(cases, 1):
            r = client.ask(case)
            results.append(r)
            log.info(
                "[%d/%d] %s %s -> %s (%.0f ms)",
                i,
                len(cases),
                case.id,
                case.role,
                r.status,
                r.latency_ms,
            )
            time.sleep(args.delay)
    except Exception:
        log.exception("Eval run aborted")
        return 2
    finally:
        client.close()

    case_checks = [checks_mod.check_case(r) for r in results]
    metrics = checks_mod.aggregate(results, case_checks)

    ragas_out: dict[str, Any] = {"per_case": [], "summary": {}}
    if not args.no_ragas:
        if os.getenv("GROQ_API_KEY"):
            samples = ragas_eval.select_samples(results, suite_cfg.get("ragas_max", 5))
            log.info("Ragas on %d samples", len(samples))
            ragas_out = ragas_eval.evaluate(samples)
            metrics.update(ragas_out["summary"])
        else:
            log.warning("GROQ_API_KEY not set; skipping Ragas metrics")

    experiment = None
    if not args.no_langsmith and os.getenv("LANGSMITH_API_KEY"):
        try:
            from harness import langsmith_sync

            experiment = langsmith_sync.publish(
                results,
                case_checks,
                ragas_out["per_case"],
                experiment_prefix=f"{args.suite}-{git_sha()}",
                metadata={"suite": args.suite, "api_url": args.api_url, "git_sha": git_sha()},
            )
        except Exception as e:  # tracing backend issues must not block a release
            log.warning("LangSmith upload failed: %s", e)

    gate_rows = evaluate_gates(metrics, config["gates"])
    totals = {
        "cases": len(results),
        "duration_s": time.monotonic() - started,
        "cost_usd": sum(r.cost_usd for r in results),
        "tokens": sum(r.total_tokens for r in results),
    }
    report = markdown_report(
        args.suite,
        args.api_url,
        gate_rows,
        [c for c in case_checks if not c.passed],
        totals,
        experiment,
    )

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "suite": args.suite,
        "api_url": args.api_url,
        "git_sha": git_sha(),
        "timestamp": stamp,
        "totals": totals,
        "metrics": metrics,
        "gates": gate_rows,
        "langsmith_experiment": experiment,
        "cases": [
            {
                **asdict(chk),
                "question": r.case.question,
                "answer": r.answer,
                "sources": [s.get("source") for s in r.sources],
                "latency_ms": r.latency_ms,
                "cost_usd": r.cost_usd,
                "request_id": r.request_id,
            }
            for r, chk in zip(results, case_checks, strict=True)
        ],
        "ragas": ragas_out["per_case"],
    }
    (args.out / f"{stamp}-{args.suite}.json").write_text(json.dumps(payload, indent=2, default=str))
    (args.out / f"latest-{args.suite}.json").write_text(json.dumps(payload, indent=2, default=str))
    (args.out / f"latest-{args.suite}.md").write_text(report)
    if summary_path := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(report)
    print(report)

    return 1 if any(r["result"] == "FAIL" for r in gate_rows) else 0


if __name__ == "__main__":
    sys.exit(main())

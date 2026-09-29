#!/usr/bin/env python3
"""Read `terraform output -json github_variables` from stdin and write GitHub Actions repo variables."""

from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print("no terraform output on stdin", file=sys.stderr)
        return 1
    values = json.loads(raw)
    if not isinstance(values, dict) or not values:
        print("expected a JSON object of name -> value", file=sys.stderr)
        return 1
    for key, value in values.items():
        subprocess.check_call(["gh", "variable", "set", key, "--body", str(value)])
        print(f"set {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

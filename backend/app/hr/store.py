"""In-memory DuckDB over the HR CSV for aggregate questions (averages, counts, rankings).

Hardening for LLM-generated SQL:
* exactly one statement, and it must be a SELECT (parsed by DuckDB itself, not regex)
* external access disabled + configuration locked after load (no read_csv('/etc/passwd'))
* results capped by an outer LIMIT
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb

from app.config import get_settings

TABLE = "employees"
MAX_ROWS = 50


class UnsafeSQLError(ValueError):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]

    def as_markdown(self) -> str:
        if not self.rows:
            return "(no rows)"
        header = "| " + " | ".join(self.columns) + " |"
        sep = "| " + " | ".join("---" for _ in self.columns) + " |"
        body = ["| " + " | ".join(str(v) for v in row) + " |" for row in self.rows]
        return "\n".join([header, sep, *body])


class HRStore:
    def __init__(self, csv_path: Path) -> None:
        self._lock = threading.Lock()
        self._con = duckdb.connect(":memory:")
        self._con.execute(
            f"CREATE TABLE {TABLE} AS SELECT * FROM read_csv_auto(?, header=true)", [str(csv_path)]
        )
        self._con.execute("SET enable_external_access = false")
        self._con.execute("SET lock_configuration = true")
        self.schema = self._describe()

    def _describe(self) -> str:
        cols = self._con.execute(f"DESCRIBE {TABLE}").fetchall()
        return f"TABLE {TABLE} (\n" + ",\n".join(f"  {c[0]} {c[1]}" for c in cols) + "\n)"

    @staticmethod
    def validate(sql: str) -> str:
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            raise UnsafeSQLError("empty query")
        try:
            statements = duckdb.extract_statements(sql)
        except duckdb.Error as exc:
            raise UnsafeSQLError(f"unparseable SQL: {exc}") from exc
        if len(statements) != 1:
            raise UnsafeSQLError("exactly one statement is allowed")
        if statements[0].type != duckdb.StatementType.SELECT:
            raise UnsafeSQLError(f"only SELECT is allowed, got {statements[0].type.name}")
        return sql

    def query(self, sql: str) -> QueryResult:
        safe = self.validate(sql)
        with self._lock:
            cur = self._con.cursor()
            try:
                rel = cur.execute(f"SELECT * FROM ({safe}) AS q LIMIT {MAX_ROWS}")
                columns = [d[0] for d in rel.description or []]
                rows = rel.fetchall()
            finally:
                cur.close()
        return QueryResult(columns=columns, rows=rows)


@lru_cache
def get_hr_store() -> HRStore:
    return HRStore(get_settings().hr_csv_path)

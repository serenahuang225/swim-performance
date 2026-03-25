from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]


def connect_sqlite(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def run_query(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] | None = None,
    *,
    limit: int | None = None,
) -> QueryResult:
    cur = conn.execute(sql, params or ())
    rows = cur.fetchmany(limit) if limit else cur.fetchall()
    if not rows:
        return QueryResult(columns=[d[0] for d in (cur.description or [])], rows=[])
    cols = list(rows[0].keys()) if isinstance(rows[0], sqlite3.Row) else [d[0] for d in cur.description]
    tuples = [tuple(r) for r in rows]
    return QueryResult(columns=cols, rows=tuples)


def iter_dict_rows(result: QueryResult) -> Iterable[dict[str, Any]]:
    for r in result.rows:
        yield dict(zip(result.columns, r))


def export_to_csv(result: QueryResult, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(result.columns)
        w.writerows(result.rows)
    return out_path


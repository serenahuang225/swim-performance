from __future__ import annotations

import argparse
import textwrap
from pathlib import Path

from src.db.sqlite_utils import connect_sqlite, export_to_csv, run_query


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "raw" / "swim_db.sqlite"


def _print_table(result) -> None:
    if not result.columns:
        print("(no columns)")
        return
    if not result.rows:
        print("(no rows)")
        return
    widths = [len(c) for c in result.columns]
    for row in result.rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len("" if v is None else str(v)))
    fmt = " | ".join("{:" + str(w) + "}" for w in widths)
    print(fmt.format(*result.columns))
    print("-+-".join("-" * w for w in widths))
    for row in result.rows:
        print(fmt.format(*[("" if v is None else str(v)) for v in row]))


def cmd_tables(db_path: Path) -> None:
    with connect_sqlite(db_path) as conn:
        r = run_query(
            conn,
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;",
        )
    _print_table(r)


def cmd_schema(db_path: Path, table: str | None) -> None:
    with connect_sqlite(db_path) as conn:
        if table:
            r = run_query(conn, "SELECT sql FROM sqlite_master WHERE type='table' AND name=?;", (table,))
        else:
            r = run_query(
                conn,
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;",
            )
    _print_table(r)


def cmd_counts(db_path: Path) -> None:
    with connect_sqlite(db_path) as conn:
        r = run_query(
            conn,
            """
            SELECT
              (SELECT COUNT(*) FROM competitions) AS competitions,
              (SELECT COUNT(*) FROM heats)        AS heats,
              (SELECT COUNT(*) FROM results)      AS results
            ;
            """,
        )
    _print_table(r)


def cmd_sample_results(db_path: Path, meet_id: str | None, limit: int) -> None:
    with connect_sqlite(db_path) as conn:
        sql = """
        SELECT
          c.meet_id,
          c.competition_id,
          r.discipline_name,
          r.gender,
          r.phase,
          r.unit_name,
          r.race_start_time,
          r.rank,
          r.lane,
          r.athlete_name,
          r.country_code,
          r.time_str,
          r.reaction_time,
          r.points,
          r.medal_tag,
          r.person_id
        FROM results r
        JOIN competitions c ON c.competition_id = r.competition_id
        WHERE (? IS NULL OR c.meet_id = ?)
        ORDER BY c.meet_id, r.discipline_name, r.phase, r.rank
        LIMIT ?;
        """
        r = run_query(conn, sql, (meet_id, meet_id, limit))
    _print_table(r)


def cmd_export_results_csv(db_path: Path, out_csv: Path, meet_id: str | None) -> None:
    with connect_sqlite(db_path) as conn:
        sql = """
        SELECT
          c.meet_id,
          c.competition_id,
          r.heat_id,
          r.discipline_name,
          r.gender,
          r.phase,
          r.unit_name,
          r.race_start_time,
          r.person_id,
          r.athlete_name,
          r.country_code,
          r.time_str,
          r.time_ms,
          r.rank,
          r.lane,
          r.reaction_time,
          r.points,
          r.medal_tag,
          r.athlete_age,
          r.splits_json
        FROM results r
        JOIN competitions c ON c.competition_id = r.competition_id
        WHERE (? IS NULL OR c.meet_id = ?)
        ORDER BY c.meet_id, r.discipline_name, r.phase, r.rank;
        """
        r = run_query(conn, sql, (meet_id, meet_id))
    p = export_to_csv(r, out_csv)
    print(f"Wrote {len(r.rows)} rows to {p}")


def cmd_sql(db_path: Path, sql: str, out_csv: Path | None, limit: int | None) -> None:
    with connect_sqlite(db_path) as conn:
        r = run_query(conn, sql, limit=limit)
    if out_csv:
        p = export_to_csv(r, out_csv)
        print(f"Wrote {len(r.rows)} rows to {p}")
    else:
        _print_table(r)


def main() -> None:
    ep = textwrap.dedent(
        f"""
        Examples:
          python -m src.db.query_swim_db tables
          python -m src.db.query_swim_db counts
          python -m src.db.query_swim_db sample-results --meet-id oly_2024 --limit 20
          python -m src.db.query_swim_db export-results-csv --meet-id oly_2024 --out data/raw/oly_2024_results.csv
          python -m src.db.query_swim_db sql "SELECT meet_id, COUNT(*) n FROM competitions GROUP BY meet_id" --limit 50

        Default DB: {DEFAULT_DB}
        """
    ).strip()
    parser = argparse.ArgumentParser(description="Query the swim_db.sqlite database", epilog=ep)
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB), help="Path to SQLite DB")

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tables", help="List tables")

    p_schema = sub.add_parser("schema", help="Show schema")
    p_schema.add_argument("--table", type=str, default=None, help="Specific table name")

    sub.add_parser("counts", help="Row counts for main tables")

    p_sample = sub.add_parser("sample-results", help="Print sample results")
    p_sample.add_argument("--meet-id", type=str, default=None, help="Filter by meet_id (e.g. oly_2024)")
    p_sample.add_argument("--limit", type=int, default=25)

    p_export = sub.add_parser("export-results-csv", help="Export results (joined with meet_id) to CSV")
    p_export.add_argument("--meet-id", type=str, default=None, help="Filter by meet_id (e.g. oly_2024)")
    p_export.add_argument("--out", type=str, required=True, help="Output CSV path")

    p_sql = sub.add_parser("sql", help="Run an ad-hoc SQL query")
    p_sql.add_argument("query", type=str, help="SQL query (wrap in quotes)")
    p_sql.add_argument("--out", type=str, default=None, help="Optional output CSV path")
    p_sql.add_argument("--limit", type=int, default=None, help="Optional fetch limit")

    args = parser.parse_args()
    db_path = Path(args.db)

    if args.cmd == "tables":
        cmd_tables(db_path)
    elif args.cmd == "schema":
        cmd_schema(db_path, args.table)
    elif args.cmd == "counts":
        cmd_counts(db_path)
    elif args.cmd == "sample-results":
        cmd_sample_results(db_path, args.meet_id, args.limit)
    elif args.cmd == "export-results-csv":
        cmd_export_results_csv(db_path, Path(args.out), args.meet_id)
    elif args.cmd == "sql":
        cmd_sql(db_path, args.query, Path(args.out) if args.out else None, args.limit)


if __name__ == "__main__":
    main()


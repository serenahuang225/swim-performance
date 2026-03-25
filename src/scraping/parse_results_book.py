"""
Parse Omega RESULTS BOOK PDFs into structured swim results.

Reads PDFs from data/raw/swimming_results/*_results.pdf and writes
data/raw/swimming_results/swim_results.csv with columns aligned to README:
  meet_id, event_id, competition_name, event, distance, stroke, gender,
  athlete, nation, lane, time, rank, heat_final, round, date (if available).

Omega PDFs vary by year; this module uses pdfplumber for table extraction.
If a PDF has no detectable tables, rows for that meet will be empty (meet_id still recorded).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # type: ignore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_ROOT / "data" / "raw" / "swimming_results"
OUT_CSV = RESULTS_DIR / "swim_results.csv"

# Expected output columns (README-aligned)
OUT_COLUMNS = [
    "meet_id",
    "event_id",
    "competition_name",
    "event",
    "distance",
    "stroke",
    "gender",
    "athlete",
    "nation",
    "lane",
    "time",
    "rank",
    "heat_final",
    "round",
    "date",
]


def extract_meet_id_from_path(path: Path) -> str:
    """e.g. oly_2024_results.pdf -> oly_2024"""
    name = path.stem
    return re.sub(r"_results$", "", name)


def parse_pdf_tables(pdf_path: Path) -> list[dict]:
    """
    Extract table-like content from PDF. Returns list of dicts (one per row).
    Omega results books are layout-dependent; this is a best-effort extractor.
    """
    if pdfplumber is None:
        return []
    rows: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables or []:
                if not table:
                    continue
                # First row as headers if they look like column names
                header = table[0] if table else []
                # Normalize header strings
                header = [str(h or "").strip() for h in header]
                for r in table[1:]:
                    if not r or all(c is None or str(c).strip() == "" for c in r):
                        continue
                    row = {}
                    for i, h in enumerate(header):
                        if i < len(r) and r[i] is not None:
                            row[h] = str(r[i]).strip() if r[i] else ""
                    if row:
                        rows.append(row)
    return rows


def normalize_parsed_to_schema(rows: list[dict], meet_id: str, competition_name: str) -> list[dict]:
    """
    Map raw extracted row dicts to OUT_COLUMNS schema.
    Omega column names vary; common variants are tried (Name, Athlete, Country, Time, etc.).
    """
    out: list[dict] = []
    for r in rows:
        # Map common Omega-style headers to the project schema
        def get(*keys: str, default: str = "") -> str:
            for k in keys:
                for rk, rv in r.items():
                    if rk and k.lower() in str(rk).lower() and rv:
                        return str(rv).strip()
            return default

        athlete = get("name", "athlete", "swimmer", "competitor")
        nation = get("nation", "country", "nat", "team")
        time_val = get("time", "result", "mark", "swim")
        rank_val = get("rank", "pos", "place", "position")
        lane_val = get("lane", "ln")
        event_info = get("event", "race", "discipline")
        round_info = get("round", "heat", "final", "phase")

        out.append({
            "meet_id": meet_id,
            "event_id": "",
            "competition_name": competition_name,
            "event": event_info,
            "distance": "",
            "stroke": "",
            "gender": "",
            "athlete": athlete,
            "nation": nation,
            "lane": lane_val,
            "time": time_val,
            "rank": rank_val,
            "heat_final": round_info,
            "round": round_info,
            "date": "",
        })
    return out


def parse_all_results_books(results_dir: Path | None = None) -> pd.DataFrame:
    """Find all *_results.pdf, parse each, return single DataFrame with OUT_COLUMNS."""
    dir_path = results_dir or RESULTS_DIR
    if not dir_path.exists():
        return pd.DataFrame(columns=OUT_COLUMNS)

    all_rows: list[dict] = []
    for path in sorted(dir_path.glob("*_results.pdf")):
        meet_id = extract_meet_id_from_path(path)
        competition_name = meet_id.replace("_", " ").title()
        raw = parse_pdf_tables(path)
        normalized = normalize_parsed_to_schema(raw, meet_id, competition_name)
        all_rows.extend(normalized)

    return pd.DataFrame(all_rows, columns=OUT_COLUMNS) if all_rows else pd.DataFrame(columns=OUT_COLUMNS)


def main() -> None:
    df = parse_all_results_books()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    print("Wrote", OUT_CSV, "with", len(df), "rows.")


if __name__ == "__main__":
    main()

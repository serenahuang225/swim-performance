"""
Fetch Olympic swimming results (1912–2020) from SCORE or a local file.

SCORE: https://data.scorenetwork.org/swimming/olympic_swimming_history.html
Kaggle (if SCORE is down): https://www.kaggle.com/datasets/datasciencedonut/olympic-swimming-1912-to-2020
  → Download the CSV, then run this script with --local path/to/Olympic_Swimming_1912-2020.csv

Output: data/raw/swimming_results/olympic_1912_2020.csv with columns aligned to
  meet_id, event, distance, stroke, gender, athlete, nation, time, rank, ...
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "data" / "raw" / "swimming_results"
OUT_CSV = OUT_DIR / "olympic_1912_2020.csv"
SCORE_URL = "https://data.scorenetwork.org/data/Olympic_Swimming_1912-2020.csv"

# Map Olympic host city + year to project meet_id
OLY_MEET_IDS = {
    (2012, "London"): "oly_2012",
    (2016, "Rio"): "oly_2016",
    (2016, "Rio de Janeiro"): "oly_2016",
    (2020, "Tokyo"): "oly_2020",
    (2024, "Paris"): "oly_2024",
    (2008, "Beijing"): "oly_2008",
    (2004, "Athens"): "oly_2004",
    (2000, "Sydney"): "oly_2000",
    (1996, "Atlanta"): "oly_1996",
    (1992, "Barcelona"): "oly_1992",
    (1988, "Seoul"): "oly_1988",
    (1984, "Los Angeles"): "oly_1984",
    (1980, "Moscow"): "oly_1980",
    (1976, "Montreal"): "oly_1976",
    (1972, "Munich"): "oly_1972",
    (1968, "Mexico City"): "oly_1968",
    (1964, "Tokyo"): "oly_1964",
    (1960, "Rome"): "oly_1960",
    (1956, "Melbourne"): "oly_1956",
    (1952, "Helsinki"): "oly_1952",
    (1948, "London"): "oly_1948",
    (1936, "Berlin"): "oly_1936",
    (1932, "Los Angeles"): "oly_1932",
    (1928, "Amsterdam"): "oly_1928",
    (1924, "Paris"): "oly_1924",
    (1920, "Antwerp"): "oly_1920",
    (1912, "Stockholm"): "oly_1912",
}


def _meet_id_for_row(location: str, year: int) -> str | None:
    loc = (location or "").strip()
    for (y, city), mid in OLY_MEET_IDS.items():
        if y == year and (city.lower() in loc.lower() or loc.lower() in city.lower()):
            return mid
    return None


def fetch_from_score() -> bytes | None:
    try:
        r = requests.get(SCORE_URL, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print("SCORE download failed:", e)
        return None


def normalize_olympic_df(df: pd.DataFrame) -> pd.DataFrame:
    """Map SCORE/Kaggle columns to swim_results-style columns."""
    # Common column names from SCORE/Kaggle
    col_map = {
        "Location": "location",
        "Year": "year",
        "Distance": "distance",
        "Stroke": "stroke",
        "Gender": "gender",
        "Team": "nation",
        "Athlete": "athlete",
        "Results": "time",
        "Rank": "rank",
    }
    out = pd.DataFrame()
    for old, new in col_map.items():
        if old in df.columns:
            out[new] = df[old]
        else:
            out[new] = None
    if "location" not in out.columns and "Location" in df.columns:
        out["location"] = df["Location"]
    if "year" not in out.columns and "Year" in df.columns:
        out["year"] = df["Year"]
    out["meet_id"] = out.apply(
        lambda r: _meet_id_for_row(
            r.get("location", "") or "",
            int(r.get("year") or 0),
        ),
        axis=1,
    )
    out["competition_name"] = "Olympics " + out["year"].astype(str)
    out["event"] = (out["distance"].astype(str) if "distance" in out else "") + " " + (out["stroke"].astype(str) if "stroke" in out else "")
    out["event_id"] = ""
    out["lane"] = ""
    out["heat_final"] = ""
    out["round"] = ""
    out["date"] = ""
    # Reorder to match swim_results
    cols = ["meet_id", "event_id", "competition_name", "event", "distance", "stroke", "gender", "athlete", "nation", "lane", "time", "rank", "heat_final", "round", "date"]
    for c in cols:
        if c not in out.columns:
            out[c] = ""
    return out[[c for c in cols if c in out.columns]]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Olympic swimming CSV from SCORE or local file.")
    parser.add_argument("--local", type=str, help="Path to local Olympic_Swimming_1912-2020.csv (e.g. from Kaggle)")
    parser.add_argument("--output", type=str, default=str(OUT_CSV), help="Output CSV path")
    args = parser.parse_args()

    if args.local:
        path = Path(args.local)
        if not path.exists():
            print("File not found:", path)
            return
        df = pd.read_csv(path)
    else:
        raw = fetch_from_score()
        if raw is None:
            print("Download failed. Use Kaggle: https://www.kaggle.com/datasets/datasciencedonut/olympic-swimming-1912-to-2020")
            print("Then run: python -m src.scraping.fetch_olympic_csv --local /path/to/Olympic_Swimming_1912-2020.csv")
            return
        df = pd.read_csv(io.BytesIO(raw))

    out = normalize_olympic_df(df)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output)
    out.to_csv(out_path, index=False)
    print("Wrote", out_path, "with", len(out), "rows.")


if __name__ == "__main__":
    main()

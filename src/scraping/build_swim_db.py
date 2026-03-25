"""
Build a swim results database from World Aquatics API.

Reads data/raw/world_aquatics_competition_ids.csv (meet_id, competition_id).
For each competition_id: fetches events → for each discipline GET /events/{id} → stores
  data/raw/swim_db.sqlite  (and optionally data/raw/swimming_results/wa_*.parquet per competition).

Tables (SQLite):
  - competitions: meet_id, competition_id, name, from_date, to_date
  - heats: competition_id, heat_id, discipline_name, gender, phase, heat_name
  - results: heat_id, person_id, phase, unit_name, race_start_time, athlete_name, country_code, time_str,
    time_ms, rank, lane, reaction_time, points, medal_tag, athlete_age, splits_json (Option A), raw_json

Run: python -m src.scraping.build_swim_db [--limit 1] [--output data/raw/swim_db.sqlite]
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW = PROJECT_ROOT / "data" / "raw"
COMPETITION_IDS_CSV = RAW / "world_aquatics_competition_ids.csv"
DB_PATH = RAW / "swim_db.sqlite"


def _normalize_result_row(
    row: dict,
    competition_id: str,
    heat_id: str,
    discipline_name: str,
    gender: str,
    phase: str,
    unit_name: str = "",
    race_start_time: str | None = None,
) -> dict:
    """Map API keys to common names. Store PersonId, splits as JSON, phase, race time."""
    out = {
        "competition_id": competition_id,
        "heat_id": heat_id,
        "discipline_name": discipline_name,
        "gender": gender,
        "phase": phase,
        "unit_name": unit_name,
        "race_start_time": race_start_time,
    }
    out["person_id"] = (
        row.get("PersonId") or row.get("person_id") or row.get("AthleteId") or ""
    )
    out["athlete_name"] = (
        " ".join(filter(None, [row.get("FirstName"), row.get("LastName")]))
        or row.get("FullName")
        or row.get("Name")
        or row.get("AthleteName")
        or ""
    )
    out["country_code"] = (
        row.get("NAT")
        or row.get("CountryCode")
        or row.get("CountryId")
        or row.get("nation")
        or row.get("NationCode")
        or ""
    )
    out["time_str"] = (
        row.get("ResultTime")
        or row.get("Time")
        or row.get("time")
        or row.get("SwimTime")
        or ""
    )
    out["time_ms"] = row.get("ResultTimeMs") or row.get("TimeMs") or None
    out["rank"] = row.get("Rank") or row.get("rank") or row.get("Place") or None
    out["lane"] = row.get("Lane") or row.get("lane") or None
    out["reaction_time"] = _parse_float(row.get("RT") or row.get("ReactionTime") or row.get("reactionTime"))
    out["points"] = _parse_int(row.get("Points") or row.get("points"))
    out["medal_tag"] = row.get("MedalTag") or row.get("medal_tag") or None
    out["athlete_age"] = _parse_int(row.get("AthleteResultAge") or row.get("athlete_age"))
    splits = row.get("Splits") or row.get("splits") or []
    out["splits_json"] = json.dumps(splits) if splits else None
    return out


def _parse_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse_int(x: Any) -> int | None:
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def load_competition_ids(csv_path: Path) -> list[tuple[str, str]]:
    """Return [(meet_id, competition_id), ...] for rows that have non-empty competition_id."""
    pairs: list[tuple[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mid = (row.get("meet_id") or "").strip()
            cid = (row.get("competition_id") or "").strip()
            if mid and cid:
                pairs.append((mid, cid))
    return pairs


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS competitions (
        meet_id TEXT,
        competition_id TEXT PRIMARY KEY,
        name TEXT,
        from_date TEXT,
        to_date TEXT
    );
    CREATE TABLE IF NOT EXISTS heats (
        competition_id TEXT,
        heat_id TEXT PRIMARY KEY,
        discipline_name TEXT,
        gender TEXT,
        phase TEXT,
        heat_name TEXT
    );
    CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        competition_id TEXT,
        heat_id TEXT,
        discipline_name TEXT,
        gender TEXT,
        phase TEXT,
        unit_name TEXT,
        race_start_time TEXT,
        person_id TEXT,
        athlete_name TEXT,
        country_code TEXT,
        time_str TEXT,
        time_ms INTEGER,
        rank INTEGER,
        lane INTEGER,
        reaction_time REAL,
        points INTEGER,
        medal_tag TEXT,
        athlete_age INTEGER,
        splits_json TEXT,
        raw_json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_results_competition ON results(competition_id);
    CREATE INDEX IF NOT EXISTS idx_results_heat ON results(heat_id);
    """)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build swim DB from World Aquatics API")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of competitions (0 = all)")
    parser.add_argument("--output", type=str, default=str(DB_PATH), help="Output SQLite path")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-scrape even if competition already has stored results",
    )
    args = parser.parse_args()

    if not COMPETITION_IDS_CSV.exists():
        print("Create", COMPETITION_IDS_CSV, "with columns meet_id, competition_id. Get competition_id from worldaquatics.com URL.")
        sys.exit(1)

    pairs = load_competition_ids(COMPETITION_IDS_CSV)
    if not pairs:
        print("No meet_id, competition_id rows in", COMPETITION_IDS_CSV)
        sys.exit(1)

    if args.limit:
        pairs = pairs[: args.limit]

    from src.scraping.world_aquatics_api import (
        get_competition_events,
        get_event_results,
        iter_disciplines,
    )

    conn = sqlite3.connect(args.output)
    ensure_schema(conn)

    for meet_id, competition_id in pairs:
        if not args.force:
            try:
                existing = conn.execute(
                    "SELECT 1 FROM competitions WHERE competition_id=? LIMIT 1;",
                    (competition_id,),
                ).fetchone()
                existing_results = conn.execute(
                    "SELECT COUNT(*) FROM results WHERE competition_id=?;",
                    (competition_id,),
                ).fetchone()[0]
                if existing and existing_results and existing_results > 0:
                    print(
                        "Skipping competition",
                        competition_id,
                        "(",
                        meet_id,
                        ") - already has",
                        existing_results,
                        "results (use --force to re-scrape)",
                    )
                    continue
            except Exception:
                # If schema doesn't exist yet, fall through to scrape.
                pass

        print("Fetching competition", competition_id, "(", meet_id, ")...")
        try:
            events = get_competition_events(competition_id)
        except Exception as e:
            print("  Failed:", e)
            continue

        name = events.get("Name") or events.get("OfficialName") or ""
        from_date = (events.get("From") or "")[:10]
        to_date = (events.get("To") or "")[:10]
        conn.execute(
            "INSERT OR REPLACE INTO competitions (meet_id, competition_id, name, from_date, to_date) VALUES (?,?,?,?,?)",
            (meet_id, competition_id, name, from_date, to_date),
        )

        n_results = 0
        seen_heats: set[tuple[str, str]] = set()
        for ref in iter_disciplines(events):
            rows = get_event_results(ref.discipline_id)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                heat_id = row.get("_heat_id") or ""
                unit_name = row.get("_unit_name") or ""
                race_start_time = row.get("_race_time") or row.get("_race_date") or None
                phase = row.get("_phase_name") or ""
                key = (competition_id, heat_id)
                if key not in seen_heats:
                    seen_heats.add(key)
                    conn.execute(
                        "INSERT OR REPLACE INTO heats (competition_id, heat_id, discipline_name, gender, phase, heat_name) VALUES (?,?,?,?,?,?)",
                        (competition_id, heat_id, ref.discipline_name, ref.gender, phase, unit_name),
                    )
                norm = _normalize_result_row(
                    row, competition_id, heat_id, ref.discipline_name, ref.gender, phase,
                    unit_name=unit_name,
                    race_start_time=str(race_start_time) if race_start_time else None,
                )
                raw_clean = {k: v for k, v in row.items() if not k.startswith("_")}
                conn.execute(
                    """INSERT INTO results (competition_id, heat_id, discipline_name, gender, phase,
                       unit_name, race_start_time, person_id, athlete_name, country_code, time_str, time_ms,
                       rank, lane, reaction_time, points, medal_tag, athlete_age, splits_json, raw_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        norm["competition_id"],
                        norm["heat_id"],
                        norm["discipline_name"],
                        norm["gender"],
                        norm["phase"],
                        norm["unit_name"],
                        norm["race_start_time"],
                        norm["person_id"] or None,
                        norm["athlete_name"],
                        norm["country_code"],
                        norm["time_str"],
                        norm["time_ms"],
                        norm["rank"],
                        norm["lane"],
                        norm["reaction_time"],
                        norm["points"],
                        norm["medal_tag"],
                        norm["athlete_age"],
                        norm["splits_json"],
                        json.dumps(raw_clean) if raw_clean else None,
                    ),
                )
                n_results += 1
        conn.commit()
        print("  Stored", n_results, "results")

    conn.close()
    print("Done. Database:", args.output)


if __name__ == "__main__":
    main()

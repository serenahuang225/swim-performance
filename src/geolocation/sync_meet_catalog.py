"""
Rebuild data/raw/meet_catalog.csv from world_aquatics_competition_ids.csv + swim_db.sqlite.

- Dates: competitions.from_date / to_date in SQLite (by competition_id).
- City/country: parsed from CSV `notes` (fallback: meet_id slug).
- Preserves existing catalog rows for meet_ids already present (dates, use_era5_land, city, country).
- New meets: use_era5_land False for open-water-like meet_id prefixes, else True.

Run from project root:
  python -m src.geolocation.sync_meet_catalog
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMP_IDS = PROJECT_ROOT / "data" / "raw" / "world_aquatics_competition_ids.csv"
CATALOG = PROJECT_ROOT / "data" / "raw" / "meet_catalog.csv"
SUPPLEMENT = PROJECT_ROOT / "data" / "raw" / "meet_catalog_supplement.csv"
DB_PATH = PROJECT_ROOT / "data" / "raw" / "swim_db.sqlite"

# Longest first
MULTIWORD_COUNTRIES = [
    "North Macedonia",
    "United Arab Emirates",
    "South Korea",
    "United Kingdom",
    "Hong Kong",
    "Czech Republic",
    "Saudi Arabia",
    "New Zealand",
    "South Africa",
    "Costa Rica",
    "Puerto Rico",
    "Great Britain",
    "People's Republic of China",
]

ABBREV_COUNTRY = {
    "USA": "United States",
    "UAE": "United Arab Emirates",
    "UK": "United Kingdom",
    "POR": "Portugal",
    "JPN": "Japan",
    "CHN": "China",
    "ITA": "Italy",
    "FRA": "France",
    "GER": "Germany",
    "ESP": "Spain",
    "BRA": "Brazil",
    "CAN": "Canada",
    "AUS": "Australia",
    "QAT": "Qatar",
    "GRE": "Greece",
    "HUN": "Hungary",
    "RUS": "Russia",
    "KOR": "South Korea",
    "TPE": "Taiwan",
    "MKD": "North Macedonia",
    "EGY": "Egypt",
    "ISR": "Israel",
    "ROU": "Romania",
    "PER": "Peru",
    "MEX": "Mexico",
    "IND": "India",
    "SGP": "Singapore",
    "NED": "Netherlands",
    "SUI": "Switzerland",
    "AUT": "Austria",
    "POL": "Poland",
    "SWE": "Sweden",
    "NOR": "Norway",
    "DEN": "Denmark",
    "FIN": "Finland",
    "BEL": "Belgium",
    "CRO": "Croatia",
    "SRB": "Serbia",
    "ARG": "Argentina",
    "COL": "Colombia",
    "NZL": "New Zealand",
    "RSA": "South Africa",
    "IRL": "Ireland",
    "TUR": "Turkey",
    "KUW": "Kuwait",
    "KSA": "Saudi Arabia",
    "TWN": "Taiwan",
    "SCZ": "Seychelles",
    "HKG": "Hong Kong",
    "GBR": "United Kingdom",
}

KNOWN_COUNTRIES = {
    "china", "japan", "italy", "france", "germany", "spain", "portugal", "brazil",
    "canada", "australia", "qatar", "greece", "hungary", "russia", "korea", "poland",
    "sweden", "norway", "denmark", "finland", "netherlands", "belgium", "switzerland",
    "austria", "romania", "peru", "mexico", "india", "singapore", "taiwan", "israel",
    "egypt", "croatia", "serbia", "argentina", "colombia", "ireland", "turkey",
    "kuwait", "egypt", "bahamas", "jamaica", "scotland", "wales", "england", "ukraine",
    "slovenia", "slovakia", "bulgaria", "estonia", "latvia", "lithuania", "indonesia",
    "malaysia", "thailand", "vietnam", "philippines", "chile", "ecuador", "venezuela",
    "uruguay", "paraguay", "morocco", "algeria", "tunisia", "nigeria", "kenya",
    "ethiopia", "uganda", "zimbabwe", "cameroon", "ghana", "senegal", "iceland",
    "luxembourg", "monaco", "andorra", "malta", "cyprus", "lebanon", "jordan",
    "oman", "bahrain", "iran", "iraq", "pakistan", "bangladesh", "sri lanka",
    "nepal", "mongolia", "kazakhstan", "uzbekistan", "azerbaijan", "georgia",
    "armenia", "belarus", "moldova", "bosnia", "montenegro", "albania", "kosovo",
    "mauritius", "seychelles", "fiji", "samoa", "tonga", "bermuda", "barbados",
    "trinidad", "tobago", "panama", "guatemala", "honduras", "nicaragua", "cuba",
    "dominican", "haiti", "jamaica", "fiji", "egypt", "iran",
}

CITY_COUNTRY = {
    "barcelona": "Spain",
    "atlanta": "United States",
    "sydney": "Australia",
    "athens": "Greece",
    "beijing": "China",
    "london": "United Kingdom",
    "rio": "Brazil",
    "rio de janeiro": "Brazil",
    "tokyo": "Japan",
    "paris": "France",
    "perth": "Australia",
    "rome": "Italy",
    "fukuoka": "Japan",
    "montreal": "Canada",
    "melbourne": "Australia",
    "shanghai": "China",
    "kazan": "Russia",
    "gwangju": "South Korea",
    "doha": "Qatar",
    "budapest": "Hungary",
    "berlin": "Germany",
    "budapest": "Hungary",
    "buda": "Hungary",
    "doha": "Qatar",
    "kazan": "Russia",
    "indianapolis": "United States",
    "toronto": "Canada",
    "athens": "Greece",
    "incheon": "South Korea",
    "shanghai": "China",
    "abu dhabi": "United Arab Emirates",
    "melbourne": "Australia",
    "birmingham": "United Kingdom",
    "london": "United Kingdom",
    "paris": "France",
    "singapore": "Singapore",
    "tokyo": "Japan",
    "beijing": "China",
    "moscow": "Russia",
    "stockholm": "Sweden",
    "dubai": "United Arab Emirates",
    "durban": "South Africa",
    "sydney": "Australia",
    "rio de janeiro": "Brazil",
    "belo horizonte": "Brazil",
    "lima": "Peru",
    "netanya": "Israel",
    "otopeni": "Romania",
    "guangzhou": "China",
    "hangzhou": "China",
    "jinan": "China",
    "eindhoven": "Netherlands",
    "kyushu": "Japan",
    "buenos aires": "Argentina",
    "carmel": "United States",
    "westmont": "United States",
    "windsor": "Canada",
    "manchester": "United Kingdom",
    "monterrey": "Mexico",
    "malmo": "Sweden",
    "hobart": "Australia",
    "hong kong": "Hong Kong",
    "sheffield": "United Kingdom",
    "imperia": "Italy",
    "edmonton": "Canada",
    "honolulu hawaii": "United States",
    "college park": "United States",
}

STOP_WORDS = {
    "fina", "world", "aquatics", "swimming", "swim", "cup", "championships", "champs",
    "open", "water", "marathon", "series", "arena", "10km", "games", "olympic",
    "olympics", "youth", "junior", "masters", "css", "champions", "ultra", "grand",
    "finale", "qualifier", "7th", "8th", "9th", "10th", "11th", "12th", "13th",
    "14th", "15th", "16th", "1st", "2nd", "3rd", "4th", "5th", "6th", "25m",
    "the", "of", "and", "in", "at", "for",
    "25m", "50m",
}


def _strip_year(notes: str) -> tuple[str, str | None]:
    notes = notes.strip()
    m = re.search(r"\s+(\d{4})\s*$", notes)
    if m:
        return notes[: m.start()].strip(), m.group(1)
    return notes, None


def parse_city_country(notes: str, meet_id: str) -> tuple[str, str]:
    body, _yr = _strip_year(notes)
    body = body.strip()

    country: str | None = None
    rest = body

    for c in MULTIWORD_COUNTRIES:
        if rest.lower().endswith(c.lower()):
            country = c
            rest = rest[: -len(c)].strip()
            break

    if country is None:
        parts = rest.split()
        if parts:
            last = parts[-1]
            if last.upper() in ABBREV_COUNTRY:
                country = ABBREV_COUNTRY[last.upper()]
                rest = " ".join(parts[:-1]).strip()
            elif last.lower() in KNOWN_COUNTRIES or (
                len(last) > 3 and last[0].isupper() and last.lower() in KNOWN_COUNTRIES
            ):
                country = last if last[0].isupper() else last.title()
                rest = " ".join(parts[:-1]).strip()
                # City-states: "Singapore 2025" → country was parsed, rest empty
                if not rest.strip():
                    rest = country

    if not country:
        country = "Unknown"

    words = rest.split()
    venue: list[str] = []
    for w in reversed(words):
        if re.fullmatch(r"\d{4}", w) or re.fullmatch(r"\d{4}-\d{4}", w):
            if venue:
                break
            continue
        wl = re.sub(r"[^a-z0-9]", "", w.lower())
        if wl in STOP_WORDS:
            if venue:
                break
            continue
        venue.insert(0, w)
        if len(venue) >= 4:
            break

    city = " ".join(venue).strip() if venue else (words[-1] if words else meet_id)
    if not city:
        city = meet_id.replace("_", " ").title()

    # Title-case city nicely
    city = " ".join(
        "Lake" if p.lower() == "lake" else (p.upper() if p.upper() in ("NEOM", "MD") else p.title())
        for p in city.split()
    )

    if country == "Unknown":
        ck = city.lower().strip()
        if ck in CITY_COUNTRY:
            country = CITY_COUNTRY[ck]
        else:
            for k, v in CITY_COUNTRY.items():
                if ck.endswith(k) or k in ck.split():
                    country = v
                    break

    return city, country


def default_use_era5_land(meet_id: str) -> bool:
    low = meet_id.lower()
    if any(
        low.startswith(p)
        for p in (
            "owwc",
            "owwjc",
            "owgp",
            "owmoq",
            "owultra",
            "msws",
            "ow_worlds",
        )
    ):
        return False
    return True


def load_db_dates() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT competition_id, from_date, to_date, name FROM competitions",
        conn,
    )
    conn.close()
    df["competition_id"] = df["competition_id"].astype(str)
    return df


def main() -> None:
    comp = pd.read_csv(COMP_IDS)
    comp["competition_id"] = comp["competition_id"].astype(str)
    db = load_db_dates()
    merged = comp.merge(db, on="competition_id", how="left")

    missing = merged["from_date"].isna()
    if missing.any():
        print(
            "WARNING: no SQLite dates for competition_id(s):",
            merged.loc[missing, "competition_id"].tolist()[:10],
            "...",
        )

    prev = pd.read_csv(CATALOG)
    prev_by_id = {str(r.meet_id): r for _, r in prev.iterrows()}

    rows = []
    for _, r in merged.iterrows():
        mid = str(r["meet_id"])
        notes = str(r.get("notes") or r.get("name") or "")
        city, country = parse_city_country(notes, mid)
        fd = r.get("from_date")
        td = r.get("to_date")
        if pd.isna(fd) or fd is None:
            start_date = "2000-01-01"
            end_date = "2000-01-07"
        else:
            start_date = str(fd)[:10]
            end_date = str(td)[:10] if not pd.isna(td) else start_date

        meet_name = re.sub(r"\s+\d{4}\s*$", "", notes).strip() or mid.replace("_", " ").title()

        use_land = default_use_era5_land(mid)
        if mid in prev_by_id:
            use_land = prev_by_id[mid]["use_era5_land"]

        rows.append(
            {
                "meet_id": mid,
                "meet_name": meet_name[:200],
                "city": city[:120],
                "country": country[:80],
                "start_date": start_date,
                "end_date": end_date,
                "use_era5_land": use_land,
            }
        )

    out = pd.DataFrame(rows)
    comp_ids_set = set(comp["meet_id"].astype(str))
    out_ids = set(out["meet_id"].astype(str))
    extra = []
    if SUPPLEMENT.exists():
        sup = pd.read_csv(SUPPLEMENT)
        for _, pr in sup.iterrows():
            mid = str(pr["meet_id"])
            if mid not in comp_ids_set and mid not in out_ids:
                extra.append(pr.to_dict())
                out_ids.add(mid)
    if extra:
        out = pd.concat([out, pd.DataFrame(extra)], ignore_index=True)

    # Order: original catalog sequence, then new comp_ids alphabetically, then extras
    old_ids = list(prev["meet_id"].astype(str))
    all_from_comp = sorted(set(comp["meet_id"].astype(str)))
    new_ids = [m for m in all_from_comp if m not in set(old_ids)]
    order: dict[str, int] = {m: i for i, m in enumerate(old_ids)}
    base = len(old_ids)
    for i, m in enumerate(new_ids):
        order[m] = base + i
    base += len(new_ids)
    for i, m in enumerate(sorted(set(out["meet_id"]) - set(old_ids) - set(new_ids))):
        order[m] = base + i
    out["_o"] = out["meet_id"].map(lambda x: order.get(str(x), 9999))
    out = out.sort_values("_o").drop(columns=["_o"])

    out.to_csv(CATALOG, index=False)
    print(
        f"Wrote {len(out)} rows to {CATALOG} "
        f"({len(new_ids)} new from competition_ids, {len(extra)} catalog-only preserved)"
    )


if __name__ == "__main__":
    main()

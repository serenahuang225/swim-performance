"""
Geocode meet catalog using GeoNames API.

Reads data/raw/meet_catalog.csv, calls GeoNames search for each (city, country),
writes data/raw/locations.csv with meet_id, city, country, lat, lon, elevation, timezone.

Username from env GEONAMES_USER or default 'serenahuang'. Cache: data/raw/locations/geocode_cache.json.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_CSV = PROJECT_ROOT / "data" / "raw" / "meet_catalog.csv"
OUT_CSV = PROJECT_ROOT / "data" / "raw" / "locations.csv"
CACHE_DIR = PROJECT_ROOT / "data" / "raw" / "locations"
CACHE_JSON = CACHE_DIR / "geocode_cache.json"
# Use HTTP to avoid SSL verification issues in some environments
GEONAMES_SEARCH = "http://api.geonames.org/search"

# Rate limit: 1 request per second for free tier
SLEEP_SEC = 1.0


def get_username() -> str:
    return os.environ.get("GEONAMES_USER", "serenahuang")


def load_cache() -> dict[str, dict]:
    if not CACHE_JSON.exists():
        return {}
    with open(CACHE_JSON) as f:
        return json.load(f)


def save_cache(cache: dict[str, dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_JSON, "w") as f:
        json.dump(cache, f, indent=2)


def cache_key(city: str, country: str) -> str:
    return f"{city}|{country}".strip().lower()


def geocode_one(city: str, country: str, username: str, cache: dict) -> dict:
    """Return dict with lat, lon, elevation, timezone (from timezoneId or gmtOffset)."""
    key = cache_key(city, country)
    if key in cache:
        return cache[key]

    q = f"{city}, {country}"
    params = {"q": q, "username": username, "maxRows": 1, "type": "json"}
    try:
        r = requests.get(GEONAMES_SEARCH, params=params, timeout=10)
        data = r.json()
        geos = data.get("geonames") or []
        if not geos:
            cache[key] = {"lat": None, "lon": None, "elevation": None, "timezone": None}
        else:
            g = geos[0]
            tz = g.get("timezoneId") or (f"UTC{g.get('gmtOffset', 0):+d}" if g.get("gmtOffset") is not None else None)
            cache[key] = {
                "lat": g.get("lat"),
                "lon": g.get("lng"),
                "elevation": g.get("elevation"),
                "timezone": tz,
            }
    except Exception as e:
        cache[key] = {"lat": None, "lon": None, "elevation": None, "timezone": None, "_error": str(e)}
    return cache[key]


def load_fallback() -> dict[str, dict]:
    """Fallback coordinates when API fails (e.g. SSL)."""
    fallback_path = CACHE_DIR / "fallback_coordinates.csv"
    if not fallback_path.exists():
        return {}
    fb = pd.read_csv(fallback_path)
    return {row["meet_id"]: {"lat": row["lat"], "lon": row["lon"], "elevation": row.get("elevation"), "timezone": row.get("timezone")} for _, row in fb.iterrows()}


def main() -> None:
    username = get_username()
    catalog = pd.read_csv(CATALOG_CSV)
    cache = load_cache()
    fallback = load_fallback()

    rows = []
    for _, r in catalog.iterrows():
        meet_id = r["meet_id"]
        city = str(r.get("city", "")).strip()
        country = str(r.get("country", "")).strip()
        info = geocode_one(city, country, username, cache)
        lat, lon = info.get("lat"), info.get("lon")
        if (lat is None or lon is None) and meet_id in fallback:
            info = fallback[meet_id]
            lat, lon = info.get("lat"), info.get("lon")
        rows.append({
            "meet_id": meet_id,
            "city": city,
            "country": country,
            "lat": lat,
            "lon": lon,
            "elevation": info.get("elevation"),
            "timezone": info.get("timezone"),
        })
        time.sleep(SLEEP_SEC)

    save_cache(cache)
    out = pd.DataFrame(rows)
    # Fill missing from fallback
    for meet_id, fb in fallback.items():
        mask = out["meet_id"] == meet_id
        if mask.any() and pd.isna(out.loc[mask, "lat"]).any():
            out.loc[mask, "lat"] = fb["lat"]
            out.loc[mask, "lon"] = fb["lon"]
            if fb.get("elevation") is not None:
                out.loc[mask, "elevation"] = fb["elevation"]
            if fb.get("timezone"):
                out.loc[mask, "timezone"] = fb["timezone"]
    out.to_csv(OUT_CSV, index=False)
    print("Wrote", OUT_CSV)


if __name__ == "__main__":
    main()

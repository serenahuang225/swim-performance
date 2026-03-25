"""
Download ERA5 or ERA5-Land for meet locations in chunks (one request per meet event).

Reads data/raw/meet_catalog.csv and data/raw/locations.csv.
For each meet: (lat, lon, start_date, end_date), use_era5_land.
Requests variables: 2m temp, 2m dew point, 10m u/v wind, surface pressure.
Writes to data/raw/weather/era5/{meet_id}.nc or era5_land/{meet_id}.nc.
Logs to data/raw/weather/download_log.csv for resume.

CLI: --status, --dry-run, --meet-id ID (repeat), --force, --ignore-log.
See README § Download ERA5 for more meets.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile, BadZipFile

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_CSV = PROJECT_ROOT / "data" / "raw" / "meet_catalog.csv"
LOCATIONS_CSV = PROJECT_ROOT / "data" / "raw" / "locations.csv"
WEATHER_DIR = PROJECT_ROOT / "data" / "raw" / "weather"
LOG_CSV = WEATHER_DIR / "download_log.csv"

# CDS dataset IDs
DATASET_ERA5 = "reanalysis-era5-single-levels"
DATASET_ERA5_LAND = "reanalysis-era5-land"

# Variables for WBGT + pressure (CDS names)
VARIABLES_ERA5 = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "surface_pressure",
]
# ERA5-Land uses same names for these
VARIABLES_ERA5_LAND = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "surface_pressure",
]


def load_catalog_and_locations() -> pd.DataFrame:
    catalog = pd.read_csv(CATALOG_CSV)
    locations = pd.read_csv(LOCATIONS_CSV)
    merged = catalog.merge(locations, on="meet_id", how="left")
    return merged


def load_log() -> set[tuple[str, str]]:
    """Return set of (meet_id, dataset) already downloaded."""
    if not LOG_CSV.exists():
        return set()
    done = set()
    with open(LOG_CSV) as f:
        r = csv.DictReader(f)
        for row in r:
            done.add((row["meet_id"], row["dataset"]))
    return done


def append_log(meet_id: str, dataset: str, file_path: str) -> None:
    WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not LOG_CSV.exists()
    with open(LOG_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["meet_id", "dataset", "file_path", "requested_at"])
        if write_header:
            w.writeheader()
        w.writerow({
            "meet_id": meet_id,
            "dataset": dataset,
            "file_path": file_path,
            "requested_at": datetime.utcnow().isoformat() + "Z",
        })


def _maybe_unzip_netcdf(out_path: Path) -> None:
    """
    CDS sometimes returns a ZIP archive even when you request format=netcdf.
    If out_path is a zip, extract the first .nc file and replace out_path.
    """
    try:
        with ZipFile(out_path) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/") and (n.endswith(".nc") or n.endswith(".netcdf"))]
            if not names:
                return
            name = names[0]
            tmp = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp.write_bytes(zf.read(name))
            tmp.replace(out_path)
    except BadZipFile:
        return
    except Exception:
        return


def build_request(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    use_era5_land: bool,
) -> dict:
    """Build CDS API request dict. area = N, W, S, E (single point as tiny box)."""
    dataset = DATASET_ERA5_LAND if use_era5_land else DATASET_ERA5
    variables = VARIABLES_ERA5_LAND if use_era5_land else VARIABLES_ERA5

    start = datetime.strptime(start_date[:10], "%Y-%m-%d")
    end = datetime.strptime(end_date[:10], "%Y-%m-%d")

    # IMPORTANT: request the *full* date range, not just endpoints.
    # CDS accepts lists for year/month/day; lists are generated from the inclusive range.
    date_index = pd.date_range(start=start.date(), end=end.date(), freq="D")
    years = sorted({d.year for d in date_index})
    months = sorted({d.month for d in date_index})
    days = sorted({d.day for d in date_index})
    # Hourly for the date range
    times = [f"{h:02d}:00" for h in range(24)]

    # Area: small box around point (CDS wants N, W, S, E)
    delta = 0.25
    area = [lat + delta, lon - delta, lat - delta, lon + delta]

    request = {
        "product_type": "reanalysis",
        "variable": variables,
        "year": [str(y) for y in years],
        "month": [f"{m:02d}" for m in months],
        "day": [f"{d:02d}" for d in days],
        "time": times,
        "area": area,
        "format": "netcdf",
    }
    return request, dataset


def download_meet(
    row: pd.Series,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> str | None:
    """Download one meet's weather. Returns output path or None."""
    try:
        import cdsapi
    except ImportError:
        print("cdsapi not installed; pip install cdsapi")
        return None

    meet_id = row["meet_id"]
    lat = row.get("lat")
    lon = row.get("lon")
    if pd.isna(lat) or pd.isna(lon):
        print(f"Skipping {meet_id}: no lat/lon")
        return None

    use_era5_land = str(row.get("use_era5_land", "False")).strip().lower() in ("true", "1", "yes")
    start_date = str(row["start_date"])
    end_date = str(row["end_date"])

    request, dataset = build_request(float(lat), float(lon), start_date, end_date, use_era5_land)
    subdir = "era5_land" if use_era5_land else "era5"
    out_dir = WEATHER_DIR / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{meet_id}.nc"

    if dry_run:
        print(f"[dry-run] would retrieve {dataset} -> {out_path} ({start_date} .. {end_date})")
        return str(out_path)

    if out_path.exists() and not force:
        print(f"Already exists: {out_path}")
        _maybe_unzip_netcdf(out_path)
        append_log(meet_id, dataset, str(out_path))
        return str(out_path)

    if force and out_path.exists():
        out_path.unlink()
        print(f"Removed existing file for re-download: {out_path}")

    c = cdsapi.Client()
    c.retrieve(dataset, request, str(out_path))
    _maybe_unzip_netcdf(out_path)
    append_log(meet_id, dataset, str(out_path))
    return str(out_path)


def _status_row(row: pd.Series, done: set[tuple[str, str]]) -> dict:
    meet_id = row["meet_id"]
    use_era5_land = str(row.get("use_era5_land", "False")).strip().lower() in ("true", "1", "yes")
    dataset = DATASET_ERA5_LAND if use_era5_land else DATASET_ERA5
    subdir = "era5_land" if use_era5_land else "era5"
    out_path = WEATHER_DIR / subdir / f"{meet_id}.nc"
    has_ll = not (pd.isna(row.get("lat")) or pd.isna(row.get("lon")))
    return {
        "meet_id": meet_id,
        "dataset": "land" if use_era5_land else "single",
        "has_lat_lon": has_ll,
        "file_exists": out_path.exists(),
        "in_log": (meet_id, dataset) in done,
        "start": str(row.get("start_date", ""))[:10],
        "end": str(row.get("end_date", ""))[:10],
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download ERA5 / ERA5-Land hourly NetCDF per meet (meet_catalog + locations)."
    )
    parser.add_argument(
        "--meet-id",
        action="append",
        dest="meet_ids",
        metavar="ID",
        help="Only this meet_id (repeat for several). Default: all rows in catalog.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if .nc exists or row is in download_log (removes old .nc first).",
    )
    parser.add_argument(
        "--ignore-log",
        action="store_true",
        help="Do not skip meets that are only in download_log (still skips if .nc exists unless --force).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print CDS targets only; no API calls.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print one line per catalog meet: lat/lon, file exists, in log; then exit.",
    )
    args = parser.parse_args()

    df = load_catalog_and_locations()
    if args.meet_ids:
        want = set(args.meet_ids)
        df = df[df["meet_id"].isin(want)]
        missing = want - set(df["meet_id"].astype(str))
        if missing:
            print("Warning: meet_id not in catalog+locations merge:", ", ".join(sorted(missing)))

    done = load_log()

    if args.status:
        for _, row in df.iterrows():
            s = _status_row(row, done)
            flags = []
            if not s["has_lat_lon"]:
                flags.append("NO_COORDS")
            elif s["file_exists"]:
                flags.append("has_nc")
            if s["in_log"]:
                flags.append("logged")
            extra = " ".join(flags) if flags else "needs_download" if s["has_lat_lon"] else ""
            print(
                f"{s['meet_id']}\t{s['dataset']}\t{s['start']}..{s['end']}\t{extra}"
            )
        return

    for _, row in df.iterrows():
        meet_id = row["meet_id"]
        use_era5_land = str(row.get("use_era5_land", "False")).strip().lower() in ("true", "1", "yes")
        dataset = DATASET_ERA5_LAND if use_era5_land else DATASET_ERA5

        if not args.force and (meet_id, dataset) in done and not args.ignore_log:
            subdir = "era5_land" if use_era5_land else "era5"
            out_path = WEATHER_DIR / subdir / f"{meet_id}.nc"
            if out_path.exists():
                print(f"Skip (in log + file exists): {meet_id}")
            else:
                print(f"Skip (in log, no file — use --ignore-log or --force): {meet_id}")
            continue

        path = download_meet(row, force=args.force, dry_run=args.dry_run)
        if path and not args.dry_run:
            print("Downloaded", path)

    if args.dry_run:
        print(f"Dry-run finished ({len(df)} meet(s)).")


if __name__ == "__main__":
    main()

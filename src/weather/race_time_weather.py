"""
Attach race-time (hourly) weather to result rows using ERA5/ERA5-Land NetCDFs.

Each result row has meet_id and race_start_time. `race_start_time` is interpreted
as local time in the meet's timezone, converted to UTC; the meet's NetCDF is indexed
at the nearest hour to extract t2m, d2m, u10, v10, sp at the grid point.
Adds columns: race_time_temperature, race_time_dewpoint, race_time_wind_speed,
race_time_pressure, race_time_relative_humidity, race_time_wbgt.

Requires: results DataFrame with meet_id, race_start_time; locations with meet_id, timezone;
NetCDF files under weather_dir (era5/{meet_id}.nc and/or era5_land/{meet_id}.nc).
ERA5 times in NetCDF are UTC.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# Optional: zoneinfo for timezone (Python 3.9+)
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW = PROJECT_ROOT / "data" / "raw"
WEATHER_DIR = RAW / "weather"


def _parse_race_time(race_start_time: str, timezone_str: str) -> datetime | None:
    """Parse race_start_time (e.g. 2024-07-29T19:27:00) as local time in timezone_str; return UTC datetime."""
    if pd.isna(race_start_time) or not str(race_start_time).strip():
        return None
    s = str(race_start_time).strip()
    # Accept ISO-like with or without Z
    if s.endswith("Z"):
        s = s[:-1]
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S") if " " in s else datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")
        # If already timezone-aware, convert to UTC and return naive
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        # Assume local time in meet timezone
        if ZoneInfo is None:
            try:
                import pytz
                tz = pytz.timezone(timezone_str)
                return tz.localize(dt).astimezone(pytz.UTC).replace(tzinfo=None)
            except Exception:
                return None
        tz = ZoneInfo(timezone_str)
        return dt.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _load_hourly_at_time(nc_path: Path, utc_dt: datetime) -> dict[str, float] | None:
    """Load one hour from NetCDF at utc_dt; return dict with temperature, dewpoint, wind_speed, pressure (and derived wbgt)."""
    try:
        import xarray as xr
    except ImportError:
        return None
    if not nc_path.exists():
        return None
    try:
        ds = xr.open_dataset(nc_path)
        time_dim = None
        for name in ("valid_time", "time"):
            if name in ds.coords:
                time_dim = name
                break
        if time_dim is None:
            for c in ds.coords:
                if "time" in c.lower():
                    time_dim = c
                    break
        if time_dim is None:
            ds.close()
            return None
        times = ds[time_dim].values
        if hasattr(times, "astype"):
            try:
                times = times.astype("datetime64[s]")
            except Exception:
                pass
        # Nearest hour
        target = np.datetime64(utc_dt.strftime("%Y-%m-%dT%H:%M:%S"), "ns")
        idx = np.abs(times.astype("datetime64[h]") - target.astype("datetime64[h]")).argmin()
        # Single point
        def sel_point(da):
            dims = list(da.dims)
            for lat in ("latitude", "lat", "y"):
                for lon in ("longitude", "lon", "x"):
                    if lat in dims and lon in dims:
                        return da.isel({time_dim: idx, lat: 0, lon: 0})
            return da.isel({time_dim: idx})
        out = {}
        if "t2m" in ds:
            t_k = float(sel_point(ds["t2m"]).values)
            out["temperature"] = t_k - 273.15
        else:
            ds.close()
            return None
        if "d2m" in ds:
            d_k = float(sel_point(ds["d2m"]).values)
            out["dewpoint"] = d_k - 273.15
        else:
            out["dewpoint"] = None
        if "u10" in ds and "v10" in ds:
            u = float(sel_point(ds["u10"]).values)
            v = float(sel_point(ds["v10"]).values)
            out["wind_speed"] = (u**2 + v**2) ** 0.5
        else:
            out["wind_speed"] = None
        if "sp" in ds:
            out["pressure"] = float(sel_point(ds["sp"]).values) / 100.0
        else:
            out["pressure"] = None
        ds.close()
        # WBGT
        if out.get("dewpoint") is not None:
            from src.features.wbgt import dewpoint_to_rh, wbgt_indoor_approx
            rh = dewpoint_to_rh(out["temperature"], out["dewpoint"])
            out["relative_humidity"] = rh
            out["wbgt"] = wbgt_indoor_approx(out["temperature"], rh_percent=rh)
        else:
            out["relative_humidity"] = None
            out["wbgt"] = None
        return out
    except Exception:
        return None


def add_race_time_weather(
    results: pd.DataFrame,
    locations: pd.DataFrame,
    weather_dir: Path | None = None,
    subdir: str = "era5_land",
) -> pd.DataFrame:
    """
    Add race-time weather columns to results. Requires columns meet_id, race_start_time.
    locations must have meet_id and timezone. NetCDFs under weather_dir/subdir/{meet_id}.nc.
    """
    if weather_dir is None:
        weather_dir = WEATHER_DIR
    path = weather_dir / subdir
    if not path.exists():
        return results
    # meet_id -> timezone
    tz_map = locations.set_index("meet_id")["timezone"].to_dict()
    # Cache per (meet_id, utc_hour_key) to avoid repeated NetCDF reads for same hour
    cache: dict[tuple[str, str], dict[str, float] | None] = {}

    rows = []
    for i, r in results.iterrows():
        meet_id = r["meet_id"]
        tz_str = tz_map.get(meet_id)
        if not tz_str:
            rows.append({k: None for k in ("race_time_temperature", "race_time_dewpoint", "race_time_wind_speed", "race_time_pressure", "race_time_relative_humidity", "race_time_wbgt")})
            continue
        utc_dt = _parse_race_time(r.get("race_start_time"), tz_str)
        if utc_dt is None:
            rows.append({k: None for k in ("race_time_temperature", "race_time_dewpoint", "race_time_wind_speed", "race_time_pressure", "race_time_relative_humidity", "race_time_wbgt")})
            continue
        key = (meet_id, utc_dt.strftime("%Y-%m-%dT%H"))
        if key not in cache:
            nc_path = path / f"{meet_id}.nc"
            cache[key] = _load_hourly_at_time(nc_path, utc_dt)
        w = cache[key]
        if w is None:
            rows.append({k: None for k in ("race_time_temperature", "race_time_dewpoint", "race_time_wind_speed", "race_time_pressure", "race_time_relative_humidity", "race_time_wbgt")})
        else:
            rows.append({
                "race_time_temperature": w.get("temperature"),
                "race_time_dewpoint": w.get("dewpoint"),
                "race_time_wind_speed": w.get("wind_speed"),
                "race_time_pressure": w.get("pressure"),
                "race_time_relative_humidity": w.get("relative_humidity"),
                "race_time_wbgt": w.get("wbgt"),
            })
    out = results.copy()
    for k in ("race_time_temperature", "race_time_dewpoint", "race_time_wind_speed", "race_time_pressure", "race_time_relative_humidity", "race_time_wbgt"):
        out[k] = [row[k] for row in rows]
    return out


if __name__ == "__main__":
    import sys
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    PROCESSED = PROJECT_ROOT / "data" / "processed"
    RAW = PROJECT_ROOT / "data" / "raw"
    df = pd.read_csv(PROCESSED / "results_with_environment.csv")
    if "race_start_time" not in df.columns:
        print("results_with_environment.csv must contain race_start_time. Re-run 01_join_results_environment (with race_start_time in the SQL).")
        sys.exit(1)
    locations = pd.read_csv(RAW / "locations.csv")
    df = add_race_time_weather(df, locations, subdir="era5_land")
    out_path = PROCESSED / "results_with_race_time_weather.csv"
    PROCESSED.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print("Wrote", out_path)

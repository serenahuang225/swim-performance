"""
Merge meet catalog, locations, weather (ERA5), and optional swim results into
data/processed/swim_environment_dataset.csv with WBGT.

Reads:
  - data/raw/meet_catalog.csv
  - data/raw/locations.csv
  - data/raw/weather/era5/*.nc and era5_land/*.nc (or pre-aggregated weather CSV)
  - data/raw/swimming_results/swim_results.csv (optional)

Writes:
  - data/processed/swim_environment_dataset.csv

Run from project root: python -m src.merge_pipeline
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as script: python src/merge_pipeline.py
if __name__ == "__main__" and str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW = PROJECT_ROOT / "data" / "raw"
PROCESSED = PROJECT_ROOT / "data" / "processed"
WEATHER_DIR = RAW / "weather"
OUT_CSV = PROCESSED / "swim_environment_dataset.csv"

# Meets to exclude from output (e.g. no ERA5 data available)
EXCLUDED_MEET_IDS = frozenset({"wc_2001", "wc_2023"})  # Fukuoka


def _sel_point(da):
    """Reduce to single point: take (0,0) for lat/lon (or lat/latitude, lon/longitude)."""
    dims = list(da.dims)
    for lat_name in ("latitude", "lat", "y"):
        for lon_name in ("longitude", "lon", "x"):
            if lat_name in dims and lon_name in dims:
                return da.isel({lat_name: 0, lon_name: 0})
    return da


def load_weather_from_netcdf() -> pd.DataFrame | None:
    """
    Load and aggregate weather from ERA5/ERA5-Land NetCDF files.

    Prefers xarray. Falls back to netCDF4 if xarray isn't available.
    Returns a dataframe with one row per (meet_id, source); use pick_weather_by_catalog
    to get one row per meet. Uses nanmean so partial data still yields values.
    """
    xr = None
    try:
        import xarray as _xr
        import numpy as np
        xr = _xr
    except Exception:
        xr = None

    rows = []
    for sub in ("era5", "era5_land"):
        path = WEATHER_DIR / sub
        if not path.exists():
            continue
        for nc in path.glob("*.nc"):
            meet_id = nc.stem
            try:
                if xr is not None:
                    ds = xr.open_dataset(nc)
                    # Use nanmean so all-NaN grids don't poison the mean
                    def _nanmean(da):
                        v = _sel_point(da).values
                        if v.size == 0:
                            return None
                        v = np.asarray(v, dtype=float)
                        if np.all(np.isnan(v)):
                            return None
                        return float(np.nanmean(v))

                    if "t2m" in ds:
                        temp_k = _nanmean(ds["t2m"])
                        temp_c = (temp_k - 273.15) if temp_k is not None and np.isfinite(temp_k) else None
                    else:
                        temp_c = None
                    if "d2m" in ds:
                        dew_k = _nanmean(ds["d2m"])
                        dew_c = (dew_k - 273.15) if dew_k is not None and np.isfinite(dew_k) else None
                    else:
                        dew_c = None
                    if "u10" in ds and "v10" in ds:
                        u = _sel_point(ds["u10"])
                        v = _sel_point(ds["v10"])
                        wind_arr = np.sqrt(u.values.astype(float) ** 2 + v.values.astype(float) ** 2)
                        if wind_arr.size == 0 or np.all(np.isnan(wind_arr)):
                            wind = None
                        else:
                            w = float(np.nanmean(wind_arr))
                            wind = w if np.isfinite(w) else None
                    else:
                        wind = None
                    if "sp" in ds:
                        sp_val = _nanmean(ds["sp"])
                        pressure = (sp_val / 100.0) if sp_val is not None and np.isfinite(sp_val) else None
                    else:
                        pressure = None
                    ds.close()
                else:
                    from netCDF4 import Dataset
                    import numpy as np

                    with Dataset(nc, mode="r") as ds:
                        def _mean_point(var_name: str) -> float | None:
                            if var_name not in ds.variables:
                                return None
                            arr = ds.variables[var_name][:]
                            a = np.asarray(arr)
                            if a.ndim >= 3:
                                a = a[:, 0, 0]
                            v = np.nanmean(a)
                            return float(v) if np.isfinite(v) else None

                        temp_k = _mean_point("t2m")
                        dew_k = _mean_point("d2m")
                        u = _mean_point("u10")
                        v = _mean_point("v10")
                        sp = _mean_point("sp")

                        temp_c = (temp_k - 273.15) if temp_k is not None else None
                        dew_c = (dew_k - 273.15) if dew_k is not None else None
                        wind = (float((u**2 + v**2) ** 0.5) if (u is not None and v is not None) else None)
                        pressure = (sp / 100.0) if sp is not None else None

                rows.append({
                    "meet_id": meet_id,
                    "source": sub,
                    "temperature": temp_c,
                    "dewpoint": dew_c,
                    "wind_speed": wind,
                    "pressure": pressure,
                })
            except Exception as e:
                print("Skip", nc, e)
    if not rows:
        return None
    return pd.DataFrame(rows)


def add_wbgt(weather_df: pd.DataFrame) -> pd.DataFrame:
    from src.features.wbgt import wbgt_indoor_approx, dewpoint_to_rh
    out = weather_df.copy()
    wbgt = []
    rh_list = []
    for _, r in out.iterrows():
        t = r.get("temperature")
        d = r.get("dewpoint")
        if pd.notna(t) and pd.notna(d):
            rh = dewpoint_to_rh(t, d)
            rh_list.append(rh)
            wbgt.append(wbgt_indoor_approx(t, rh_percent=rh))
        else:
            rh_list.append(None)
            wbgt.append(None)
    out["relative_humidity"] = rh_list
    out["wbgt"] = wbgt
    return out


def pick_weather_by_catalog(weather_df: pd.DataFrame, catalog: pd.DataFrame) -> pd.DataFrame:
    """One row per meet_id: prefer era5_land when catalog.use_era5_land is True."""
    if "use_era5_land" not in catalog.columns or "source" not in weather_df.columns:
        return weather_df.drop(columns=["source"], errors="ignore").drop_duplicates(subset=["meet_id"], keep="first")
    want_land = catalog[["meet_id", "use_era5_land"]].drop_duplicates()
    merged = weather_df.merge(want_land, on="meet_id", how="left")
    # Prefer: (source == "era5_land") == use_era5_land
    merged["_match"] = (merged["source"] == "era5_land") == merged["use_era5_land"]
    # For each meet, keep the matching row; if none, keep any
    merged = merged.sort_values("_match", ascending=False).drop_duplicates(subset=["meet_id"], keep="first")
    return merged.drop(columns=["source", "use_era5_land", "_match"], errors="ignore")


def main() -> None:
    catalog = pd.read_csv(RAW / "meet_catalog.csv")
    locations = pd.read_csv(RAW / "locations.csv")
    merged = catalog.merge(locations, on="meet_id", how="left")

    weather = load_weather_from_netcdf()
    if weather is not None:
        weather = add_wbgt(weather)
        weather = pick_weather_by_catalog(weather, catalog)
        merged = merged.merge(weather, on="meet_id", how="left")
    else:
        merged["temperature"] = None
        merged["dewpoint"] = None
        merged["wind_speed"] = None
        merged["pressure"] = None
        merged["wbgt"] = None

    # Optional: join swim results
    swim_path = RAW / "swimming_results" / "swim_results.csv"
    if swim_path.exists():
        swim = pd.read_csv(swim_path)
        # Keep one row per meet for env dataset; full join would explode rows
        merged = merged  # env is at meet level; results can be joined later by meet_id

    # Unify city/country columns if merge created _x/_y
    for col in ("city", "country"):
        if f"{col}_y" in merged.columns:
            merged[col] = merged[f"{col}_x"].fillna(merged[f"{col}_y"])
            merged = merged.drop(columns=[f"{col}_x", f"{col}_y"])
    merged = merged[~merged["meet_id"].isin(EXCLUDED_MEET_IDS)]
    PROCESSED.mkdir(parents=True, exist_ok=True)
    merged.to_csv(OUT_CSV, index=False)
    print("Wrote", OUT_CSV)


if __name__ == "__main__":
    main()

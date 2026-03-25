"""
Compute Wet Bulb Globe Temperature (WBGT) from ERA5-like weather variables.

WBGT is used for heat-stress analysis on athletes. The implementation uses an approximation that
requires only air temperature, humidity (or dew point), and optional wind speed.

Formula (outdoor, with sun): WBGT = 0.7*Twb + 0.2*Tg + 0.1*Ta
For indoor / simplified: Twb is approximated from Ta and relative humidity
using the Stull formula (wet-bulb from temp and RH), and approximate Tg ≈ Ta
when radiation data is missing, giving:
  WBGT ≈ 0.7*Twb + 0.3*Ta  (indoor-style)
or with a simple Tg approximation: Tg ≈ Ta + small offset.

Reference: Stull, "Wet-Bulb Temperature from Relative Humidity and Air Temperature",
J. Appl. Meteor. Climatol. (2011). Twb = Ta * arctan(0.151977 * (RH% + 8.313659)^0.5)
  + arctan(Ta + RH%) - arctan(RH% - 1.676331) + 0.00391838 * (RH%)^1.5 * arctan(0.023101*RH%)
  - arctan(0.08465)
All in degrees C; RH in percent (0-100).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def dewpoint_to_rh(temperature_c: float, dewpoint_c: float) -> float:
    """Relative humidity (0-100) from temperature and dew point (Magnus formula)."""
    if temperature_c <= dewpoint_c:
        return 100.0
    a, b = 17.27, 237.7
    es_t = 6.112 * math.exp(a * temperature_c / (temperature_c + b))
    es_td = 6.112 * math.exp(a * dewpoint_c / (dewpoint_c + b))
    return 100.0 * (es_td / es_t)


def stull_wet_bulb(temperature_c: float, rh_percent: float) -> float:
    """Wet-bulb temperature (C) from dry-bulb temp (C) and RH (0-100). Stull (2011)."""
    ta = temperature_c
    rh = max(0.0, min(100.0, rh_percent))
    sqrt = math.sqrt(rh + 8.313659)
    atan1 = math.atan(0.151977 * sqrt)
    atan2 = math.atan(ta + rh)
    atan3 = math.atan(rh - 1.676331)
    atan4 = math.atan(0.023101 * rh)
    atan5 = math.atan(0.08465)
    twb = ta * atan1 + atan2 - atan3 + 0.00391838 * (rh ** 1.5) * atan4 - atan5
    return twb


def wbgt_indoor_approx(
    temperature_c: float,
    rh_percent: float | None = None,
    dewpoint_c: float | None = None,
) -> float:
    """
    Indoor/simplified WBGT: 0.7*Twb + 0.3*Ta.
    Provide either rh_percent (0-100) or dewpoint_c (with temperature_c).
    """
    if rh_percent is None:
        if dewpoint_c is not None:
            rh_percent = dewpoint_to_rh(temperature_c, dewpoint_c)
        else:
            return float("nan")
    twb = stull_wet_bulb(temperature_c, rh_percent)
    return 0.7 * twb + 0.3 * temperature_c


def wbgt_outdoor_approx(
    temperature_c: float,
    rh_percent: float | None = None,
    dewpoint_c: float | None = None,
    wind_speed_m_s: float | None = None,
) -> float:
    """
    Outdoor WBGT approximation: 0.7*Twb + 0.2*Tg + 0.1*Ta.
    Tg ≈ Ta (no radiation); wind can be used in future for Tg.
    So currently: 0.7*Twb + 0.2*Ta + 0.1*Ta = 0.7*Twb + 0.3*Ta (same as indoor).
    """
    wb = wbgt_indoor_approx(temperature_c, rh_percent=rh_percent, dewpoint_c=dewpoint_c)
    if math.isnan(wb):
        return wb
    # With Tg ≈ Ta: WBGT = 0.7*Twb + 0.2*Ta + 0.1*Ta = 0.7*Twb + 0.3*Ta
    return wb


def add_wbgt_to_dataframe(
    df: pd.DataFrame,
    temp_col: str = "temperature",
    dewpoint_col: str = "dewpoint",
    rh_col: str | None = None,
    wind_col: str | None = None,
    out_col: str = "wbgt",
    method: str = "indoor",
) -> pd.DataFrame:
    """
    Add a WBGT column to a DataFrame with temperature (C), and either
    dewpoint (C) or relative humidity (0-100). Optional wind for future use.
    """
    out = df.copy()
    temp = out[temp_col].astype(float)
    rh = out[rh_col].astype(float) if rh_col and rh_col in out.columns else None
    dp = out[dewpoint_col].astype(float) if dewpoint_col in out.columns else None

    if rh is not None:
        wbgt_vals = [
            wbgt_indoor_approx(t, rh_percent=rh.iloc[i]) if not (pd.isna(t) or pd.isna(rh.iloc[i])) else np.nan
            for i, t in enumerate(temp)
        ]
    else:
        # From dewpoint
        def row_wbgt(r):
            t = r[temp_col]
            d = r.get(dewpoint_col)
            if pd.isna(t) or pd.isna(d):
                return np.nan
            return wbgt_indoor_approx(t, dewpoint_c=float(d))
        wbgt_vals = out.apply(row_wbgt, axis=1)
    out[out_col] = wbgt_vals
    return out


def compute_wbgt_series(
    temperature_c: np.ndarray | pd.Series,
    dewpoint_c: np.ndarray | pd.Series | None = None,
    rh_percent: np.ndarray | pd.Series | None = None,
) -> np.ndarray:
    """Vectorized: compute WBGT for arrays. Prefer dewpoint if both given."""
    t = np.asarray(temperature_c, dtype=float)
    if dewpoint_c is not None:
        d = np.asarray(dewpoint_c, dtype=float)
        rh = np.array([dewpoint_to_rh(tt, dd) if not (np.isnan(tt) or np.isnan(dd)) else np.nan for tt, dd in zip(t, d)])
    elif rh_percent is not None:
        rh = np.asarray(rh_percent, dtype=float)
    else:
        return np.full_like(t, np.nan)

    out = np.full_like(t, np.nan)
    for i in range(len(t)):
        if not np.isnan(t[i]) and not np.isnan(rh[i]):
            out[i] = wbgt_indoor_approx(t[i], rh_percent=rh[i])
    return out

"""
World Aquatics (FINA) API client.

API pattern:
  1. GET https://api.worldaquatics.com/fina/competitions/{competition_id}/events
     → returns competition + Sports[].DisciplineList[] (each has Id, DisciplineName, Gender)

  2. GET https://api.worldaquatics.com/fina/events/{discipline_id}
     → returns Heats[] (each with Name, PhaseName, UtcDateTime, Results[])

This event-level fetch returns all heats/results for one event in a single call.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests

BASE = "https://api.worldaquatics.com/fina"
RATE_SLEEP = 0.3  # be nice to the API


@dataclass
class HeatRef:
    discipline_id: str
    discipline_name: str
    gender: str
    distance: str
    heat_id: str
    phase: str
    name: str
    unit: str
    is_final: bool


def get_competition_events(competition_id: str) -> dict[str, Any]:
    """GET /fina/competitions/{id}/events. Returns full JSON (competition + sports/disciplines/heats)."""
    url = f"{BASE}/competitions/{competition_id}/events"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    time.sleep(RATE_SLEEP)
    return r.json()


@dataclass
class DisciplineRef:
    discipline_id: str
    discipline_name: str
    gender: str
    distance: str


def iter_disciplines(
    events_payload: dict[str, Any], sport_codes: list[str] | None = ("SW", "OW")
) -> Iterator[DisciplineRef]:
    """Yield DisciplineRef for every discipline (event). By default Swimming (SW) + Open Water (OW)."""
    sports = events_payload.get("Sports") or []
    codes = frozenset(sport_codes or ()) if sport_codes else frozenset()
    for sport in sports:
        code = (sport.get("Code") or "").strip()
        if codes and code not in codes:
            continue
        for disc in sport.get("DisciplineList") or []:
            disc_id = disc.get("Id") or ""
            disc_name = (disc.get("DisciplineName") or "").strip()
            gender = (disc.get("Gender") or "").strip()
            name = (disc.get("Name") or "").strip()
            yield DisciplineRef(
                discipline_id=disc_id,
                discipline_name=disc_name,
                gender=gender,
                distance=name,
            )


def iter_heats(events_payload: dict[str, Any], sport_codes: list[str] | None = ("SW", "OW")) -> Iterator[HeatRef]:
    """Yield HeatRef for every heat in the events payload. By default Swimming (SW) + Open Water (OW)."""
    sports = events_payload.get("Sports") or []
    codes = frozenset(sport_codes or ()) if sport_codes else frozenset()
    for sport in sports:
        code = (sport.get("Code") or "").strip()
        if codes and code not in codes:
            continue
        for disc in sport.get("DisciplineList") or []:
            disc_id = disc.get("Id") or ""
            disc_name = (disc.get("DisciplineName") or "").strip()
            gender = (disc.get("Gender") or "").strip()
            name = (disc.get("Name") or "").strip()  # distance code
            for h in disc.get("HeatList") or []:
                yield HeatRef(
                    discipline_id=disc_id,
                    discipline_name=disc_name,
                    gender=gender,
                    distance=name,
                    heat_id=h.get("Id") or "",
                    phase=(h.get("Phase") or "").strip(),
                    name=(h.get("Name") or "").strip(),
                    unit=(h.get("Unit") or "").strip(),
                    is_final=h.get("IsFinal") or False,
                )


def get_event_results(discipline_id: str) -> list[dict[str, Any]]:
    """
    Fetch all heats/results for one event (discipline).
    GET /fina/events/{discipline_id} returns Heats[] with Results[] in each.
    Returns flat list of result dicts with _race_time, _phase_name, _unit_name, _heat_id.
    """
    url = f"{BASE}/events/{discipline_id}"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        time.sleep(RATE_SLEEP)
        data = r.json()
    except Exception:
        return []
    heats = data.get("Heats") or data.get("heats") or []
    out: list[dict[str, Any]] = []
    for h in heats:
        if not isinstance(h, dict):
            continue
        results = h.get("Results") or h.get("results") or []
        race_time = h.get("UtcDateTime") or h.get("EndUtcDateTime") or h.get("Time") or h.get("EndTime")
        phase_name = h.get("PhaseName") or h.get("Phase") or ""
        unit_name = h.get("Name") or ""
        heat_id = h.get("PhaseId") or h.get("Id") or f"{discipline_id}_{unit_name}"
        for r in results:
            if not isinstance(r, dict):
                continue
            row = dict(r)
            row["_race_date"] = h.get("Date") or h.get("EndDate")
            row["_race_time"] = race_time
            row["_phase_name"] = phase_name
            row["_unit_name"] = unit_name
            row["_heat_id"] = heat_id
            out.append(row)
    return out


def _flatten_units_to_results(units: list[dict]) -> list[dict]:
    """
    API may return list of units; each unit has Date, Time, UtcDateTime, PhaseName, Name, Results[].
    Flatten to one result dict per row, with _race_date, _race_time, _phase_name, _unit_name attached.
    """
    out: list[dict] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        results = unit.get("Results") or unit.get("results") or []
        race_date = unit.get("Date") or unit.get("EndDate")
        race_time = unit.get("UtcDateTime") or unit.get("EndUtcDateTime") or unit.get("Time") or unit.get("EndTime")
        phase_name = unit.get("PhaseName") or unit.get("Phase")
        unit_name = unit.get("Name") or ""
        for r in results:
            if not isinstance(r, dict):
                continue
            row = dict(r)
            row["_race_date"] = race_date
            row["_race_time"] = race_time
            row["_phase_name"] = phase_name
            row["_unit_name"] = unit_name
            out.append(row)
    return out


def get_heat_results(competition_id: str, heat_id: str, discipline_id: str | None = None) -> list[dict[str, Any]]:
    """
    Fetch result rows for one heat. Returns list of result dicts.
    Handles: list of units (each with Results[]), or direct Results list, or dict with Results key.
    """
    candidates = [
        f"/competitions/{competition_id}/units/{heat_id}/results",
        f"/competitions/{competition_id}/heats/{heat_id}/results",
        f"/units/{heat_id}/results",
    ]
    if discipline_id:
        candidates.append(f"/competitions/{competition_id}/results?event={discipline_id}&unit={heat_id}")
    for path in candidates:
        url = BASE + path
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                continue
            time.sleep(RATE_SLEEP)
            data = r.json()
            # List of units (each unit has Results[])
            if isinstance(data, list) and data and isinstance(data[0], dict):
                if "Results" in data[0] or "results" in data[0]:
                    return _flatten_units_to_results(data)
                # Direct list of result dicts (no Results key)
                return data
            if isinstance(data, dict):
                for key in ("Results", "results"):
                    if key in data and isinstance(data[key], list) and data[key]:
                        arr = data[key]
                        if isinstance(arr[0], dict) and ("Results" in arr[0] or "results" in arr[0]):
                            return _flatten_units_to_results(arr)
                        return arr
                for key in ("Entries", "entries", "Athletes", "athletes"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                for v in data.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        return v
            return []
        except Exception:
            continue
    return []


def fetch_competition_results(
    competition_id: str, sport_codes: list[str] | None = ("SW", "OW")
) -> list[dict[str, Any]]:
    """
    Fetch all results for a competition: get events, then for each discipline call
    GET /fina/events/{discipline_id} to get all heats/results in one call.
    Returns flat list of result rows, each enriched with competition_id, heat_id,
    discipline_name, gender, _phase_name, _unit_name, _race_time.
    """
    events = get_competition_events(competition_id)
    out: list[dict[str, Any]] = []
    for ref in iter_disciplines(events, sport_codes=sport_codes):
        rows = get_event_results(ref.discipline_id)
        for row in rows:
            if not isinstance(row, dict):
                continue
            r = dict(row)
            r["_competition_id"] = competition_id
            r["_discipline_id"] = ref.discipline_id
            r["_discipline_name"] = ref.discipline_name
            r["_gender"] = ref.gender
            out.append(r)
    return out

# Swim performance vs environment

### Investigating the relationship between elite swim performance and environmental conditions

> *Does swim performance correlate with environmental conditions (weather, pressure, altitude, location, etc.)?*

---

## Overview

This project investigates whether environmental conditions (ex. atmospheric pressure, temperature, humidity, altitude, geographic location) correlate with elite swimming performance.

1. Build a dataset from major international meets using the World Aquatics API (Swimming + Open Water).
2. Pull reanalysis weather with ERA5 (and optionally ERA5-Land).
3. Merge meet- or race-time environment with results.
4. Explore relationships with statistics and ML in the notebooks.

---

## 1. Build dataset (points-first, finals-only)

Pipeline to scrape swimming results from the World Aquatics API into **SQLite**, with helpers to inspect and export CSV for modeling.

### 0) Install dependencies

From the project root:

```bash
python3 -m venv env
./env/bin/pip install -r requirements.txt
```

### 1) Choose which meets to scrape

Edit:

- `data/raw/world_aquatics_competition_ids.csv`

It maps your `meet_id` (like `oly_2024`, `wc_2024`) to the World Aquatics numeric `competition_id`.

### 2) Scrape into SQLite

This builds/updates:

- `data/raw/swim_db.sqlite`

Run one meet first:

```bash
python3 -m src.scraping.build_swim_db --limit 1
```

Run all rows in `world_aquatics_competition_ids.csv`:

```bash
python3 -m src.scraping.build_swim_db
```

**Notes**

- The scraper fetches `GET /fina/competitions/{competition_id}/events`, then for each event in **Swimming (SW)** and **Open Water (OW)** calls
  `GET /fina/events/{discipline_id}` and flattens the returned `Heats[]` + `Results[]`.
- By default include **SW + OW** (no diving / water polo / artistic swimming).

### 3) Debug line-by-line (recommended when something “stores 0 rows”)

Open and run:

- `notebooks/test_build_swim_db.ipynb`

It downloads the intermediate JSON/CSVs into:

- `data/raw/debug_build_swim_db/`

This makes it easy to see whether the API returned results, how fields are shaped, and what will be inserted.

### 4) Query the SQLite database

List tables:

```bash
python3 -m src.db.query_swim_db tables
```

Row counts:

```bash
python3 -m src.db.query_swim_db counts
```

Show a few rows for a meet:

```bash
python3 -m src.db.query_swim_db sample-results --meet-id oly_2024 --limit 20
```

### 5) Export to CSV for modeling

Export results (joined with `meet_id`) to CSV:

```bash
python3 -m src.db.query_swim_db export-results-csv --meet-id oly_2024 --out data/raw/oly_2024_results.csv
```

Run any SQL and optionally write CSV:

```bash
python3 -m src.db.query_swim_db sql "SELECT meet_id, COUNT(*) n FROM competitions GROUP BY meet_id" --limit 50
python3 -m src.db.query_swim_db sql "SELECT * FROM results LIMIT 100" --out data/raw/results_sample.csv
```

---

## 2. Download weather data

This repo downloads hourly ERA5 (or ERA5-Land) reanalysis weather for each meet location and date range, then merges meet-level weather summaries into `data/processed/swim_environment_dataset.csv`.

### What gets downloaded

`src/weather/era5_download.py` pulls **hourly** data for each `meet_id` (from `data/raw/meet_catalog.csv` + `data/raw/locations.csv`) and writes a NetCDF file per meet:

- **ERA5**: `data/raw/weather/era5/{meet_id}.nc`
- **ERA5-Land**: `data/raw/weather/era5_land/{meet_id}.nc`

Variables requested (ERA5 names → typical NetCDF var names):

- `2m_temperature` → `t2m` (Kelvin)
- `2m_dewpoint_temperature` → `d2m` (Kelvin) (lets you derive humidity / RH)
- `10m_u_component_of_wind` → `u10`
- `10m_v_component_of_wind` → `v10`
- `surface_pressure` → `sp` (Pa)

### Prereqs (CDS / Copernicus)

ERA5 downloads require a Copernicus Climate Data Store account and API key:

- The `cdsapi` client is listed in `requirements.txt`.
- Configure `~/.cdsapirc` per [CDS API setup](https://cds.climate.copernicus.eu/how-to-api).

### Download ERA5 for all meets

```bash
python3 -m src.weather.era5_download
```

The script is resumable:

- It writes a log to `data/raw/weather/download_log.csv`
- It also skips if the `.nc` already exists

### Sync meet catalog from competition IDs

After editing `data/raw/world_aquatics_competition_ids.csv` (and scraping so `swim_db.sqlite` has `competitions` rows), rebuild the catalog:

```bash
python3 -m src.geolocation.sync_meet_catalog
```

This merges **notes + dates from SQLite** (`from_date` / `to_date` per `competition_id`) into `data/raw/meet_catalog.csv`. **`use_era5_land`** is kept from your previous catalog when the `meet_id` already existed (defaults: open-water-style `meet_id` prefixes → `False`, else `True`).  
Meets listed only in **`data/raw/meet_catalog_supplement.csv`** (e.g. legacy `wc_2013`) are appended if they are not in the competition-id list. Then run **`python3 -m src.geolocation.geocode_meets`** for any new `meet_id` before ERA5 download.

### Download ERA5 for *more* meets (bulk)

1. **Add rows** to `data/raw/meet_catalog.csv` (or run **`sync_meet_catalog`** above after updating competition IDs) (`meet_id`, `meet_name`, `city`, `country`, `start_date`, `end_date`, `use_era5_land`). Use `True` for `use_era5_land` for most land/pool venues (finer grid); `False` forces full **ERA5 single levels** (e.g. some coastal/open-water cases).
2. **Geocode** so every new `meet_id` has lat/lon:
   ```bash
   python3 -m src.geolocation.geocode_meets
   ```
   (Merge or edit `data/raw/locations.csv` if you already have coordinates.)
3. **See what’s missing** (no coords / no file / already logged):
   ```bash
   python3 -m src.weather.era5_download --status
   ```
4. **Download** new meets only (default: skips anything already in the log *and* present on disk):
   ```bash
   python3 -m src.weather.era5_download
   ```
   **Only certain meets:**
   ```bash
   python3 -m src.weather.era5_download --meet-id wc_2025 --meet-id swcup_2025_toronto
   ```
   **Re-download** (bad/corrupt NetCDF or wrong dates after fixing catalog):
   ```bash
   python3 -m src.weather.era5_download --meet-id wc_2023 --force
   ```
   **Dry run** (no CDS API calls):
   ```bash
   python3 -m src.weather.era5_download --dry-run
   ```
   If the log says “skip” but the `.nc` file is missing, run with **`--ignore-log`** (or **`--force`** to replace files).

CDS queues can be long; many meets = many separate requests (one NetCDF per meet).

### Merge weather into the meet dataset

`src/merge_pipeline.py` loads all NetCDFs (requires `xarray`), computes **meet-level means** (average across the downloaded period), adds WBGT (via `src/features/wbgt.py`), and writes:

- `data/processed/swim_environment_dataset.csv`

Run:

```bash
python3 -m src.merge_pipeline
```

**Note:** the merge output is **meet-aggregated** (one weather row per meet). For **race-time** (hourly) weather, use the step below.

### Race-time weather (hourly at race start)

So that environment reflects conditions at the actual race time (not the meet average), the pipeline can attach **hourly** ERA5 values at each result’s `race_start_time` (interpreted in the meet’s timezone, then converted to UTC to index the NetCDF).

1. **Export must include `race_start_time`** – Notebook 01’s SQL already selects `race_start_time`; re-run 01 to get `results_with_environment.csv` with that column.
2. **Run the race-time weather script** (requires `xarray` and NetCDFs under `data/raw/weather/era5_land/` or `era5/`):
   ```bash
   python3 -m src.weather.race_time_weather
   ```
   This reads `data/processed/results_with_environment.csv` and `data/raw/locations.csv`, indexes each meet’s NetCDF at the UTC hour for each row’s `race_start_time`, and writes **`data/processed/results_with_race_time_weather.csv`** with extra columns: `race_time_temperature`, `race_time_dewpoint`, `race_time_wind_speed`, `race_time_pressure`, `race_time_relative_humidity`, `race_time_wbgt`. Use this CSV in the athlete-centric notebook (04) with env features set to these columns for race-time analysis.

### Join results and run analysis (notebooks)

1. **`notebooks/01_join_results_environment.ipynb`** – Loads results from `data/raw/swim_db.sqlite`, filters to **Finals only** and requires non-null **`points`**, joins with `data/processed/swim_environment_dataset.csv` on `meet_id`, and writes `data/processed/results_with_environment.csv` (points-first dataset with meet-level env). `time_seconds` is optional for debugging.
2. **`notebooks/02_statistical_analysis.ipynb`** – Loads `results_with_environment.csv`, aggregates by (meet, discipline, gender), correlations, scatter plots, heatmaps, box plots by temperature quartile, distributions by event, and correlation heatmaps.

3. **`notebooks/03_ml_models.ipynb`** – Event-level prediction of mean race time from environment + event type: **Ridge** and **Random Forest**, train/test split by meet, feature importance, predicted vs actual, and residual plots. (High R² here is largely from event identity; see the notebook.)

4. **`notebooks/04_athlete_centric_models.ipynb`** – **Athlete-centric** modeling: loads `results_with_environment.csv` (must include **person_id**). **Target A** (within-meet, within-event z-score) and **Target B** (delta vs swimmer baseline for that event, leakage-safe). Meet-holdout split; Ridge and RF on env + event; coefficient plot, predicted vs actual, residuals, and within-event scatter.

Run 01 first (from project root or from `notebooks/`), then 02, 03, and 04. Notebook 01 already selects `person_id` and `race_start_time`. For **race-time** env in 04, run `python3 -m src.weather.race_time_weather` after 01 and point 04 at `results_with_race_time_weather.csv` using the `race_time_*` columns as env features.

### Adding more meets vs open water

- **More pool meets (e.g. World Cup, regional champs)** – Adds env and athlete variation and repeated swimmers, which improves the pipeline and model signal. Add rows to `world_aquatics_competition_ids.csv` and re-run the scraper and downstream steps before investing heavily in open water.
- **Open water** – Environment is more directly relevant, but events and API shape may differ; venues are often coastal. Treat as a **separate track** once the pool pipeline and race-time weather are stable (e.g. separate competition list and notebooks).

---

## Research questions

- Is there a statistical association between **environmental variables** and swim performance (e.g. FINA points)?
- Do conditions correlate with **faster or slower** times after accounting for event and meet?
- How do **altitude**, **pressure**, and **venue** show up in aggregated or athlete-level models?
- (Future) **Travel / timezone** and **fast-pool** effects need extra data not yet in this repo.

---

## Hypotheses (informal)

- **H1:** Lower pressure / higher altitude may associate with slightly reduced performance (oxygen availability).
- **H2:** Moderate temperature and humidity may associate with comfort-related effects on performance.
- **H3:** Venues may differ systematically (pool, altitude, climate) beyond pure weather at race time.
- **H4:** Environment may explain **a small fraction** of variance once event and athlete effects are modeled.

---

## Data sources (what this repo actually uses)

| Layer | Source | Role |
| ----- | ------ | ---- |
| Results | World Aquatics API → `src/scraping/build_swim_db.py` | SQLite `data/raw/swim_db.sqlite` |
| Meet metadata | `sync_meet_catalog`, competition IDs CSV, optional `meet_catalog_supplement.csv` | Dates and meet list for weather windows |
| Coordinates | `src/geolocation/geocode_meets.py` | `data/raw/locations.csv` (+ cache under `data/raw/locations/`) |
| Weather | ERA5 / ERA5-Land via `cdsapi` | NetCDF under `data/raw/weather/` |

Optional / auxiliary scripts under `src/scraping/` (e.g. results books, Olympic CSV helpers) are not required for the main API → SQLite → ERA5 → notebooks path.

---

## Repository layout

```
swim-performance/
├── data/
│   ├── raw/
│   │   ├── world_aquatics_competition_ids.csv
│   │   ├── meet_catalog.csv
│   │   ├── meet_catalog_supplement.csv
│   │   ├── locations.csv
│   │   ├── swim_db.sqlite          # created by scraper
│   │   ├── weather/                # ERA5 NetCDF + download_log.csv
│   │   └── debug_build_swim_db/    # optional debug output from test notebook
│   └── processed/
│       ├── swim_environment_dataset.csv
│       ├── results_with_environment.csv
│       └── results_with_race_time_weather.csv   # after race_time_weather
├── notebooks/
│   ├── 01_join_results_environment.ipynb
│   ├── 02_statistical_analysis.ipynb
│   ├── 03_ml_models.ipynb
│   ├── 04_athlete_centric_models.ipynb
│   └── test_build_swim_db.ipynb
├── src/
│   ├── scraping/
│   ├── db/
│   ├── geolocation/
│   ├── weather/
│   ├── features/                   # e.g. WBGT helpers
│   ├── merge_pipeline.py
│   └── ...
├── requirements.txt
└── README.md
```

---

## Features and modeling (implemented)

- **Performance:** FINA **points** (primary in 01–04), optional time fields for debugging or event-level models.
- **Environment (meet-level):** Means over the meet window from NetCDF + **WBGT** in `merge_pipeline` / `swim_environment_dataset.csv`.
- **Environment (race-time):** Hourly slice at `race_start_time` via `race_time_weather.py` → `race_time_*` columns.
- **ML in notebooks:** **Ridge** and **Random Forest** only (no XGBoost/LightGBM in the current notebooks).

---

## Challenges

- **Confounding:** Training phase, tech suits, pool depth, and meet prestige dominate many signals; athlete- and event-aware targets (notebook 04) help but do not remove all confounding.
- **Indoor pools:** Weather is often indirect; pressure and altitude still matter for physiology and facility location.
- **Data gaps:** Missing precise pool coordinates or timestamps limit some analyses; race-time weather needs valid `race_start_time` and timezone-aware handling.

---

## Extensions (not implemented here)

- More meets (World Cup, nationals) via the competition ID list and scraper.
- Extra covariates: pool depth, lane, travel distance, timezone shift.
- Stronger inference: hierarchical or causal models beyond the current sklearn baselines.

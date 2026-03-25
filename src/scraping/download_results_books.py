"""
Download Omega Timing RESULTS BOOK (PDF) per meet.

Reads meet_id -> URL from data/raw/results_book_urls.csv (columns: meet_id, results_book_url).
Skips rows with empty URL. Saves PDFs to data/raw/swimming_results/{meet_id}_results.pdf.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import requests

# Default paths (relative to project root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
URLS_CSV = PROJECT_ROOT / "data" / "raw" / "results_book_urls.csv"
OUT_DIR = PROJECT_ROOT / "data" / "raw" / "swimming_results"

# Optional: request headers to avoid blocks
HEADERS = {
    "User-Agent": "SwimPerformancePipeline/1.0 (research; +https://github.com)",
}


def load_urls(csv_path: Path | None = None) -> list[tuple[str, str]]:
    """Load (meet_id, url) pairs from CSV. Skips empty URLs."""
    path = csv_path or URLS_CSV
    pairs: list[tuple[str, str]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            meet_id = (row.get("meet_id") or "").strip()
            url = (row.get("results_book_url") or "").strip()
            if meet_id and url and url.startswith("http"):
                pairs.append((meet_id, url))
    return pairs


def download_one(meet_id: str, url: str, out_dir: Path | None = None) -> Path | None:
    """Download one results book to out_dir/{meet_id}_results.pdf. Returns path or None on failure."""
    dir_path = out_dir or OUT_DIR
    dir_path.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^\w\-]", "_", meet_id)
    out_path = dir_path / f"{safe_id}_results.pdf"

    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        out_path.write_bytes(r.content)
        return out_path
    except Exception as e:
        print(f"Download failed {meet_id}: {e}")
        return None


def main() -> None:
    pairs = load_urls()
    if not pairs:
        print("No results_book_urls found in", URLS_CSV)
        return
    for meet_id, url in pairs:
        path = download_one(meet_id, url)
        if path:
            print("Downloaded", path)


if __name__ == "__main__":
    main()

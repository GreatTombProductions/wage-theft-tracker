"""
Acquire DOL Wage and Hour Division enforcement data.

Data source: https://data.dol.gov/data-catalog/WHD/enforcement/WHD_enforcement.zip
Quarterly updated bulk download of all concluded WHD compliance actions since FY 2005.
~365K records, ~189MB ZIP containing 8 CSV chunks.

Mirror: https://github.com/labordata/whd-compliance (Makefile uses same URL)
"""

import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"

DOWNLOAD_URL = "https://data.dol.gov/data-catalog/WHD/enforcement/WHD_enforcement.zip"
ZIP_NAME = "WHD_enforcement.zip"


def download_whd_data():
    """Download WHD enforcement data ZIP from DOL."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = RAW_DIR / ZIP_NAME

    # Check for existing CSVs
    existing_csvs = list(RAW_DIR.glob("LOAD*.csv"))
    if existing_csvs:
        total_size = sum(f.stat().st_size for f in existing_csvs)
        print(f"Data already extracted: {len(existing_csvs)} CSV files ({total_size / (1024*1024):.1f} MB)")
        print("Delete them to re-download.")
        return existing_csvs

    print("Downloading WHD enforcement data (~189 MB)...")
    print(f"URL: {DOWNLOAD_URL}")

    result = subprocess.run(
        ["curl", "-L", "-o", str(zip_path), DOWNLOAD_URL],
        capture_output=False
    )
    if result.returncode != 0:
        print("Download failed.", file=sys.stderr)
        sys.exit(1)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Downloaded: {zip_path} ({size_mb:.1f} MB)")

    # Extract
    print("Extracting...")
    subprocess.run(["unzip", "-o", str(zip_path), "-d", str(RAW_DIR)], check=True)

    # List extracted files
    csvs = sorted(RAW_DIR.glob("LOAD*.csv"))
    total_size = sum(f.stat().st_size for f in csvs)
    print(f"Extracted {len(csvs)} CSV files ({total_size / (1024*1024):.1f} MB)")

    return csvs


if __name__ == "__main__":
    csvs = download_whd_data()
    for f in csvs:
        print(f"  {f.name}: {f.stat().st_size / (1024*1024):.1f} MB")

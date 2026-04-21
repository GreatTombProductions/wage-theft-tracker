"""
Full pipeline orchestrator for Wage Theft Tracker.

Usage:
    python3 pipeline/run.py              # Run full pipeline
    python3 pipeline/run.py --clean      # Skip acquisition, re-clean and re-index
    python3 pipeline/run.py --index      # Skip clean, re-index only
    python3 pipeline/run.py --geo        # Regenerate county data only
    python3 pipeline/run.py --industry   # Regenerate industry percentiles only
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "pipeline"))

from acquire import download_whd_data
from clean import clean_data
from index import build_index
from geo import build_county_data
from industry import build_industry_data


def main():
    args = set(sys.argv[1:])

    if "--index" in args:
        print("=" * 60)
        print("INDEXING ONLY (using existing DuckDB)")
        print("=" * 60)
        build_index()
        return

    if "--geo" in args:
        print("=" * 60)
        print("GEOGRAPHIC DATA ONLY (using existing DuckDB)")
        print("=" * 60)
        build_county_data()
        return

    if "--industry" in args:
        print("=" * 60)
        print("INDUSTRY DATA ONLY (using existing DuckDB)")
        print("=" * 60)
        build_industry_data()
        return

    if "--clean" not in args:
        print("=" * 60)
        print("STEP 1: DATA ACQUISITION")
        print("=" * 60)
        download_whd_data()
        print()

    print("=" * 60)
    print("STEP 2: CLEANING & NORMALIZATION")
    print("=" * 60)
    clean_data()
    print()

    print("=" * 60)
    print("STEP 3: INDEX GENERATION")
    print("=" * 60)
    build_index()
    print()

    print("=" * 60)
    print("STEP 4: GEOGRAPHIC AGGREGATION")
    print("=" * 60)
    build_county_data()
    print()

    print("=" * 60)
    print("STEP 5: INDUSTRY PERCENTILES")
    print("=" * 60)
    build_industry_data()
    print()

    print("=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print("To serve locally: python3 -m http.server 8080 -d frontend/")


if __name__ == "__main__":
    main()

"""
Geographic aggregation for county choropleth (Phase 2).

Input: Clean DuckDB database from clean.py
Output:
  frontend/data/counties.json  — Per-county enforcement stats for choropleth

Approach:
  1. Download Census ZCTA-to-county relationship file (free, no auth)
  2. Map case ZIP codes to county FIPS via ZCTA crosswalk
  3. For ZCTAs spanning multiple counties, assign to county with largest land area overlap
  4. Aggregate enforcement stats per county
  5. Output compact JSON keyed by 5-digit county FIPS

Frontend loads county boundaries from US Atlas TopoJSON CDN (~700KB),
joins with this data file (~50-80KB) for choropleth rendering.
"""

import csv
import io
import json
import urllib.request
from collections import defaultdict
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "whd.duckdb"
OUTPUT_DIR = PROJECT_ROOT / "frontend" / "data"
CACHE_DIR = PROJECT_ROOT / "data" / "geo_cache"

ZCTA_COUNTY_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/"
    "zcta520/tab20_zcta520_county20_natl.txt"
)


def download_zcta_crosswalk() -> dict[str, tuple[str, str]]:
    """
    Download Census ZCTA-to-county crosswalk.

    Returns dict: zip_code -> (county_fips, county_name)
    For ZCTAs spanning multiple counties, keeps the one with largest land area.
    """
    cache_path = CACHE_DIR / "zcta_county.json"
    if cache_path.exists():
        print("  Using cached ZCTA-to-county crosswalk")
        with open(cache_path) as f:
            return json.load(f)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading ZCTA-to-county crosswalk...")
    req = urllib.request.Request(ZCTA_COUNTY_URL, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=60)
    data = resp.read().decode("utf-8-sig")

    # Parse pipe-delimited file
    # Columns: OID_ZCTA5_20|GEOID_ZCTA5_20|...|OID_COUNTY_20|GEOID_COUNTY_20|
    #          NAMELSAD_COUNTY_20|...|AREALAND_PART|AREAWATER_PART
    lines = data.strip().split("\n")
    header = lines[0].split("|")

    # Find column indices
    zcta_idx = header.index("GEOID_ZCTA5_20")
    county_fips_idx = header.index("GEOID_COUNTY_20")
    county_name_idx = header.index("NAMELSAD_COUNTY_20")
    area_land_idx = header.index("AREALAND_PART")

    # For each ZCTA, keep the county with the largest land area overlap
    zcta_best: dict[str, tuple[str, str, int]] = {}  # zcta -> (fips, name, area)

    for line in lines[1:]:
        parts = line.split("|")
        zcta = parts[zcta_idx].strip()
        if not zcta:
            continue
        county_fips = parts[county_fips_idx].strip()
        county_name = parts[county_name_idx].strip()
        try:
            area = int(parts[area_land_idx].strip())
        except (ValueError, IndexError):
            area = 0

        if zcta not in zcta_best or area > zcta_best[zcta][2]:
            zcta_best[zcta] = (county_fips, county_name, area)

    # Build final mapping: zcta -> (fips, name)
    crosswalk = {z: [v[0], v[1]] for z, v in zcta_best.items()}
    print(f"  Crosswalk: {len(crosswalk):,} ZCTAs mapped to counties")

    # Cache for future runs
    with open(cache_path, "w") as f:
        json.dump(crosswalk, f)

    return crosswalk


def build_county_data():
    """Aggregate enforcement data by county and write frontend JSON."""
    print("Building county geographic data...")

    crosswalk = download_zcta_crosswalk()
    con = duckdb.connect(str(DB_PATH), read_only=True)

    # Get all cases with valid zip codes
    print("  Querying cases...")
    cases = con.execute("""
        SELECT
            LPAD(TRIM(zip_code), 5, '0') as zip5,
            COUNT(*) as case_count,
            COALESCE(SUM(back_wages), 0) as total_bw,
            COALESCE(SUM(civil_penalties), 0) as total_penalties,
            COALESCE(SUM(employees_violated), 0) as total_employees,
            COUNT(*) FILTER (WHERE violation_count > 0) as cases_with_violations,
            COUNT(*) FILTER (WHERE has_flsa) as flsa_cases,
            COUNT(*) FILTER (WHERE has_child_labor) as child_labor_cases,
            COUNT(*) FILTER (WHERE has_h1b OR has_h2a OR has_h2b) as visa_cases,
            MIN(findings_start) as earliest,
            MAX(findings_end) as latest
        FROM cases
        WHERE zip_code IS NOT NULL AND TRIM(zip_code) != ''
        GROUP BY zip5
    """).fetchall()
    con.close()

    print(f"  {len(cases):,} unique ZIP codes with cases")

    # Aggregate by county FIPS
    county_data: dict[str, dict] = defaultdict(lambda: {
        "n": "",  # county name
        "c": 0,   # case count
        "bw": 0,  # back wages
        "p": 0,   # penalties
        "e": 0,   # employees
        "v": 0,   # cases with violations
        "fl": 0,  # FLSA cases
        "cl": 0,  # child labor cases
        "vi": 0,  # visa worker cases
    })

    matched = 0
    unmatched = 0
    unmatched_cases = 0

    for row in cases:
        zip5, case_count, bw, penalties, employees, violations, flsa, cl, visa, _, _ = row
        if zip5 in crosswalk:
            fips, name = crosswalk[zip5]
            d = county_data[fips]
            d["n"] = name
            d["c"] += case_count
            d["bw"] += bw
            d["p"] += penalties
            d["e"] += employees
            d["v"] += violations
            d["fl"] += flsa
            d["cl"] += cl
            d["vi"] += visa
            matched += 1
        else:
            unmatched += 1
            unmatched_cases += case_count

    print(f"  Matched: {matched:,} ZIPs ({matched/(matched+unmatched)*100:.1f}%)")
    print(f"  Unmatched: {unmatched:,} ZIPs ({unmatched_cases:,} cases)")

    # Round monetary values and compute per-county derived metrics
    output = {}
    for fips, d in county_data.items():
        output[fips] = {
            "n": d["n"],
            "c": d["c"],
            "bw": round(d["bw"]),
            "p": round(d["p"]),
            "e": d["e"],
            "v": d["v"],
            "fl": d["fl"],
            "cl": d["cl"],
            "vi": d["vi"],
        }

    # Write output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "counties.json"
    with open(output_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    size_kb = output_path.stat().st_size / 1024
    print(f"\n=== COUNTY DATA ===")
    print(f"  Counties: {len(output):,}")
    print(f"  Output: {output_path} ({size_kb:.0f} KB)")

    # Summary stats
    total_cases = sum(d["c"] for d in output.values())
    total_bw = sum(d["bw"] for d in output.values())
    top_counties = sorted(output.items(), key=lambda x: x[1]["c"], reverse=True)[:10]
    print(f"  Total cases mapped: {total_cases:,}")
    print(f"  Total back wages mapped: ${total_bw:,.0f}")
    print(f"\n  Top 10 counties by case count:")
    for fips, d in top_counties:
        print(f"    {d['n']:30s} ({fips}): {d['c']:>6,d} cases, ${d['bw']:>14,.0f} back wages")

    return output_path


if __name__ == "__main__":
    build_county_data()

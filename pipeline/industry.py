"""
Generate industry comparison percentile data for the static frontend.

Input: Clean DuckDB database from clean.py
Output:
  frontend/data/industry.json — Pre-computed percentile distributions

For each industry_category (national) and state+industry_category (where n >= 30):
  - Employer-level aggregates (total back wages, total cases, total employees affected)
  - Percentile breakpoints: p25, p50, p75, p90, p95, p99
  - Count of employers in the comparison group

This allows the frontend to compute "this employer's total back wages are higher
than X% of [industry] employers investigated" without shipping the raw data.

Architecture note: percentiles are computed over EMPLOYER aggregates, not raw cases.
An employer with 5 cases totaling $100K is compared against the distribution of
employer-level totals, not case-level amounts. This is the meaningful comparison
for a user evaluating a specific employer.
"""

import json
import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "whd.duckdb"
OUTPUT_PATH = PROJECT_ROOT / "frontend" / "data" / "industry.json"

# Minimum employers in a comparison group for it to be included
MIN_GROUP_SIZE = 30

# Percentile breakpoints to pre-compute
PERCENTILES = [25, 50, 75, 90, 95, 99]


def compute_percentiles(con, where_clause="", params=None):
    """Compute employer-level percentile distributions for a group.

    Returns dict with distributions for back_wages, case_count, and employees,
    or None if the group has fewer than MIN_GROUP_SIZE employers.
    """
    if params is None:
        params = []

    # First: build employer aggregates within the group
    employer_aggs = con.execute(f"""
        SELECT
            name_normalized,
            COUNT(*) as cases,
            ROUND(SUM(back_wages), 2) as total_bw,
            SUM(employees_violated) as total_ee,
            ROUND(SUM(civil_penalties), 2) as total_cp
        FROM cases
        WHERE name_normalized IS NOT NULL
          AND TRIM(name_normalized) != ''
          {where_clause}
        GROUP BY name_normalized
    """, params).fetchall()

    n = len(employer_aggs)
    if n < MIN_GROUP_SIZE:
        return None

    # Extract arrays for percentile computation
    bw_vals = sorted([r[2] or 0 for r in employer_aggs])
    case_vals = sorted([r[1] for r in employer_aggs])
    ee_vals = sorted([r[3] or 0 for r in employer_aggs])
    cp_vals = sorted([r[4] or 0 for r in employer_aggs])

    def pcts(vals):
        """Compute percentile breakpoints from a sorted list."""
        result = {}
        for p in PERCENTILES:
            idx = int(len(vals) * p / 100)
            idx = min(idx, len(vals) - 1)
            result[f"p{p}"] = round(vals[idx], 2)
        result["mean"] = round(sum(vals) / len(vals), 2) if vals else 0
        result["max"] = round(vals[-1], 2) if vals else 0
        return result

    return {
        "n": n,
        "bw": pcts(bw_vals),
        "cases": pcts(case_vals),
        "ee": pcts(ee_vals),
        "cp": pcts(cp_vals),
    }


def build_industry_data():
    """Generate industry comparison JSON."""
    con = duckdb.connect(str(DB_PATH), read_only=True)

    output = {
        "national": {},     # industry_category -> percentile data
        "state": {},        # "ST:industry_category" -> percentile data
    }

    # --- National percentiles by industry_category ---
    print("Computing national industry percentiles...")

    categories = [r[0] for r in con.execute("""
        SELECT DISTINCT industry_category FROM cases
        WHERE industry_category IS NOT NULL
        ORDER BY industry_category
    """).fetchall()]

    for cat in categories:
        result = compute_percentiles(
            con,
            "AND industry_category = ?",
            [cat]
        )
        if result:
            output["national"][cat] = result
            print(f"  {cat:35s}  {result['n']:>6,} employers")
        else:
            print(f"  {cat:35s}  SKIPPED (< {MIN_GROUP_SIZE} employers)")

    # --- State-level percentiles by state + industry_category ---
    print("\nComputing state-level industry percentiles...")

    state_cats = con.execute("""
        SELECT DISTINCT state, industry_category
        FROM cases
        WHERE state IS NOT NULL AND TRIM(state) != ''
          AND industry_category IS NOT NULL
        ORDER BY state, industry_category
    """).fetchall()

    included = 0
    skipped = 0

    for state, cat in state_cats:
        result = compute_percentiles(
            con,
            "AND state = ? AND industry_category = ?",
            [state, cat]
        )
        if result:
            key = f"{state}:{cat}"
            output["state"][key] = result
            included += 1
        else:
            skipped += 1

    print(f"  {included} state+category groups included, {skipped} skipped (< {MIN_GROUP_SIZE})")

    # --- Write output ---
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"\n=== OUTPUT ===")
    print(f"  {OUTPUT_PATH} ({size_kb:.0f} KB)")
    print(f"  National: {len(output['national'])} categories")
    print(f"  State-level: {len(output['state'])} groups")

    con.close()
    return OUTPUT_PATH


if __name__ == "__main__":
    build_industry_data()

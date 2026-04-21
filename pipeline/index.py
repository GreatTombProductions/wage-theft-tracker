"""
Generate search index and per-state JSON chunks for the static frontend.

Input: Clean DuckDB database from clean.py
Output:
  frontend/data/
    states/{ST}.json          — Per-state case data, loaded on demand
    search/{prefix}.json      — Search chunks by first letter (A-Z, 0-9, _other)
    search/manifest.json      — Chunk sizes for preload hints
    stats.json                — National summary statistics
    meta.json                 — Data vintage, column metadata

Search architecture:
  1. User types employer name
  2. After 2+ chars, frontend fetches search/{first_letter}.json (~100-200KB)
  3. Client-side fuzzy match within loaded chunk
  4. Results show: employer name, states, case count, total back wages
  5. Click result → fetch state JSON(s), filter to that employer
  6. Display full case details

  Initial load: stats.json (6KB) only
  First search keystroke: one search chunk (~100-200KB gzipped ~50-100KB)
  Click result: one state JSON (~200KB-2MB)
"""

import json
import gzip
import duckdb
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "whd.duckdb"
OUTPUT_DIR = PROJECT_ROOT / "frontend" / "data"
STATES_DIR = OUTPUT_DIR / "states"
SEARCH_DIR = OUTPUT_DIR / "search"


def build_index():
    """Generate all frontend data files."""
    STATES_DIR.mkdir(parents=True, exist_ok=True)
    SEARCH_DIR.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(DB_PATH), read_only=True)

    # -------------------------------------------------------
    # 1. Per-state JSON chunks (compact format)
    # -------------------------------------------------------
    print("Generating per-state JSON chunks...")

    states = [r[0] for r in con.execute("""
        SELECT DISTINCT state FROM cases
        WHERE state IS NOT NULL AND TRIM(state) != ''
        ORDER BY state
    """).fetchall()]

    state_sizes = {}
    total_exported = 0

    for state in states:
        cases = con.execute("""
            SELECT
                case_id, trade_name, legal_name, name_normalized,
                city, zip_code, industry, industry_category,
                findings_start::VARCHAR, findings_end::VARCHAR,
                violation_count, employees_violated,
                ROUND(back_wages, 2), employees_owed_back_wages,
                ROUND(civil_penalties, 2), violation_types,
                ROUND(flsa_min_wage_bw, 2), ROUND(flsa_overtime_bw, 2),
                has_flsa, has_child_labor, has_fmla, flsa_repeat_violator
            FROM cases WHERE state = ?
            ORDER BY back_wages DESC, findings_end DESC NULLS LAST
        """, [state]).fetchall()

        records = []
        for row in cases:
            (cid, trade, legal, norm, city, zip_c, ind, ind_cat,
             fs, fe, vcnt, ev, bw, ee_bw, cp, vtypes,
             mw_bw, ot_bw, has_f, has_cl, has_fm, flsa_rpt) = row

            r = {"id": cid}
            if trade: r["tn"] = trade  # noqa: E701
            if legal: r["ln"] = legal  # noqa: E701
            if norm: r["nn"] = norm  # noqa: E701
            if city: r["ct"] = city  # noqa: E701
            if zip_c: r["zp"] = zip_c  # noqa: E701
            if ind: r["in"] = ind  # noqa: E701
            if ind_cat: r["ic"] = ind_cat  # noqa: E701
            if fs: r["fs"] = fs[:10]  # noqa: E701
            if fe: r["fe"] = fe[:10]  # noqa: E701
            if vcnt: r["vc"] = vcnt  # noqa: E701
            if ev: r["ev"] = ev  # noqa: E701
            if bw: r["bw"] = bw  # noqa: E701
            if ee_bw: r["eb"] = ee_bw  # noqa: E701
            if cp: r["cp"] = cp  # noqa: E701
            if vtypes and vtypes != "No violations found": r["vt"] = vtypes  # noqa: E701
            if mw_bw: r["mw"] = mw_bw  # noqa: E701
            if ot_bw: r["ot"] = ot_bw  # noqa: E701
            if has_f: r["fl"] = 1  # noqa: E701
            if has_cl: r["cl"] = 1  # noqa: E701
            if has_fm: r["fm"] = 1  # noqa: E701
            if flsa_rpt and flsa_rpt not in ("N/A", ""): r["rp"] = flsa_rpt  # noqa: E701
            records.append(r)

        output_path = STATES_DIR / f"{state}.json"
        with open(output_path, "w") as f:
            json.dump(records, f, separators=(",", ":"))

        size_kb = output_path.stat().st_size / 1024
        state_sizes[state] = size_kb
        total_exported += len(records)

    total_size_mb = sum(state_sizes.values()) / 1024
    print(f"  {len(states)} states, {total_exported:,} cases, {total_size_mb:.1f} MB total")

    # -------------------------------------------------------
    # 2. Chunked search index (by first letter)
    # -------------------------------------------------------
    print("\nBuilding chunked search index...")

    rows = con.execute("""
        SELECT
            COALESCE(MIN(trade_name), MIN(legal_name), name_normalized) as display,
            name_normalized,
            LIST(DISTINCT state ORDER BY state) as states,
            COUNT(*) as cnt,
            ROUND(SUM(back_wages), 0)::BIGINT as bw
        FROM cases
        WHERE name_normalized IS NOT NULL AND TRIM(name_normalized) != ''
          AND state IS NOT NULL AND TRIM(state) != ''
        GROUP BY name_normalized
        ORDER BY cnt DESC, bw DESC
    """).fetchall()

    # Group by first character of normalized name
    chunks = defaultdict(list)
    for display, norm, st_list, cnt, bw in rows:
        if not norm or not st_list:
            continue
        first_char = norm[0].upper() if norm else "_"
        if first_char.isalpha():
            key = first_char
        elif first_char.isdigit():
            key = "0"  # All numbers in one chunk
        else:
            key = "_"  # Other characters

        # Compact format: [display_name, states_joined, case_count, back_wages]
        entry = [
            display[:80],
            ",".join(st_list),
            cnt,
        ]
        if bw and bw > 0:
            entry.append(int(bw))

        chunks[key].append(entry)

    # Write each chunk
    manifest = {}
    total_search_size = 0
    total_search_gz = 0

    for key in sorted(chunks.keys()):
        entries = chunks[key]
        chunk_path = SEARCH_DIR / f"{key}.json"

        with open(chunk_path, "w") as f:
            json.dump(entries, f, separators=(",", ":"))

        raw_size = chunk_path.stat().st_size

        # Gzip for size estimation
        gz_path = SEARCH_DIR / f"{key}.json.gz"
        with open(chunk_path, "rb") as f_in:
            with gzip.open(gz_path, "wb", compresslevel=9) as f_out:
                f_out.write(f_in.read())
        gz_size = gz_path.stat().st_size

        manifest[key] = {
            "count": len(entries),
            "size": raw_size,
            "gzip": gz_size
        }
        total_search_size += raw_size
        total_search_gz += gz_size

    # Write manifest
    manifest_path = SEARCH_DIR / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"  {len(chunks)} chunks, {sum(len(v) for v in chunks.values()):,} employers")
    print(f"  Total: {total_search_size / (1024*1024):.1f} MB raw, {total_search_gz / (1024*1024):.1f} MB gzipped")

    # Show chunk distribution
    print("  Chunk sizes (gzipped):")
    for key in sorted(manifest.keys()):
        info = manifest[key]
        bar = "#" * (info["gzip"] // 10000)
        print(f"    {key}: {info['count']:>6,} employers, {info['gzip']/1024:>6.0f} KB {bar}")

    # -------------------------------------------------------
    # 3. Summary statistics
    # -------------------------------------------------------
    print("\nGenerating summary statistics...")

    stats = {
        "total_cases": con.execute("SELECT COUNT(*) FROM cases").fetchone()[0],
        "cases_with_violations": con.execute(
            "SELECT COUNT(*) FROM cases WHERE violation_count > 0"
        ).fetchone()[0],
        "total_back_wages": int(con.execute(
            "SELECT ROUND(SUM(back_wages), 0) FROM cases"
        ).fetchone()[0]),
        "total_employees_affected": con.execute(
            "SELECT SUM(employees_violated) FROM cases"
        ).fetchone()[0],
        "total_civil_penalties": int(con.execute(
            "SELECT ROUND(SUM(civil_penalties), 0) FROM cases"
        ).fetchone()[0]),
        "states": {},
        "top_industries": [],
        "by_year": {},
        "violation_types": {},
    }

    for row in con.execute("SELECT * FROM state_summary ORDER BY total_cases DESC").fetchall():
        stats["states"][row[0]] = {
            "c": row[1], "v": row[2], "bw": row[3], "p": row[4], "e": row[5]
        }

    for r in con.execute("""
        SELECT industry, COUNT(*), ROUND(SUM(back_wages), 0)::BIGINT, SUM(employees_violated)
        FROM cases WHERE industry IS NOT NULL AND TRIM(industry) != ''
        GROUP BY industry ORDER BY COUNT(*) DESC LIMIT 25
    """).fetchall():
        stats["top_industries"].append({"n": r[0], "c": r[1], "bw": r[2], "e": r[3]})

    for yr, cnt, bw in con.execute("""
        SELECT EXTRACT(YEAR FROM findings_end)::INT, COUNT(*),
               ROUND(SUM(back_wages), 0)::BIGINT
        FROM cases WHERE findings_end IS NOT NULL
          AND EXTRACT(YEAR FROM findings_end) BETWEEN 2005 AND 2026
        GROUP BY 1 ORDER BY 1
    """).fetchall():
        stats["by_year"][str(yr)] = {"c": cnt, "bw": bw}

    for col, label in [
        ("has_flsa", "FLSA"), ("has_child_labor", "Child Labor"),
        ("has_fmla", "FMLA"), ("has_h1b", "H-1B"), ("has_h2a", "H-2A"),
        ("has_h2b", "H-2B"), ("has_mspa", "MSPA"), ("has_dbra", "Davis-Bacon"),
        ("has_sca", "SCA"), ("has_osha", "OSHA"), ("has_eppa", "EPPA"),
        ("has_ccpa", "CCPA"), ("has_cwhssa", "CWHSSA")
    ]:
        cnt = con.execute(f"SELECT COUNT(*) FROM cases WHERE {col} = true").fetchone()[0]
        if cnt > 0:
            stats["violation_types"][label] = cnt

    stats_path = OUTPUT_DIR / "stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, separators=(",", ":"))
    print(f"  Stats: {stats_path.stat().st_size / 1024:.0f} KB")

    # -------------------------------------------------------
    # 4. Metadata
    # -------------------------------------------------------
    date_range = con.execute("""
        SELECT MIN(findings_start)::VARCHAR, MAX(findings_end)::VARCHAR
        FROM cases WHERE findings_start IS NOT NULL
    """).fetchone()

    meta = {
        "source": "U.S. Department of Labor, Wage and Hour Division",
        "source_url": "https://data.dol.gov/data-catalog/WHD/enforcement/WHD_enforcement.zip",
        "description": "All concluded WHD compliance actions since FY 2005",
        "date_range": {"start": date_range[0][:10], "end": date_range[1][:10]},
        "record_count": stats["total_cases"],
        "update_frequency": "Quarterly",
        "disclaimer": "This data reflects federal enforcement actions only. "
                      "It does not include state-level enforcement. "
                      "Absence of a record does not mean no violations occurred. "
                      "Not legal advice.",
        "state_sizes_kb": {st: round(sz) for st, sz in state_sizes.items()},
    }

    meta_path = OUTPUT_DIR / "meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # -------------------------------------------------------
    # Summary
    # -------------------------------------------------------
    print("\n=== OUTPUT SUMMARY ===")
    print(f"  State JSONs: {len(state_sizes)} files, {total_size_mb:.1f} MB")
    print(f"  Search chunks: {len(chunks)} files, {total_search_gz / (1024*1024):.1f} MB gzipped")
    print(f"  Stats: {stats_path.stat().st_size / 1024:.0f} KB")
    print("  Initial page load: stats.json (~6KB) only")
    print("  First search: one chunk (~50-200KB gzipped)")

    con.close()


if __name__ == "__main__":
    build_index()

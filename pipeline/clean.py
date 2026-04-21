"""
Clean and normalize WHD enforcement data.

Input: Raw CSV chunks from DOL bulk download
Output: DuckDB database with cleaned, normalized records

Cleaning steps:
1. Combine all CSV chunks into single table
2. Normalize employer names (trim, case-normalize for matching)
3. Clean dates (filter impossible values)
4. Derive violation type flags for easier filtering
5. Compute per-employer aggregates
"""

import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
DB_PATH = PROJECT_ROOT / "data" / "whd.duckdb"


# NAICS prefix to human-readable industry category
NAICS_CATEGORIES = {
    "11": "Agriculture",
    "21": "Mining",
    "22": "Utilities",
    "23": "Construction",
    "31": "Manufacturing",
    "32": "Manufacturing",
    "33": "Manufacturing",
    "42": "Wholesale Trade",
    "44": "Retail Trade",
    "45": "Retail Trade",
    "48": "Transportation",
    "49": "Transportation",
    "51": "Information",
    "52": "Finance & Insurance",
    "53": "Real Estate",
    "54": "Professional Services",
    "55": "Management",
    "56": "Administrative Services",
    "61": "Education",
    "62": "Health Care",
    "71": "Arts & Entertainment",
    "72": "Accommodation & Food Services",
    "81": "Other Services",
    "92": "Public Administration",
}

# Violation type codes and their human-readable labels
VIOLATION_TYPES = {
    "FLSA": "Fair Labor Standards Act",
    "FLSA_CL": "FLSA Child Labor",
    "FMLA": "Family and Medical Leave Act",
    "H1B": "H-1B Visa Workers",
    "H2A": "H-2A Agricultural Workers",
    "H2B": "H-2B Non-Agricultural Workers",
    "MSPA": "Migrant and Seasonal Worker Protection",
    "DBRA": "Davis-Bacon (Prevailing Wage)",
    "SCA": "Service Contract Act",
    "OSHA": "OSHA Whistleblower",
    "EPPA": "Employee Polygraph Protection",
    "CCPA": "Consumer Credit Protection",
    "CWHSSA": "Contract Work Hours Safety",
}


def clean_data():
    """Process raw CSVs into clean DuckDB database."""
    csv_pattern = str(RAW_DIR / "LOAD*.csv")

    if DB_PATH.exists():
        DB_PATH.unlink()

    con = duckdb.connect(str(DB_PATH))

    print("Loading raw CSVs...")
    con.execute(f"""
        CREATE TABLE raw AS
        SELECT * FROM read_csv_auto('{csv_pattern}',
            header=true,
            union_by_name=true,
            ignore_errors=true
        )
    """)
    total = con.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
    print(f"  Loaded {total:,} raw records")

    print("Cleaning and normalizing...")
    con.execute("""
        CREATE TABLE cases AS
        SELECT
            CAST(CASE_ID AS INTEGER) as case_id,

            -- Employer names
            TRIM(TRADE_NM) as trade_name,
            TRIM(LEGAL_NAME) as legal_name,
            -- Normalized name for matching (uppercase, stripped of common suffixes)
            UPPER(TRIM(
                REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        REGEXP_REPLACE(
                            COALESCE(NULLIF(TRIM(LEGAL_NAME), ''), TRIM(TRADE_NM)),
                            ',?\\s*(INC\\.?|LLC\\.?|LTD\\.?|CORP\\.?|CO\\.?|L\\.?L\\.?C\\.?|L\\.?P\\.?)$', '', 'i'
                        ),
                        ',?\\s*(D/?B/?A|DBA)\\s+.*$', '', 'i'
                    ),
                    '\\s+', ' '
                )
            )) as name_normalized,

            -- Location
            TRIM(STREET_ADDR_1_TXT) as address,
            TRIM(CTY_NM) as city,
            TRIM(ST_CD) as state,
            LEFT(TRIM(ZIP_CD), 5) as zip_code,

            -- Industry
            TRIM(NAIC_CD) as naics_code,
            TRIM(NAICS_CODE_DESCRIPTION) as industry,

            -- Dates (clean impossible values)
            CASE
                WHEN TRY_CAST(FINDINGS_START_DATE AS TIMESTAMP) IS NOT NULL
                     AND EXTRACT(YEAR FROM TRY_CAST(FINDINGS_START_DATE AS TIMESTAMP)) BETWEEN 1990 AND 2027
                THEN TRY_CAST(FINDINGS_START_DATE AS TIMESTAMP)
            END as findings_start,
            CASE
                WHEN TRY_CAST(FINDINGS_END_DATE AS TIMESTAMP) IS NOT NULL
                     AND EXTRACT(YEAR FROM TRY_CAST(FINDINGS_END_DATE AS TIMESTAMP)) BETWEEN 1990 AND 2027
                THEN TRY_CAST(FINDINGS_END_DATE AS TIMESTAMP)
            END as findings_end,

            -- Summary violations
            COALESCE(TRY_CAST(CASE_VIOLTN_CNT AS INTEGER), 0) as violation_count,
            COALESCE(TRY_CAST(EE_VIOLTD_CNT AS INTEGER), 0) as employees_violated,
            COALESCE(TRY_CAST(BW_ATP_AMT AS DOUBLE), 0) as back_wages,
            COALESCE(TRY_CAST(EE_ATP_CNT AS INTEGER), 0) as employees_owed_back_wages,
            COALESCE(TRY_CAST(CMP_ASSD AS DOUBLE), 0) as civil_penalties,

            -- Per-act violation flags (boolean: did this case have this type?)
            COALESCE(TRY_CAST(FLSA_VIOLTN_CNT AS INTEGER), 0) > 0 as has_flsa,
            COALESCE(TRY_CAST(FLSA_CL_VIOLTN_CNT AS INTEGER), 0) > 0 as has_child_labor,
            COALESCE(TRY_CAST(FMLA_VIOLTN_CNT AS INTEGER), 0) > 0 as has_fmla,
            COALESCE(TRY_CAST(H1B_VIOLTN_CNT AS INTEGER), 0) > 0 as has_h1b,
            COALESCE(TRY_CAST(H2A_VIOLTN_CNT AS INTEGER), 0) > 0 as has_h2a,
            COALESCE(TRY_CAST(H2B_VIOLTN_CNT AS INTEGER), 0) > 0 as has_h2b,
            COALESCE(TRY_CAST(MSPA_VIOLTN_CNT AS INTEGER), 0) > 0 as has_mspa,
            COALESCE(TRY_CAST(DBRA_VIOLTN_CNT AS INTEGER), 0) > 0 as has_dbra,
            COALESCE(TRY_CAST(SCA_VIOLTN_CNT AS INTEGER), 0) > 0 as has_sca,
            COALESCE(TRY_CAST(OSHA_VIOLTN_CNT AS INTEGER), 0) > 0 as has_osha,
            COALESCE(TRY_CAST(EPPA_VIOLTN_CNT AS INTEGER), 0) > 0 as has_eppa,
            COALESCE(TRY_CAST(CCPA_VIOLTN_CNT AS INTEGER), 0) > 0 as has_ccpa,
            COALESCE(TRY_CAST(CWHSSA_VIOLTN_CNT AS INTEGER), 0) > 0 as has_cwhssa,

            -- FLSA detail
            COALESCE(TRY_CAST(FLSA_MW_BW_ATP_AMT AS DOUBLE), 0) as flsa_min_wage_bw,
            COALESCE(TRY_CAST(FLSA_OT_BW_ATP_AMT AS DOUBLE), 0) as flsa_overtime_bw,
            TRIM(FLSA_REPEAT_VIOLATOR) as flsa_repeat_violator

        FROM raw
    """)

    cleaned = con.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    print(f"  {cleaned:,} cases in clean table")

    # Clean up bad dates
    bad_dates = con.execute("""
        SELECT COUNT(*) FROM cases
        WHERE findings_start IS NULL AND findings_end IS NULL
    """).fetchone()[0]
    print(f"  {bad_dates:,} cases with no valid dates")

    # Derive violation type string for each case
    print("Deriving violation type labels...")
    con.execute("""
        ALTER TABLE cases ADD COLUMN violation_types VARCHAR;
    """)
    con.execute("""
        UPDATE cases SET violation_types =
            CASE WHEN violation_count = 0 THEN 'No violations found'
            ELSE TRIM(BOTH ', ' FROM
                CASE WHEN has_flsa THEN 'FLSA, ' ELSE '' END ||
                CASE WHEN has_child_labor THEN 'Child Labor, ' ELSE '' END ||
                CASE WHEN has_fmla THEN 'FMLA, ' ELSE '' END ||
                CASE WHEN has_h1b THEN 'H-1B, ' ELSE '' END ||
                CASE WHEN has_h2a THEN 'H-2A, ' ELSE '' END ||
                CASE WHEN has_h2b THEN 'H-2B, ' ELSE '' END ||
                CASE WHEN has_mspa THEN 'MSPA, ' ELSE '' END ||
                CASE WHEN has_dbra THEN 'Davis-Bacon, ' ELSE '' END ||
                CASE WHEN has_sca THEN 'SCA, ' ELSE '' END ||
                CASE WHEN has_osha THEN 'OSHA, ' ELSE '' END ||
                CASE WHEN has_eppa THEN 'EPPA, ' ELSE '' END ||
                CASE WHEN has_ccpa THEN 'CCPA, ' ELSE '' END ||
                CASE WHEN has_cwhssa THEN 'CWHSSA, ' ELSE '' END
            )
            END
    """)

    # Add industry category
    print("Adding industry categories...")
    con.execute("ALTER TABLE cases ADD COLUMN industry_category VARCHAR;")
    # Use NAICS 2-digit prefix
    for prefix, category in NAICS_CATEGORIES.items():
        con.execute(f"""
            UPDATE cases SET industry_category = '{category}'
            WHERE industry_category IS NULL AND LEFT(naics_code, 2) = '{prefix}'
        """)
    con.execute("""
        UPDATE cases SET industry_category = 'Other'
        WHERE industry_category IS NULL
    """)

    # Drop raw table
    con.execute("DROP TABLE raw")

    # Create indexes for query performance
    print("Creating summary stats...")

    # Per-state summary
    con.execute("""
        CREATE TABLE state_summary AS
        SELECT
            state,
            COUNT(*) as total_cases,
            COUNT(*) FILTER (WHERE violation_count > 0) as cases_with_violations,
            ROUND(SUM(back_wages), 0)::BIGINT as total_back_wages,
            ROUND(SUM(civil_penalties), 0)::BIGINT as total_penalties,
            SUM(employees_violated) as total_employees_violated
        FROM cases
        WHERE state IS NOT NULL AND TRIM(state) != ''
        GROUP BY state
        ORDER BY total_cases DESC
    """)

    # Final stats
    stats = con.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE violation_count > 0) as with_violations,
            COUNT(DISTINCT state) as states,
            ROUND(SUM(back_wages), 0) as total_bw,
            MIN(findings_start)::VARCHAR as earliest,
            MAX(findings_end)::VARCHAR as latest
        FROM cases
    """).fetchone()

    print("\n=== CLEAN DATABASE ===")
    print(f"  Total cases: {stats[0]:,}")
    print(f"  With violations: {stats[1]:,}")
    print(f"  States: {stats[2]}")
    print(f"  Total back wages: ${stats[3]:,.0f}")
    print(f"  Date range: {stats[4]} to {stats[5]}")
    print(f"  Database: {DB_PATH} ({DB_PATH.stat().st_size / (1024*1024):.1f} MB)")

    con.close()
    return DB_PATH


if __name__ == "__main__":
    clean_data()

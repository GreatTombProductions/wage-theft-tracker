# Wage Theft Tracker

Search U.S. Department of Labor wage enforcement records by employer name. See federal investigation history instantly. View enforcement density across 3,100+ counties on an interactive map. Compare any employer against their industry.

**365,390 cases** · **$4.7 billion in back wages** · **5.4 million employees affected** · FY 2005 to present

## Why This Exists

The federal government investigates wage theft and publishes the results. But the data sits in a [bulk download](https://data.dol.gov/data-catalog/WHD/enforcement/) that nobody can practically search. Employers know their enforcement history. Workers don't.

This tool inverts the access pattern: type an employer name, see their federal enforcement record. Or switch to the map view to see where enforcement concentrates — by case count or back wages, county by county.

## Use It

Open `index.html` in a browser, or serve locally:

```bash
cd wage-theft-tracker
python3 -m http.server 8080
# Open http://localhost:8080
```

<!-- Or visit the live site on GitHub Pages once deployed -->

### How It Works

**Search view:**
1. Type an employer name in the search box
2. Results show case count, back wages, and states
3. Click an employer to see individual case details
4. Filter by state or violation type
5. Industry comparison shows how the employer's record compares to others in their industry

**Map view:**
1. Click the "Map" tab to see enforcement density by county
2. Toggle between case count and back wages
3. Hover over any county to see details

The initial page load is ~6KB. Search chunks (~50–300KB each) and state data (~200KB–13MB) load on demand as you search and click. The map loads county boundaries (~700KB from CDN) and county stats (~300KB) on first view. Industry comparison data (~314KB) loads when you view an employer's details.

## Reproduce the Data

The pipeline regenerates everything from public sources:

```bash
pip install duckdb pandas pyarrow
python3 pipeline/run.py
```

This downloads the WHD enforcement ZIP (~189MB), normalizes employer names, and generates the search index and per-state JSON files.

### Data Source

[U.S. Department of Labor, Wage and Hour Division](https://data.dol.gov/data-catalog/WHD/enforcement/) — all concluded compliance actions since FY 2005. Updated quarterly. Free, no authentication required.

### Pipeline Architecture

```
pipeline/
  acquire.py    — Download WHD bulk data from DOL
  clean.py      — Normalize names, clean dates, derive violation types, categorize industries
  index.py      — Build chunked search index + per-state JSONs + summary stats
  geo.py        — Aggregate cases by county (ZIP-to-county crosswalk) for map choropleth
  industry.py   — Compute industry comparison percentiles (national + per-state)
  run.py        — Full pipeline orchestrator
```

### What's In Each Record

- Employer legal and trade names
- Location (city, state, zip)
- Industry (NAICS-derived)
- Violation types (FLSA minimum wage/overtime, child labor, FMLA, H-1B/H-2A/H-2B, MSPA, Davis-Bacon, SCA)
- Back wages found
- Civil penalties assessed
- Number of employees affected
- Investigation dates

## Important Disclaimers

- **Federal enforcement actions only.** Does not include state-level investigations.
- **Not legal advice.** This is raw government data, not a legal assessment.
- **Absence of a record does not mean no violations occurred.** Only investigated cases appear.
- **Employer name matching is approximate.** Government data contains variant spellings, DBA names, and entity changes over time.

## Technical Notes

- Vanilla JavaScript — no framework, no build step
- Chunked search index: 28 files by first letter (A–Z, 0–9, other). Only the relevant chunk loads when you type.
- Per-state JSON files loaded on demand when you click into an employer's cases
- Industry comparison: pre-computed percentile distributions (21 categories nationally + 777 state-level groups). Shows where an employer ranks for back wages and case count vs. their industry peers
- County choropleth map via MapLibre GL JS and US Atlas TopoJSON (loaded from CDN)
- Mobile-first responsive design — meant to work in a parking lot on a phone
- Dark theme

## License

MIT

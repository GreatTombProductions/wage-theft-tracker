/**
 * Wage Theft Tracker — Client-side search and display
 *
 * Architecture:
 *   - stats.json loaded on page init (~6KB)
 *   - search/{letter}.json loaded on demand when user types (~50-200KB each)
 *   - states/{ST}.json loaded on demand when user clicks a result (~200KB-2MB)
 *   - All data cached in memory after first fetch
 */

(function () {
    "use strict";

    // --- State ---
    const cache = {
        search: {},    // letter -> employer array
        states: {},    // state code -> case array
        stats: null,
        industry: null,  // industry percentile data
    };

    let currentResults = [];
    let debounceTimer = null;

    // --- DOM refs ---
    const searchInput = document.getElementById("search-input");
    const searchStatus = document.getElementById("search-status");
    const filtersEl = document.getElementById("filters");
    const filterState = document.getElementById("filter-state");
    const filterViolation = document.getElementById("filter-violation");
    const resultsEl = document.getElementById("results");
    const caseDetailEl = document.getElementById("case-detail");
    const backBtn = document.getElementById("back-btn");
    const employerHeader = document.getElementById("employer-header");
    const caseList = document.getElementById("case-list");
    const statsPanel = document.getElementById("stats-panel");
    const statsGrid = document.getElementById("stats-grid");
    const topIndustriesEl = document.getElementById("top-industries");

    // --- Data fetching ---
    const BASE = new URL("data/", window.location.href).href;

    async function fetchJSON(path) {
        const url = new URL(path, BASE).href;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`Failed to fetch ${path}: ${resp.status}`);
        return resp.json();
    }

    async function loadSearchChunk(letter) {
        const key = letter.toUpperCase();
        if (cache.search[key]) return cache.search[key];
        const data = await fetchJSON(`search/${key}.json`);
        cache.search[key] = data;
        return data;
    }

    async function loadState(stateCode) {
        if (cache.states[stateCode]) return cache.states[stateCode];
        const data = await fetchJSON(`states/${stateCode}.json`);
        cache.states[stateCode] = data;
        return data;
    }

    async function loadIndustry() {
        if (cache.industry) return cache.industry;
        const data = await fetchJSON("industry.json");
        cache.industry = data;
        return data;
    }

    // --- Formatting ---
    function fmtMoney(n) {
        if (!n || n === 0) return "$0";
        if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
        if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K";
        return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 0 });
    }

    function fmtNumber(n) {
        if (!n) return "0";
        return n.toLocaleString("en-US");
    }

    function fmtDate(d) {
        if (!d) return "";
        // d is YYYY-MM-DD
        const parts = d.split("-");
        if (parts.length >= 3) {
            return parts[1] + "/" + parts[2] + "/" + parts[0];
        }
        return d;
    }

    // --- Industry Comparison ---

    /**
     * Estimate what percentile a value falls at, given pre-computed breakpoints.
     * Returns 0-100 (integer). Uses linear interpolation between breakpoints.
     */
    function estimatePercentile(value, dist) {
        if (!dist || !value) return 0;

        const breakpoints = [
            { p: 0, v: 0 },
            { p: 25, v: dist.p25 },
            { p: 50, v: dist.p50 },
            { p: 75, v: dist.p75 },
            { p: 90, v: dist.p90 },
            { p: 95, v: dist.p95 },
            { p: 99, v: dist.p99 },
            { p: 100, v: dist.max },
        ];

        if (value <= 0) return 0;
        if (value >= dist.max) return 99;

        // Find surrounding breakpoints and interpolate
        for (let i = 1; i < breakpoints.length; i++) {
            if (value <= breakpoints[i].v) {
                const lo = breakpoints[i - 1];
                const hi = breakpoints[i];
                if (hi.v === lo.v) return lo.p;
                const frac = (value - lo.v) / (hi.v - lo.v);
                return Math.round(lo.p + frac * (hi.p - lo.p));
            }
        }
        return 99;
    }

    /**
     * Build industry comparison HTML for the employer detail view.
     * Returns HTML string, or empty string if no comparison data available.
     */
    function buildIndustryComparison(cases, industryData) {
        if (!industryData || !cases.length) return "";

        // Determine primary industry category from cases
        const catCounts = {};
        for (const c of cases) {
            const cat = c.ic || "Other";
            catCounts[cat] = (catCounts[cat] || 0) + 1;
        }
        const primaryCategory = Object.entries(catCounts)
            .sort((a, b) => b[1] - a[1])[0][0];

        // Compute employer aggregates
        const totalBW = cases.reduce((s, c) => s + (c.bw || 0), 0);
        const totalCases = cases.length;
        const totalEE = cases.reduce((s, c) => s + (c.ev || 0), 0);

        // Try state-level first (if all cases in one state), fall back to national
        const statesSet = new Set(cases.map(c => c._state));
        let dist = null;
        let scope = "nationally";

        if (statesSet.size === 1) {
            const st = [...statesSet][0];
            const stateKey = st + ":" + primaryCategory;
            if (industryData.state && industryData.state[stateKey]) {
                dist = industryData.state[stateKey];
                scope = "in " + st;
            }
        }

        if (!dist) {
            dist = industryData.national[primaryCategory];
            scope = "nationally";
        }

        if (!dist) return "";

        const bwPct = estimatePercentile(totalBW, dist.bw);
        const casesPct = estimatePercentile(totalCases, dist.cases);

        // Only show comparison if there's something meaningful to compare
        if (totalBW === 0 && totalCases <= 1) return "";

        function pctBar(pct, label, value) {
            const color = pct >= 90 ? "var(--danger)" :
                          pct >= 75 ? "var(--warning)" :
                          pct >= 50 ? "var(--accent)" : "var(--text-dim)";
            return `
                <div class="ic-metric">
                    <div class="ic-metric-header">
                        <span class="ic-metric-label">${label}</span>
                        <span class="ic-metric-value">${value}</span>
                    </div>
                    <div class="ic-bar-track">
                        <div class="ic-bar-fill" style="width:${Math.max(pct, 2)}%;background:${color}"></div>
                        <div class="ic-bar-marker" style="left:${pct}%"></div>
                    </div>
                    <div class="ic-pct-label">Higher than <strong>${pct}%</strong> of ${escapeHtml(primaryCategory.toLowerCase())} employers investigated ${scope}</div>
                </div>
            `;
        }

        return `
            <div class="industry-comparison">
                <h3 class="ic-title">Industry Comparison</h3>
                <div class="ic-context">vs. ${fmtNumber(dist.n)} ${escapeHtml(primaryCategory.toLowerCase())} employers investigated ${scope}</div>
                ${pctBar(bwPct, "Back Wages", fmtMoney(totalBW))}
                ${pctBar(casesPct, "Cases", totalCases.toString())}
            </div>
        `;
    }

    // --- Search ---
    function getSearchKey(query) {
        const first = query.trim().charAt(0).toUpperCase();
        if (first >= "A" && first <= "Z") return first;
        if (first >= "0" && first <= "9") return "0";
        return "_";
    }

    function matchScore(name, query) {
        // Simple scoring: exact prefix match > word start match > substring match
        const nameLower = name.toLowerCase();
        const queryLower = query.toLowerCase();

        if (nameLower === queryLower) return 1000;
        if (nameLower.startsWith(queryLower)) return 100;

        // Check word starts
        const words = nameLower.split(/\s+/);
        const queryWords = queryLower.split(/\s+/);
        let wordMatches = 0;
        for (const qw of queryWords) {
            for (const w of words) {
                if (w.startsWith(qw)) { wordMatches++; break; }
            }
        }
        if (wordMatches === queryWords.length) return 50 + wordMatches;
        if (wordMatches > 0) return 10 + wordMatches;

        // Substring
        if (nameLower.includes(queryLower)) return 5;

        return 0;
    }

    async function doSearch(query) {
        query = query.trim();
        if (query.length < 2) {
            showStats();
            return;
        }

        searchStatus.textContent = "Searching...";
        statsPanel.classList.add("hidden");
        caseDetailEl.classList.add("hidden");
        resultsEl.innerHTML = "";

        try {
            // Load the right search chunk
            const key = getSearchKey(query);
            const chunk = await loadSearchChunk(key);

            // Filter and score matches
            // Each entry: [display_name, states_string, case_count, back_wages?]
            const scored = [];
            const queryLower = query.toLowerCase();

            for (const entry of chunk) {
                const name = entry[0];
                const score = matchScore(name, query);
                if (score > 0) {
                    scored.push({ entry, score });
                }
            }

            // Sort by score desc, then by case count desc
            scored.sort((a, b) => {
                if (b.score !== a.score) return b.score - a.score;
                return (b.entry[2] || 0) - (a.entry[2] || 0);
            });

            currentResults = scored.slice(0, 100);

            // Render results
            renderResults(query);

        } catch (err) {
            searchStatus.textContent = "Search error. Try again.";
            console.error("Search error:", err);
        }
    }

    function renderResults(query) {
        const count = currentResults.length;

        if (count === 0) {
            searchStatus.textContent = "No employers found matching that name.";
            resultsEl.innerHTML = "";
            filtersEl.classList.add("hidden");
            return;
        }

        searchStatus.textContent = `${count} employer${count !== 1 ? "s" : ""} found`;
        filtersEl.classList.add("hidden"); // Filters apply to case detail, not search results

        resultsEl.innerHTML = "";

        for (const { entry } of currentResults) {
            const [name, statesStr, cases, bw] = entry;
            const states = statesStr.split(",");

            const card = document.createElement("div");
            card.className = "result-card";
            card.setAttribute("role", "button");
            card.setAttribute("tabindex", "0");

            // Highlight matching text
            const displayName = highlightMatch(name, query);

            card.innerHTML = `
                <div class="result-name">${displayName}</div>
                <div class="result-meta">
                    <span class="cases">${cases} case${cases !== 1 ? "s" : ""}</span>
                    ${bw ? `<span class="wages">${fmtMoney(bw)} in back wages</span>` : ""}
                    <span class="states">${states.length > 3 ? states.slice(0, 3).join(", ") + " +" + (states.length - 3) + " more" : states.join(", ")}</span>
                </div>
            `;

            card.addEventListener("click", () => showEmployerDetail(name, statesStr, name));
            card.addEventListener("keydown", (e) => {
                if (e.key === "Enter") showEmployerDetail(name, statesStr, name);
            });

            resultsEl.appendChild(card);
        }
    }

    function highlightMatch(text, query) {
        const idx = text.toLowerCase().indexOf(query.toLowerCase());
        if (idx === -1) return escapeHtml(text);
        const before = escapeHtml(text.slice(0, idx));
        const match = escapeHtml(text.slice(idx, idx + query.length));
        const after = escapeHtml(text.slice(idx + query.length));
        return `${before}<mark>${match}</mark>` + after;
    }

    function escapeHtml(s) {
        const div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }

    // --- Employer Detail ---
    async function showEmployerDetail(name, statesStr, normName) {
        const states = statesStr.split(",");
        resultsEl.innerHTML = "";
        statsPanel.classList.add("hidden");
        filtersEl.classList.remove("hidden");
        caseDetailEl.classList.remove("hidden");

        // Populate state filter
        filterState.innerHTML = '<option value="">All states</option>';
        for (const st of states) {
            const opt = document.createElement("option");
            opt.value = st;
            opt.textContent = st;
            filterState.appendChild(opt);
        }

        employerHeader.innerHTML = `<h2>${escapeHtml(name)}</h2><div class="loading">Loading cases</div>`;
        caseList.innerHTML = "";

        // Build normalized name for matching
        // Strip common suffixes to match the pipeline's normalization
        const nameUpper = name.toUpperCase().trim();
        const normTarget = (normName || nameUpper)
            .replace(/,?\s*(INC\.?|LLC\.?|LTD\.?|CORP\.?|CO\.?|L\.?L\.?C\.?|L\.?P\.?)$/i, "")
            .replace(/,?\s*(D\/?B\/?A|DBA)\s+.*$/i, "")
            .replace(/\s+/g, " ")
            .trim()
            .toUpperCase();

        const allCases = [];

        try {
            // Load state data and industry data in parallel
            const statePromises = states.map(st => loadState(st));
            const industryPromise = loadIndustry().catch(() => null);
            const [stateDataArrays, industryData] = await Promise.all([
                Promise.all(statePromises),
                industryPromise,
            ]);

            for (let i = 0; i < states.length; i++) {
                const st = states[i];
                const stateData = stateDataArrays[i];

                // Match cases using normalized name (most reliable)
                // Then fall back to display name matching
                for (const c of stateData) {
                    const cNorm = (c.nn || "").toUpperCase();
                    const cTrade = (c.tn || "").toUpperCase();
                    const cLegal = (c.ln || "").toUpperCase();

                    if (cNorm === normTarget ||
                        cTrade === nameUpper ||
                        cLegal === nameUpper ||
                        (normTarget.length > 5 && (cNorm.includes(normTarget) || normTarget.includes(cNorm)))) {
                        allCases.push({ ...c, _state: st });
                    }
                }
            }

            // Sort by back wages desc
            allCases.sort((a, b) => (b.bw || 0) - (a.bw || 0));

            renderEmployerDetail(name, allCases, undefined, undefined, industryData);

        } catch (err) {
            employerHeader.innerHTML = `<h2>${escapeHtml(name)}</h2><p style="color:var(--danger)">Error loading case data.</p>`;
            console.error("Detail error:", err);
        }
    }

    function renderEmployerDetail(name, allCases, filterSt, filterViol, industryData) {
        // Apply filters
        let cases = allCases;
        if (filterSt) {
            cases = cases.filter(c => c._state === filterSt);
        }
        if (filterViol) {
            cases = cases.filter(c => c.vt && c.vt.includes(filterViol));
        }

        // Compute summary
        const totalBW = cases.reduce((s, c) => s + (c.bw || 0), 0);
        const totalPenalties = cases.reduce((s, c) => s + (c.cp || 0), 0);
        const totalEmployees = cases.reduce((s, c) => s + (c.ev || 0), 0);
        const withViolations = cases.filter(c => c.vc > 0).length;
        const statesSet = new Set(cases.map(c => c._state));

        // Industry comparison always uses unfiltered cases (employer-level comparison)
        const indData = industryData || caseDetailEl._industryData || null;
        const comparisonHtml = buildIndustryComparison(allCases, indData);

        employerHeader.innerHTML = `
            <h2>${escapeHtml(name)}</h2>
            <div class="employer-summary">
                <div class="summary-stat">
                    <div class="value">${cases.length}</div>
                    <div class="label">Cases</div>
                </div>
                <div class="summary-stat">
                    <div class="value money">${fmtMoney(totalBW)}</div>
                    <div class="label">Back Wages</div>
                </div>
                <div class="summary-stat">
                    <div class="value danger">${fmtNumber(totalEmployees)}</div>
                    <div class="label">Employees Affected</div>
                </div>
                <div class="summary-stat">
                    <div class="value">${statesSet.size}</div>
                    <div class="label">States</div>
                </div>
            </div>
            ${comparisonHtml}
        `;

        // Render cases
        caseList.innerHTML = "";
        for (const c of cases) {
            const card = document.createElement("div");
            card.className = "case-card";

            const location = [c.ct, c._state, c.zp].filter(Boolean).join(", ");
            const dateRange = [fmtDate(c.fs), fmtDate(c.fe)].filter(Boolean).join(" - ");

            let violationHtml;
            if (c.vc > 0) {
                violationHtml = `<div class="case-violations">${escapeHtml(c.vt || "Violations found")}</div>`;
            } else {
                violationHtml = `<div class="no-violations">No violations found</div>`;
            }

            const numbers = [];
            if (c.bw) numbers.push(`<span class="bw">${fmtMoney(c.bw)} back wages</span>`);
            if (c.cp) numbers.push(`<span class="penalty">${fmtMoney(c.cp)} penalties</span>`);
            if (c.ev) numbers.push(`<span>${c.ev} employees affected</span>`);

            card.innerHTML = `
                <div class="case-header">
                    <div class="case-location">${escapeHtml(location)}</div>
                    <div class="case-date">${escapeHtml(dateRange)}</div>
                </div>
                ${violationHtml}
                ${numbers.length ? `<div class="case-numbers">${numbers.join("")}</div>` : ""}
                ${c.in ? `<div class="case-industry">${escapeHtml(c.in)}</div>` : ""}
            `;

            caseList.appendChild(card);
        }

        // Stash for filter re-rendering
        caseDetailEl._allCases = allCases;
        caseDetailEl._name = name;
        if (indData) caseDetailEl._industryData = indData;
    }

    // --- Stats ---
    function showStats() {
        resultsEl.innerHTML = "";
        caseDetailEl.classList.add("hidden");
        filtersEl.classList.add("hidden");
        searchStatus.textContent = "";

        if (!cache.stats) {
            statsPanel.classList.add("hidden");
            return;
        }

        statsPanel.classList.remove("hidden");

        const s = cache.stats;
        statsGrid.innerHTML = `
            <div class="stat-card">
                <div class="stat-value">${fmtNumber(s.total_cases)}</div>
                <div class="stat-label">Federal Investigations</div>
            </div>
            <div class="stat-card">
                <div class="stat-value money">${fmtMoney(s.total_back_wages)}</div>
                <div class="stat-label">Back Wages Found</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${fmtNumber(s.cases_with_violations)}</div>
                <div class="stat-label">With Violations</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${fmtNumber(s.total_employees_affected)}</div>
                <div class="stat-label">Employees Affected</div>
            </div>
        `;

        // Top industries
        if (s.top_industries && s.top_industries.length) {
            let html = '<h3>Top Industries by Case Count</h3>';
            for (const ind of s.top_industries.slice(0, 10)) {
                html += `
                    <div class="industry-row">
                        <span class="ind-name">${escapeHtml(ind.n)}</span>
                        <span class="ind-cases">${fmtNumber(ind.c)}</span>
                        <span class="ind-wages">${fmtMoney(ind.bw)}</span>
                    </div>
                `;
            }
            topIndustriesEl.innerHTML = html;
        }
    }

    // --- Event handlers ---
    searchInput.addEventListener("input", () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            doSearch(searchInput.value);
        }, 200);
    });

    backBtn.addEventListener("click", () => {
        caseDetailEl.classList.add("hidden");
        filtersEl.classList.add("hidden");
        // Re-render search results
        if (searchInput.value.trim().length >= 2) {
            renderResults(searchInput.value);
        } else {
            showStats();
        }
    });

    filterState.addEventListener("change", () => {
        if (caseDetailEl._allCases) {
            renderEmployerDetail(
                caseDetailEl._name,
                caseDetailEl._allCases,
                filterState.value,
                filterViolation.value
            );
        }
    });

    filterViolation.addEventListener("change", () => {
        if (caseDetailEl._allCases) {
            renderEmployerDetail(
                caseDetailEl._name,
                caseDetailEl._allCases,
                filterState.value,
                filterViolation.value
            );
        }
    });

    // --- View toggle ---
    const viewTabs = document.querySelectorAll(".view-tab");
    const searchView = document.getElementById("search-view");
    const mapView = document.getElementById("map-view");

    viewTabs.forEach(tab => {
        tab.addEventListener("click", () => {
            const view = tab.dataset.view;
            viewTabs.forEach(t => t.classList.remove("active"));
            tab.classList.add("active");

            if (view === "search") {
                searchView.classList.remove("hidden");
                mapView.classList.add("hidden");
            } else if (view === "map") {
                searchView.classList.add("hidden");
                mapView.classList.remove("hidden");
                // Trigger map init on first switch
                if (window.initMap) window.initMap();
            }
        });
    });

    // --- Init ---
    async function init() {
        try {
            cache.stats = await fetchJSON("stats.json");
            showStats();
        } catch (err) {
            console.error("Failed to load stats:", err);
        }
    }

    init();
})();

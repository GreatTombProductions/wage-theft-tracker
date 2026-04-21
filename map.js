/**
 * Wage Theft Tracker — County Choropleth Map (Phase 2)
 *
 * Renders enforcement density by county using:
 *   - MapLibre GL JS for rendering
 *   - US Atlas TopoJSON for county boundaries (~700KB from CDN)
 *   - counties.json for per-county enforcement data (~300KB)
 *
 * Two metrics: case count and back wages. Toggle via legend buttons.
 */

(function () {
    "use strict";

    const US_ATLAS_URL =
        "https://cdn.jsdelivr.net/npm/us-atlas@3/counties-10m.json";

    let map = null;
    let mapInitialized = false;
    let countyData = null;
    let currentMetric = "cases"; // "cases" or "wages"

    // Color scales — sequential blues for cases, sequential greens for wages
    const SCALES = {
        cases: {
            breaks: [0, 1, 10, 50, 100, 500, 1000, 5000],
            colors: [
                "#0a0a0f",  // 0 — background (no data)
                "#1a1a3e",  // 1+
                "#1e3a5f",  // 10+
                "#2166ac",  // 50+
                "#4393c3",  // 100+
                "#92c5de",  // 500+
                "#d1e5f0",  // 1000+
                "#f7f7f7",  // 5000+
            ],
            labels: ["0", "1", "10", "50", "100", "500", "1K", "5K+"],
            format: n => n >= 1000 ? (n / 1000).toFixed(1) + "K" : n.toLocaleString(),
        },
        wages: {
            breaks: [0, 1, 10000, 100000, 1000000, 10000000, 50000000, 200000000],
            colors: [
                "#0a0a0f",
                "#1a2e1a",
                "#1a5e2a",
                "#2ca02c",
                "#5cb85c",
                "#8cd98c",
                "#b8e6b8",
                "#e8f5e8",
            ],
            labels: ["$0", "$1", "$10K", "$100K", "$1M", "$10M", "$50M", "$200M+"],
            format: n => {
                if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
                if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
                if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K";
                return "$" + n.toLocaleString();
            },
        },
    };

    // --- Tooltip ---
    const tooltip = document.getElementById("map-tooltip");

    function showTooltip(e, props) {
        if (!props) {
            tooltip.style.display = "none";
            return;
        }
        const d = countyData[props.GEOID] || countyData[String(parseInt(props.GEOID))] || null;
        const name = d ? d.n : (props.NAME || "Unknown");
        const cases = d ? d.c : 0;
        const bw = d ? d.bw : 0;
        const employees = d ? d.e : 0;

        const scale = SCALES[currentMetric];

        tooltip.innerHTML = `
            <div class="tt-name">${escapeHtml(name)}</div>
            <div class="tt-stat">${scale.format(currentMetric === "cases" ? cases : bw)}</div>
            <div class="tt-detail">
                ${cases.toLocaleString()} cases &middot;
                $${bw.toLocaleString()} back wages &middot;
                ${employees.toLocaleString()} employees
            </div>
        `;

        // Position near cursor
        const rect = document.getElementById("map-container").getBoundingClientRect();
        let x = e.point.x + 12;
        let y = e.point.y - 12;

        // Keep tooltip on screen
        const ttRect = tooltip.getBoundingClientRect();
        if (x + 250 > rect.width) x = e.point.x - 260;
        if (y < 0) y = e.point.y + 16;

        tooltip.style.left = x + "px";
        tooltip.style.top = y + "px";
        tooltip.style.display = "block";
    }

    function escapeHtml(s) {
        const div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }

    // --- Legend ---
    function renderLegend() {
        const scale = SCALES[currentMetric];
        const legendScale = document.getElementById("legend-scale");
        let html = '<div class="legend-bar">';
        for (let i = 0; i < scale.colors.length; i++) {
            html += `<div class="legend-swatch" style="background:${scale.colors[i]}">
                <span>${scale.labels[i]}</span>
            </div>`;
        }
        html += "</div>";
        legendScale.innerHTML = html;
    }

    // --- Build fill-color expression ---
    function buildFillColor() {
        const scale = SCALES[currentMetric];
        const prop = currentMetric === "cases" ? "cases" : "wages";

        // MapLibre step expression: ["step", input, defaultColor, stop1, color1, ...]
        const expr = ["step", ["coalesce", ["get", prop], 0]];
        for (let i = 0; i < scale.breaks.length; i++) {
            if (i === 0) {
                expr.push(scale.colors[i]); // default for values < first break
            } else {
                expr.push(scale.breaks[i]);
                expr.push(scale.colors[i]);
            }
        }
        return expr;
    }

    // --- Map init ---
    async function initMapInternal() {
        if (mapInitialized) {
            // Just resize in case the container changed
            if (map) map.resize();
            return;
        }
        mapInitialized = true;

        // Load county data and TopoJSON in parallel
        const [countyResp, topoResp] = await Promise.all([
            fetch(new URL("data/counties.json", window.location.href).href),
            fetch(US_ATLAS_URL),
        ]);

        if (!countyResp.ok || !topoResp.ok) {
            document.getElementById("map-container").innerHTML =
                '<div class="loading">Failed to load map data.</div>';
            return;
        }

        countyData = await countyResp.json();
        const topo = await topoResp.json();

        // Convert TopoJSON to GeoJSON
        const counties = topojson.feature(topo, topo.objects.counties);
        const states = topojson.mesh(topo, topo.objects.states, (a, b) => a !== b);

        // Inject enforcement data into county properties
        for (const feature of counties.features) {
            const fips = feature.id;
            // Try both padded and unpadded FIPS
            const d = countyData[fips] || countyData[String(parseInt(fips))] || null;
            feature.properties = feature.properties || {};
            feature.properties.GEOID = fips;
            if (d) {
                feature.properties.NAME = d.n;
                feature.properties.cases = d.c;
                feature.properties.wages = d.bw;
                feature.properties.employees = d.e;
                feature.properties.violations = d.v;
            } else {
                feature.properties.cases = 0;
                feature.properties.wages = 0;
                feature.properties.employees = 0;
                feature.properties.violations = 0;
            }
        }

        // Create map
        map = new maplibregl.Map({
            container: "map-container",
            style: {
                version: 8,
                sources: {},
                layers: [
                    {
                        id: "background",
                        type: "background",
                        paint: { "background-color": "#0a0a0f" },
                    },
                ],
            },
            center: [-98.5, 39.5],
            zoom: 3.5,
            minZoom: 2,
            maxZoom: 10,
        });

        map.on("load", () => {
            // Add county source
            map.addSource("counties", {
                type: "geojson",
                data: counties,
            });

            // Add state boundaries source
            map.addSource("state-borders", {
                type: "geojson",
                data: {
                    type: "Feature",
                    geometry: states,
                },
            });

            // County fill layer
            map.addLayer({
                id: "county-fill",
                type: "fill",
                source: "counties",
                paint: {
                    "fill-color": buildFillColor(),
                    "fill-opacity": 0.85,
                },
            });

            // County border (subtle)
            map.addLayer({
                id: "county-border",
                type: "line",
                source: "counties",
                paint: {
                    "line-color": "#1a1a2e",
                    "line-width": 0.3,
                },
            });

            // State borders (more visible)
            map.addLayer({
                id: "state-border",
                type: "line",
                source: "state-borders",
                paint: {
                    "line-color": "#3a3a5e",
                    "line-width": 1,
                },
            });

            // Hover highlight
            map.addLayer({
                id: "county-hover",
                type: "line",
                source: "counties",
                paint: {
                    "line-color": "#ffffff",
                    "line-width": 2,
                },
                filter: ["==", "GEOID", ""],
            });

            // Interaction: hover
            map.on("mousemove", "county-fill", (e) => {
                map.getCanvas().style.cursor = "pointer";
                const feature = e.features[0];
                if (feature) {
                    map.setFilter("county-hover", ["==", "GEOID", feature.properties.GEOID]);
                    showTooltip(e, feature.properties);
                }
            });

            map.on("mouseleave", "county-fill", () => {
                map.getCanvas().style.cursor = "";
                map.setFilter("county-hover", ["==", "GEOID", ""]);
                tooltip.style.display = "none";
            });

            renderLegend();
        });

        // Metric toggle
        document.querySelectorAll(".legend-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".legend-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                currentMetric = btn.dataset.metric;
                if (map.getLayer("county-fill")) {
                    map.setPaintProperty("county-fill", "fill-color", buildFillColor());
                }
                renderLegend();
            });
        });
    }

    // Expose for lazy init from view toggle
    window.initMap = function () {
        initMapInternal().catch(err => {
            console.error("Map init error:", err);
            document.getElementById("map-container").innerHTML =
                '<div class="loading">Failed to initialize map.</div>';
        });
    };
})();

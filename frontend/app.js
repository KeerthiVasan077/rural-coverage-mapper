/**
 * TerraSignal | Rural Coverage & Capacity Gap Mapper (SW-27)
 * National Deficit Framework Controller
 * Bound to Modern Telecom GIS UI (Light M3 Editorial Theme)
 */

// Application State
let map = null;
let districtConfig = null;
let currentDistrictId = "bihar-sitamarhi";
let currentRadiusKm = 5.0;
let currentTowerCount = 10;
let currentCapacityThresh = 10000;
let currentDeficitFilter = "all"; // 'all', 'zero', 'congested'
let debounceTimer = null;
let cachedGapsFeatures = [];
let cachedSitesGeoJson = null;

// Leaflet Layer Groups
const layerProposedGroup = L.layerGroup();
const layerDeficitGroup = L.layerGroup();
const layerExistingCoverageGroup = L.layerGroup();
const layerExistingTowersGroup = L.layerGroup();
const layerWaterGroup = L.layerGroup();

// DOM Element References
const sidebarDrawer = document.getElementById("sidebarDrawer");
const sidebarCollapseBtn = document.getElementById("sidebarCollapseBtn");
const sidebarExpandBtn = document.getElementById("sidebarExpandBtn");
const analyticsPanel = document.getElementById("analyticsPanel");
const toggleAnalyticsBtn = document.getElementById("toggleAnalyticsBtn");

const zoneSelector = document.getElementById("zoneSelector");
const districtBadge = document.getElementById("districtBadge");
const districtDesc = document.getElementById("districtDesc");
const demoModeBadge = document.getElementById("demoModeBadge");

const radiusSlider = document.getElementById("radiusSlider");
const radiusValDisplay = document.getElementById("radiusValDisplay");
const towersSlider = document.getElementById("towersSlider");
const towersValDisplay = document.getElementById("towersValDisplay");
const capacitySlider = document.getElementById("capacitySlider");
const capacityValDisplay = document.getElementById("capacityValDisplay");
const paramStatusText = document.getElementById("paramStatusText");

const metricZeroSignal = document.getElementById("metricZeroSignal");
const metricZeroSignalSub = document.getElementById("metricZeroSignalSub");
const metricBottleneck = document.getElementById("metricBottleneck");
const metricBottleneckSub = document.getElementById("metricBottleneckSub");
const metricWellServed = document.getElementById("metricWellServed");
const metricWellServedSub = document.getElementById("metricWellServedSub");
const metricUsersPerTower = document.getElementById("metricUsersPerTower");
const metricUsersPerTowerSub = document.getElementById("metricUsersPerTowerSub");

const deficitToggleGroup = document.getElementById("deficitToggleGroup");

const layerProposedCheck = document.getElementById("layerProposed");
const layerDeficitsCheck = document.getElementById("layerDeficits");
const layerExistingCoverageCheck = document.getElementById("layerExistingCoverage");
const layerExistingTowersCheck = document.getElementById("layerExistingTowers");
const layerWaterBodiesCheck = document.getElementById("layerWaterBodies");

const exportGeoJsonBtn = document.getElementById("exportGeoJsonBtn");
const exportCsvBtn = document.getElementById("exportCsvBtn");
const rankedSitesList = document.getElementById("rankedSitesList");
const rankedSitesCountBadge = document.getElementById("rankedSitesCountBadge");

/**
 * Modern Slider Visual Track Fill
 */
function updateSliderFill(slider, activeColor, trackColor = "#f4dde3") {
  if (!slider) return;
  const min = parseFloat(slider.min) || 0;
  const max = parseFloat(slider.max) || 100;
  const val = parseFloat(slider.value) || 0;
  const pct = Math.max(0, Math.min(100, ((val - min) / (max - min)) * 100));
  slider.style.background = `linear-gradient(to right, ${activeColor} 0%, ${activeColor} ${pct}%, ${trackColor} ${pct}%, ${trackColor} 100%)`;
}

/**
 * Toggle Collapsible Sidebar Drawer
 */
function toggleSidebar(collapsed) {
  if (!sidebarDrawer) return;
  sidebarDrawer.classList.toggle("collapsed-sidebar", collapsed);

  if (sidebarExpandBtn) {
    if (collapsed) {
      sidebarExpandBtn.classList.remove("hidden");
    } else {
      sidebarExpandBtn.classList.add("hidden");
    }
  }

  // Smooth Leaflet container resize
  setTimeout(() => {
    if (map) {
      map.invalidateSize({ debounceMoveEvents: true });
    }
  }, 350);
}

/**
 * Toggle Right Analytics Panel
 */
function toggleAnalytics(collapsed) {
  if (!analyticsPanel) return;
  analyticsPanel.classList.toggle("collapsed-panel", collapsed);
  setTimeout(() => {
    if (map) map.invalidateSize({ debounceMoveEvents: true });
  }, 350);
}

// Custom SVG Icons (Harmonized with TerraSignal Theme)
function createExistingTowerIcon(isOverloaded) {
  const strokeColor = isOverloaded ? "#a93700" : "#953891";
  const circleColor = isOverloaded ? "#ffebe5" : "#fab4fd";
  const svg = `
    <div class="existing-tower-icon">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${strokeColor}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" class="filter drop-shadow-sm">
        <path d="M12 2v20"></path>
        <path d="m4 22 4-15 4-5 4 5 4 15"></path>
        <path d="M6.5 17h11"></path>
        <path d="M8 12h8"></path>
        <path d="M9.5 7h5"></path>
        <circle cx="12" cy="2" r="1.5" fill="${circleColor}"></circle>
        <path d="M8 3.5a5.5 5.5 0 0 0 0 4"></path>
        <path d="M16 3.5a5.5 5.5 0 0 1 0 4"></path>
      </svg>
    </div>`;
  return L.divIcon({
    html: svg,
    className: "",
    iconSize: [20, 20],
    iconAnchor: [10, 10]
  });
}

function createProposedIcon(rank) {
  return L.divIcon({
    className: "",
    html: `<div class="proposed-node-marker">${rank}</div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12]
  });
}

/**
 * Initialize Leaflet Map
 */
function initMap(center, bbox) {
  if (map) {
    map.remove();
  }

  map = L.map("map", {
    center: center,
    zoom: 11,
    zoomControl: true,
    attributionControl: true
  });

  // Standard OpenStreetMap tiles (fast, crisp, high cartographic detail)
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" class="text-primary underline">OpenStreetMap</a> contributors'
  }).addTo(map);

  // Add layer groups to map
  layerWaterGroup.addTo(map);
  layerExistingCoverageGroup.addTo(map);
  layerDeficitGroup.addTo(map);
  layerExistingTowersGroup.addTo(map);
  layerProposedGroup.addTo(map);

  if (bbox && bbox.length === 4) {
    const corner1 = L.latLng(bbox[1], bbox[0]);
    const corner2 = L.latLng(bbox[3], bbox[2]);
    map.fitBounds(L.latLngBounds(corner1, corner2), { padding: [30, 30] });
  }
}

/**
 * Update District Callout Information Card
 */
function updateDistrictCard(district) {
  if (!district) return;

  const isCongested = district.category === "congested";
  const isZeroSignal = district.category === "zero_signal";

  if (districtBadge) {
    if (isCongested) {
      districtBadge.className = "inline-block px-2.5 py-0.5 rounded text-[10px] font-extrabold bg-amber-50 text-amber-900 border border-amber-200 uppercase tracking-wider shadow-sm";
      districtBadge.textContent = "Capacity Bottleneck Belt";
    } else if (isZeroSignal) {
      // High-contrast soft white/light gray background with dark red text for superior readability
      districtBadge.className = "inline-block px-2.5 py-0.5 rounded text-[10px] font-extrabold bg-slate-50 text-[#991b1b] border border-red-200 uppercase tracking-wider shadow-sm";
      districtBadge.textContent = "Zero-Signal Forest Blackspot";
    } else {
      districtBadge.className = "inline-block px-2.5 py-0.5 rounded text-[10px] font-extrabold bg-purple-50 text-purple-900 border border-purple-200 uppercase tracking-wider shadow-sm";
      districtBadge.textContent = "Sparse Terrain Settlement";
    }
  }

  if (districtDesc) {
    const categoryLabel = isCongested ? "High-Density Bottleneck" : (isZeroSignal ? "Absolute Zero-Signal Blackspot" : "Dispersed Hamlets");
    const primaryIssue = isCongested ? "Severe cell overload (>15k users/tower), catastrophic data throttling" : (isZeroSignal ? "Total tower absence across Scheduled Tribe habitations & forest ravines" : "Isolated rural habitations with sparse highway-only cellular links");
    const teledensity = isCongested ? "~42% rural teledensity (lowest in India, DoT audit)" : (isZeroSignal ? "1,789 unconnected villages across rugged forest terrain" : "Sub-50% teledensity with long distance between cells");

    districtDesc.innerHTML = `
      <ul class="space-y-1.5 text-[11px] text-on-surface-variant pt-0.5">
        <li class="flex items-start gap-1.5">
          <span class="text-primary font-bold shrink-0">•</span>
          <span><strong class="text-on-surface font-semibold">Classification:</strong> ${categoryLabel}</span>
        </li>
        <li class="flex items-start gap-1.5">
          <span class="text-tertiary font-bold shrink-0">•</span>
          <span><strong class="text-on-surface font-semibold">Deficit Threat:</strong> ${primaryIssue}</span>
        </li>
        <li class="flex items-start gap-1.5">
          <span class="text-error font-bold shrink-0">•</span>
          <span><strong class="text-on-surface font-semibold">Audit Baseline:</strong> ${teledensity}</span>
        </li>
      </ul>
    `;
  }
}

/**
 * Fetch and Render Existing Macro Towers
 */
async function loadExistingTowers() {
  layerExistingTowersGroup.clearLayers();
  layerExistingCoverageGroup.clearLayers();

  try {
    const res = await fetch(`/api/towers?district_id=${currentDistrictId}`);
    if (!res.ok) throw new Error("Failed to load towers");
    const geojson = await res.json();

    geojson.features.forEach((feat) => {
      const [lon, lat] = feat.geometry.coordinates;
      const p = feat.properties || {};
      const users = p.users_served ? p.users_served.toLocaleString() : "N/A";
      const isOverloaded = p.congested || (p.users_served && p.users_served >= currentCapacityThresh);

      // Tower Marker
      const marker = L.marker([lat, lon], { icon: createExistingTowerIcon(isOverloaded) });
      marker.bindPopup(`
        <div class="text-xs text-on-surface font-sans p-1">
          <div class="font-bold border-b pb-1 mb-1 flex items-center justify-between gap-2">
            <span class="text-primary">Cell Tower (${p.radio || "LTE"})</span>
            <span class="text-[10px] px-1.5 py-0.2 rounded font-mono ${isOverloaded ? 'bg-tertiary-container text-tertiary font-bold' : 'bg-primary-container text-primary font-bold'}">
              ${isOverloaded ? 'Overloaded' : 'Normal'}
            </span>
          </div>
          <div>Location: <span class="font-mono text-outline">${lat.toFixed(4)}, ${lon.toFixed(4)}</span></div>
          <div>Coverage Radius: <span class="font-semibold">${p.range_km || currentRadiusKm} km</span></div>
          <div class="mt-1">Load: <strong class="${isOverloaded ? 'text-tertiary' : 'text-on-surface'}">${users} users</strong></div>
        </div>
      `);
      layerExistingTowersGroup.addLayer(marker);

      // Existing Tower Coverage Circle
      const radiusMeters = (p.range_km ? p.range_km : currentRadiusKm) * 1000;
      const circle = L.circle([lat, lon], {
        radius: radiusMeters,
        color: "#953891",
        weight: 1.4,
        dashArray: "5, 5",
        fillColor: "#fab4fd",
        fillOpacity: 0.1
      });
      layerExistingCoverageGroup.addLayer(circle);
    });
  } catch (err) {
    console.error("Error loading existing towers:", err);
  }
}

/**
 * Fetch and Render Water Bodies (Exclusions)
 */
async function loadWaterBodies() {
  layerWaterGroup.clearLayers();
  try {
    const res = await fetch(`/api/water?district_id=${currentDistrictId}`);
    if (!res.ok) return;
    const geojson = await res.json();

    if (geojson && geojson.features && geojson.features.length > 0) {
      L.geoJSON(geojson, {
        style: {
          color: "#0284c7",
          weight: 1.5,
          fillColor: "#bae6fd",
          fillOpacity: 0.35
        },
        onEachFeature: (feature, layer) => {
          layer.bindTooltip("Water Body (Excluded from Candidate Sites)", { sticky: true });
        }
      }).addTo(layerWaterGroup);
    }
  } catch (err) {
    console.warn("Water bodies load skipped:", err);
  }
}

/**
 * Render Deficit Grid Cells (Zero-Signal vs Congested)
 */
function renderDeficits() {
  layerDeficitGroup.clearLayers();

  if (!cachedGapsFeatures || cachedGapsFeatures.length === 0) return;

  const filtered = cachedGapsFeatures.filter((f) => {
    const p = f.properties || {};
    if (currentDeficitFilter === "zero") return p.gap_type === "zero_signal" || p.uncovered === true;
    if (currentDeficitFilter === "congested") return p.gap_type === "congested";
    return true;
  });

  filtered.forEach((feat) => {
    const [lon, lat] = feat.geometry.coordinates;
    const p = feat.properties || {};
    const isZero = p.gap_type === "zero_signal" || p.uncovered === true;
    const pop = Math.round(p.population || 0);

    const color = isZero ? "#ba1a1a" : "#a93700";
    const fillColor = isZero ? "#ffdad6" : "#ffebe5";

    const circle = L.circleMarker([lat, lon], {
      radius: Math.min(10, Math.max(3.5, Math.sqrt(pop) * 0.45)),
      color: color,
      weight: 1,
      fillColor: fillColor,
      fillOpacity: isZero ? 0.75 : 0.65
    });

    circle.bindTooltip(`
      <div class="text-xs p-1">
        <div class="font-bold ${isZero ? 'text-error' : 'text-tertiary'}">
          ${isZero ? 'Zero-Signal Blackspot' : 'Capacity Bottleneck'}
        </div>
        <div>Population: <strong>${pop.toLocaleString()} people</strong></div>
        <div>Nearest Tower: <strong>${p.dist_to_tower_km ? p.dist_to_tower_km.toFixed(1) : '> ' + currentRadiusKm} km</strong></div>
      </div>
    `);

    layerDeficitGroup.addLayer(circle);
  });
}

/**
 * Render Proposed K Sites
 */
function renderProposedSites(sitesGeoJson) {
  layerProposedGroup.clearLayers();
  if (rankedSitesList) rankedSitesList.innerHTML = "";

  if (!sitesGeoJson || !sitesGeoJson.features || sitesGeoJson.features.length === 0) {
    if (rankedSitesList) {
      rankedSitesList.innerHTML = '<div class="text-outline text-center py-3 text-xs">No candidate sites found.</div>';
    }
    if (rankedSitesCountBadge) rankedSitesCountBadge.textContent = "0 Sites";
    return;
  }

  if (rankedSitesCountBadge) {
    rankedSitesCountBadge.textContent = `${sitesGeoJson.features.length} Sites`;
  }

  const features = sitesGeoJson.features;

  features.forEach((feat) => {
    const [lon, lat] = feat.geometry.coordinates;
    const p = feat.properties || {};
    const rank = p.rank || 1;
    const gained = p.people_gained ? Math.round(p.people_gained).toLocaleString() : "N/A";
    const cumPct = p.cumulative_coverage_pct ? p.cumulative_coverage_pct.toFixed(1) : "";

    // Candidate Marker Pin with Anchored Caret Pointer
    const marker = L.marker([lat, lon], { icon: createProposedIcon(rank) });
    marker.bindPopup(`
      <div class="text-xs text-on-surface font-sans p-1">
        <div class="font-bold text-primary border-b pb-1 mb-1 flex items-center justify-between">
          <span>Proposed Site #${rank}</span>
          <span class="text-[10px] bg-primary-container text-primary px-1.5 py-0.5 rounded font-mono font-bold">+${gained} pop</span>
        </div>
        <div>Coordinates: <span class="font-mono text-outline">${lat.toFixed(4)}, ${lon.toFixed(4)}</span></div>
        <div>Coverage Boost: <strong class="text-primary">${cumPct}% cumulative</strong></div>
        <div class="text-[11px] text-on-surface-variant mt-1">Recommended 4G/5G macro cell installation</div>
      </div>
    `, {
      className: "proposed-site-popup",
      offset: [0, -12],
      closeButton: true
    });
    layerProposedGroup.addLayer(marker);

    // Proposed Coverage Buffer
    const proposedCircle = L.circle([lat, lon], {
      radius: currentRadiusKm * 1000,
      color: "#953891",
      weight: 1.8,
      fillColor: "#fab4fd",
      fillOpacity: 0.14
    });
    layerProposedGroup.addLayer(proposedCircle);

    // Populate Ranked Sites Sidebar Card
    if (rankedSitesList) {
      const item = document.createElement("div");
      item.className = "flex items-center justify-between p-2 rounded-lg bg-surface-container/60 hover:bg-surface-container-high cursor-pointer transition border border-outline-variant/30 group";
      item.innerHTML = `
        <div class="flex items-center space-x-2">
          <span class="w-5 h-5 rounded-full bg-primary text-white font-bold text-[10px] flex items-center justify-center shadow-sm">
            ${rank}
          </span>
          <div>
            <div class="font-bold text-on-surface text-xs">+${gained} people</div>
            <div class="text-[9.5px] font-mono text-outline">${lat.toFixed(3)}, ${lon.toFixed(3)}</div>
          </div>
        </div>
        <div class="text-right">
          <div class="text-[10px] font-mono font-extrabold text-primary">${cumPct}%</div>
          <div class="text-[9px] text-outline font-medium">coverage</div>
        </div>
      `;
      item.addEventListener("click", () => {
        map.flyTo([lat, lon], 12, { animate: true, duration: 0.8 });
        marker.openPopup();
      });
      rankedSitesList.appendChild(item);
    }
  });
}

/**
 * Run Core Geospatial Optimization
 */
async function runOptimization() {
  if (paramStatusText) {
    paramStatusText.textContent = `Optimizing R=${currentRadiusKm}km, K=${currentTowerCount}...`;
  }

  try {
    const [summaryRes, gapsRes, sitesRes] = await Promise.all([
      fetch(`/api/summary?district_id=${currentDistrictId}&radius_km=${currentRadiusKm}&k=${currentTowerCount}&capacity_threshold=${currentCapacityThresh}`),
      fetch(`/api/gaps?district_id=${currentDistrictId}&radius_km=${currentRadiusKm}&capacity_threshold=${currentCapacityThresh}`),
      fetch(`/api/sites?district_id=${currentDistrictId}&radius_km=${currentRadiusKm}&k=${currentTowerCount}&capacity_threshold=${currentCapacityThresh}`)
    ]);

    if (!summaryRes.ok || !gapsRes.ok || !sitesRes.ok) {
      throw new Error("API computation error");
    }

    const summary = await summaryRes.json();
    const gapsGeoJson = await gapsRes.json();
    const sitesGeoJson = await sitesRes.json();

    cachedGapsFeatures = gapsGeoJson.features || [];
    cachedSitesGeoJson = sitesGeoJson;

    const zeroPop = summary.zero_signal_population ?? summary.uncovered_population ?? 0;
    const zeroPct = summary.zero_signal_pct ?? summary.uncovered_pct ?? 0;
    const congPop = summary.congested_population ?? 0;
    const congPct = summary.congested_pct ?? 0;
    const wellPop = summary.well_served_population ?? summary.baseline_covered_population ?? 0;
    const wellPct = summary.well_served_pct ?? summary.baseline_coverage_pct ?? 0;
    const gained = summary.people_gained_total ?? summary.total_people_gained ?? 0;
    const basePct = summary.baseline_coverage_pct ?? 0;
    const finalPct = summary.final_coverage_pct ?? 0;
    const avgBefore = summary.avg_users_per_tower_before ?? 0;

    // Update Metric Cards
    if (metricZeroSignal) metricZeroSignal.textContent = Math.round(zeroPop).toLocaleString();
    if (metricZeroSignalSub) metricZeroSignalSub.textContent = `${zeroPct.toFixed(1)}% unserved`;

    if (metricBottleneck) metricBottleneck.textContent = Math.round(congPop).toLocaleString();
    const congCount = summary.congested_towers_count !== undefined ? `${summary.congested_towers_count} overloaded` : "overloaded";
    if (metricBottleneckSub) metricBottleneckSub.textContent = `${congPct.toFixed(1)}% (${congCount})`;

    if (metricWellServed) metricWellServed.textContent = Math.round(wellPop).toLocaleString();
    if (metricWellServedSub) metricWellServedSub.textContent = `${wellPct.toFixed(1)}% compliant coverage`;

    if (metricUsersPerTower) metricUsersPerTower.textContent = `~${Math.round(avgBefore).toLocaleString()}`;
    if (metricUsersPerTowerSub) metricUsersPerTowerSub.textContent = `Safe threshold (≤${Math.round(currentCapacityThresh).toLocaleString()})`;

    // Status Indicator
    if (paramStatusText) {
      paramStatusText.textContent = `Calculated for R=${currentRadiusKm}km, K=${currentTowerCount} (+${Math.round(gained).toLocaleString()} gained, ${basePct.toFixed(1)}% → ${finalPct.toFixed(1)}%)`;
    }

    // Render layers
    renderDeficits();
    renderProposedSites(sitesGeoJson);

  } catch (err) {
    console.error("Optimization failed:", err);
    if (paramStatusText) {
      paramStatusText.textContent = `Calculation failed: ${err.message}`;
    }
  }
}

/**
 * Switch District
 */
async function switchDistrict(districtId) {
  currentDistrictId = districtId;

  const district = (districtConfig.districts_list || []).find((d) => d.id === districtId) || districtConfig.district;
  updateDistrictCard(district);

  if (district.bbox && map) {
    const b = district.bbox;
    map.fitBounds([[b[1], b[0]], [b[3], b[2]]], { padding: [30, 30] });
  }

  await Promise.all([loadExistingTowers(), loadWaterBodies()]);
  await runOptimization();
}

/**
 * Debounced Trigger for Sliders
 */
function triggerDebouncedOptimization() {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    runOptimization();
  }, 280);
}

/**
 * Export Proposed Sites as GeoJSON
 */
function exportGeoJson() {
  if (!cachedSitesGeoJson) {
    alert("No proposed sites data available to export.");
    return;
  }
  const blob = new Blob([JSON.stringify(cachedSitesGeoJson, null, 2)], { type: "application/geo+json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `proposed_towers_${currentDistrictId}_R${currentRadiusKm}_K${currentTowerCount}.geojson`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Export Proposed Sites as CSV
 */
function exportCsv() {
  if (!cachedSitesGeoJson || !cachedSitesGeoJson.features || cachedSitesGeoJson.features.length === 0) {
    alert("No proposed sites data available to export.");
    return;
  }

  const rows = [
    ["rank", "latitude", "longitude", "people_gained", "cumulative_coverage_pct", "coverage_radius_km", "district"]
  ];

  cachedSitesGeoJson.features.forEach((f) => {
    const [lon, lat] = f.geometry.coordinates;
    const p = f.properties || {};
    rows.push([
      p.rank || "",
      lat.toFixed(6),
      lon.toFixed(6),
      p.people_gained ? Math.round(p.people_gained) : "",
      p.cumulative_coverage_pct || "",
      currentRadiusKm,
      currentDistrictId
    ]);
  });

  const csvContent = "data:text/csv;charset=utf-8," + rows.map((e) => e.join(",")).join("\n");
  const encodedUri = encodeURI(csvContent);
  const link = document.createElement("a");
  link.setAttribute("href", encodedUri);
  link.setAttribute("download", `proposed_towers_${currentDistrictId}_R${currentRadiusKm}_K${currentTowerCount}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

/**
 * Boot and Wire Application Event Handlers
 */
async function initApp() {
  try {
    // 1. Fetch Config
    const res = await fetch("/api/config");
    districtConfig = await res.json();

    // 2. Populate District Selector
    if (zoneSelector) {
      zoneSelector.innerHTML = "";
      (districtConfig.districts_list || [districtConfig.district]).forEach((d) => {
        const opt = document.createElement("option");
        opt.value = d.id;
        const catLabel = d.category === "congested" ? " [Congested Plain]" : " [Zero-Signal Blackspot]";
        opt.textContent = `${d.state}: ${d.name}${catLabel}`;
        if (d.id === (districtConfig.district?.id || "bihar-sitamarhi")) {
          opt.selected = true;
        }
        zoneSelector.appendChild(opt);
      });

      zoneSelector.addEventListener("change", (e) => {
        switchDistrict(e.target.value);
      });
    }

    currentDistrictId = districtConfig.district?.id || "bihar-sitamarhi";
    updateDistrictCard(districtConfig.district);

    // 3. Set Defaults from Config
    if (districtConfig.coverage) {
      if (districtConfig.coverage.default_radius_km) currentRadiusKm = districtConfig.coverage.default_radius_km;
      if (districtConfig.coverage.default_k) currentTowerCount = districtConfig.coverage.default_k;
    }
    if (districtConfig.congestion && districtConfig.congestion.default_capacity_users_per_tower) {
      currentCapacityThresh = districtConfig.congestion.default_capacity_users_per_tower;
    }

    if (radiusSlider) {
      radiusSlider.value = currentRadiusKm;
      updateSliderFill(radiusSlider, "#953891", "#f4dde3");
    }
    if (radiusValDisplay) radiusValDisplay.textContent = `${currentRadiusKm.toFixed(1)} km`;

    if (towersSlider) {
      towersSlider.value = currentTowerCount;
      updateSliderFill(towersSlider, "#834889", "#f4dde3");
    }
    if (towersValDisplay) towersValDisplay.textContent = currentTowerCount;

    if (capacitySlider) {
      capacitySlider.value = currentCapacityThresh;
      updateSliderFill(capacitySlider, "#a93700", "#f4dde3");
    }
    if (capacityValDisplay) capacityValDisplay.textContent = `${currentCapacityThresh.toLocaleString()} users`;

    // 4. Initialize Map
    initMap(districtConfig.center || [26.60, 85.50], districtConfig.bbox);

    // 5. Wire Drawer Toggle Buttons
    if (sidebarCollapseBtn) {
      sidebarCollapseBtn.addEventListener("click", () => toggleSidebar(true));
    }
    if (sidebarExpandBtn) {
      sidebarExpandBtn.addEventListener("click", () => toggleSidebar(false));
    }
    if (toggleAnalyticsBtn && analyticsPanel) {
      toggleAnalyticsBtn.addEventListener("click", () => {
        analyticsPanel.classList.toggle("collapsed-panel");
        setTimeout(() => {
          if (map) map.invalidateSize({ debounceMoveEvents: true });
        }, 350);
      });
    }

    // 6. Wire Slider Event Handlers with Real-Time Track Fills
    if (radiusSlider) {
      radiusSlider.addEventListener("input", (e) => {
        currentRadiusKm = parseFloat(e.target.value);
        if (radiusValDisplay) radiusValDisplay.textContent = `${currentRadiusKm.toFixed(1)} km`;
        if (paramStatusText) paramStatusText.textContent = `Calculated for R=${currentRadiusKm}km, K=${currentTowerCount}`;
        updateSliderFill(radiusSlider, "#953891", "#f4dde3");
        triggerDebouncedOptimization();
      });
    }

    if (towersSlider) {
      towersSlider.addEventListener("input", (e) => {
        currentTowerCount = parseInt(e.target.value);
        if (towersValDisplay) towersValDisplay.textContent = currentTowerCount;
        if (paramStatusText) paramStatusText.textContent = `Calculated for R=${currentRadiusKm}km, K=${currentTowerCount}`;
        updateSliderFill(towersSlider, "#834889", "#f4dde3");
        triggerDebouncedOptimization();
      });
    }

    if (capacitySlider) {
      capacitySlider.addEventListener("input", (e) => {
        currentCapacityThresh = parseInt(e.target.value);
        if (capacityValDisplay) capacityValDisplay.textContent = `${currentCapacityThresh.toLocaleString()} users`;
        updateSliderFill(capacitySlider, "#a93700", "#f4dde3");
        triggerDebouncedOptimization();
      });
    }

    // 7. Wire Deficit Filter Toggle Buttons
    if (deficitToggleGroup) {
      const buttons = deficitToggleGroup.querySelectorAll("button");
      buttons.forEach((btn) => {
        btn.addEventListener("click", () => {
          buttons.forEach((b) => {
            b.className = "flex-1 py-1 px-2 rounded-lg text-xs font-semibold bg-surface-container-high hover:bg-surface-variant text-on-surface flex items-center justify-center space-x-1 transition";
          });
          btn.className = "flex-1 py-1 px-2 rounded-lg text-xs font-bold bg-primary text-white shadow-sm transition flex items-center justify-center space-x-1";

          currentDeficitFilter = btn.getAttribute("data-type") || "all";
          renderDeficits();
        });
      });
    }

    // 8. Wire Layer Checkboxes
    if (layerProposedCheck) {
      layerProposedCheck.addEventListener("change", (e) => {
        if (e.target.checked) map.addLayer(layerProposedGroup);
        else map.removeLayer(layerProposedGroup);
      });
    }
    if (layerDeficitsCheck) {
      layerDeficitsCheck.addEventListener("change", (e) => {
        if (e.target.checked) map.addLayer(layerDeficitGroup);
        else map.removeLayer(layerDeficitGroup);
      });
    }
    if (layerExistingCoverageCheck) {
      layerExistingCoverageCheck.addEventListener("change", (e) => {
        if (e.target.checked) map.addLayer(layerExistingCoverageGroup);
        else map.removeLayer(layerExistingCoverageGroup);
      });
    }
    if (layerExistingTowersCheck) {
      layerExistingTowersCheck.addEventListener("change", (e) => {
        if (e.target.checked) map.addLayer(layerExistingTowersGroup);
        else map.removeLayer(layerExistingTowersGroup);
      });
    }
    if (layerWaterBodiesCheck) {
      layerWaterBodiesCheck.addEventListener("change", (e) => {
        if (e.target.checked) map.addLayer(layerWaterGroup);
        else map.removeLayer(layerWaterGroup);
      });
    }

    // 9. Wire Export Buttons
    if (exportGeoJsonBtn) exportGeoJsonBtn.addEventListener("click", exportGeoJson);
    if (exportCsvBtn) exportCsvBtn.addEventListener("click", exportCsv);

    // 10. Initial Data Load
    await Promise.all([loadExistingTowers(), loadWaterBodies()]);
    await runOptimization();

    // 11. Support URL state parameters (for deep-linking & screenshots)
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get("collapsed") === "true") {
      toggleSidebar(true);
    }

  } catch (err) {
    console.error("Initialization error:", err);
    if (paramStatusText) {
      paramStatusText.textContent = `Initialization error: ${err.message}`;
    }
  }
}

// Start application when DOM is ready
window.addEventListener("DOMContentLoaded", initApp);

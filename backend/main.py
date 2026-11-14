"""
FastAPI application for Rural Coverage Gap Mapper (SW-27).
Implements the Comprehensive National Framework (DoT Audits & Open Geospatial Analysis):
- Dual Deficit Tracking: Zero-Signal Blackspots (Tribal/Forest) + Capacity Bottlenecks (Congested Plains).
- Regional Presets for Bihar, Odisha, UP Terai, Meghalaya, Chhattisgarh, Tamil Nadu, Karnataka, Rajasthan.
- Vectorized Maximum Coverage Optimizer with Anti-Double-Counting.
"""

import os
import logging
from typing import Dict, Any, Tuple, Optional, List
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.loaders import (
    load_population_data,
    load_towers_data,
    fetch_osm_water_bodies,
)
from backend.demo_data import generate_demo_dataset
from backend.optimizer import (
    calculate_baseline_coverage,
    generate_candidate_sites,
    run_greedy_max_coverage,
    BaselineCoverage,
    OptimizationResult,
)

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rural_mapper.api")

# Load configuration
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

DISTRICTS_LIST = CONFIG.get("districts", [])
DISTRICTS_BY_ID = {d.get("id"): d for d in DISTRICTS_LIST}
DEFAULT_DISTRICT_ID = DISTRICTS_LIST[0].get("id")

app = FastAPI(
    title="Rural Coverage Gap Mapper",
    description="Identifies underserved rural areas and optimizes new mobile tower site selection.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_STORE_MAP: Dict[str, Dict[str, Any]] = {}
OPTIMIZATION_CACHE: Dict[Tuple[str, float, int, float], Tuple[BaselineCoverage, OptimizationResult]] = {}


def get_district_info(district_id: Optional[str] = None) -> Dict[str, Any]:
    if not district_id or district_id not in DISTRICTS_BY_ID:
        district_id = DEFAULT_DISTRICT_ID
    return DISTRICTS_BY_ID[district_id]


def get_data_store(district_id: Optional[str] = None) -> Dict[str, Any]:
    dist_cfg = get_district_info(district_id)
    d_id = dist_cfg["id"]

    if d_id not in DATA_STORE_MAP:
        bbox = dist_cfg["bbox"]
        pop_path = CONFIG["data"]["population_raster"]
        agg_thresh = CONFIG["optimization"]["aggregation_threshold"]
        step_km = CONFIG["optimization"]["grid_step_km"]

        p_lons, p_lats, p_pops, is_demo_pop = load_population_data(
            pop_path, bbox, agg_thresh, step_km
        )

        t_df, is_demo_towers = load_towers_data(
            CONFIG["data"]["opencellid_csv"], bbox
        )

        water_geom = None
        if CONFIG["optimization"]["filter_water_bodies"]:
            if is_demo_pop or is_demo_towers:
                _, _, water_geom = generate_demo_dataset(bbox)
            else:
                cache_file = CONFIG["data"]["osm_water_cache"].format(district_id=d_id)
                water_geom = fetch_osm_water_bodies(
                    bbox,
                    cache_file,
                    os.getenv("OVERPASS_API_URL", "https://overpass-api.de/api/interpreter"),
                )

        center_lon = (bbox[0] + bbox[2]) / 2.0
        center_lat = (bbox[1] + bbox[3]) / 2.0

        DATA_STORE_MAP[d_id] = {
            "pop_lons": p_lons,
            "pop_lats": p_lats,
            "pop_counts": p_pops,
            "towers_df": t_df,
            "water_geom": water_geom,
            "is_demo": is_demo_pop or is_demo_towers,
            "center_lon": center_lon,
            "center_lat": center_lat,
            "bbox": bbox,
            "name": dist_cfg["name"],
        }

    return DATA_STORE_MAP[d_id]


def _safe_float(val, default: float) -> float:
    if hasattr(val, "default"):
        return float(val.default)
    return float(val) if val is not None else float(default)


def _safe_int(val, default: int) -> int:
    if hasattr(val, "default"):
        return int(val.default)
    return int(val) if val is not None else int(default)


def _safe_str(val, default: str) -> str:
    if hasattr(val, "default"):
        return str(val.default)
    return str(val) if val is not None else str(default)


def get_or_compute_optimization(
    district_id: str,
    radius_km: float,
    k: int,
    capacity_threshold: float = 10000.0,
) -> Tuple[BaselineCoverage, OptimizationResult]:
    radius_km = _safe_float(radius_km, 5.0)
    k = _safe_int(k, 10)
    capacity_threshold = _safe_float(capacity_threshold, 10000.0)

    key = (district_id, round(float(radius_km), 2), int(k), round(float(capacity_threshold), 1))
    if key in OPTIMIZATION_CACHE:
        return OPTIMIZATION_CACHE[key]

    store = get_data_store(district_id)
    radio_radii = CONFIG["coverage"].get("supported_radii", {})

    baseline = calculate_baseline_coverage(
        pop_lons=store["pop_lons"],
        pop_lats=store["pop_lats"],
        pop_counts=store["pop_counts"],
        towers_df=store["towers_df"],
        center_lon=store["center_lon"],
        center_lat=store["center_lat"],
        default_radius_km=radius_km,
        radio_radii=radio_radii,
        capacity_threshold=capacity_threshold,
    )

    c_lons, c_lats, c_x, c_y = generate_candidate_sites(
        uncovered_lons=baseline.uncovered_lons,
        uncovered_lats=baseline.uncovered_lats,
        uncovered_pops=baseline.uncovered_pops,
        uncovered_x_km=baseline.uncovered_x_km,
        uncovered_y_km=baseline.uncovered_y_km,
        max_candidates=CONFIG["optimization"]["max_candidates"],
        water_bodies=store["water_geom"],
    )

    n_existing = len(store["towers_df"]) if store["towers_df"] is not None else 0

    opt_result = run_greedy_max_coverage(
        uncovered_lons=baseline.uncovered_lons,
        uncovered_lats=baseline.uncovered_lats,
        uncovered_pops=baseline.uncovered_pops,
        uncovered_x_km=baseline.uncovered_x_km,
        uncovered_y_km=baseline.uncovered_y_km,
        cand_lons=c_lons,
        cand_lats=c_lats,
        cand_x_km=c_x,
        cand_y_km=c_y,
        k=k,
        radius_km=radius_km,
        initial_covered_pop=baseline.covered_pop,
        total_pop=baseline.total_pop,
        capacity_threshold=capacity_threshold,
        existing_towers_count=n_existing,
    )

    OPTIMIZATION_CACHE[key] = (baseline, opt_result)
    return baseline, opt_result


@app.get("/api/config")
def get_config(district_id: Optional[str] = None):
    """Returns application configuration, bounding box, and all national benchmark districts."""
    dist_cfg = get_district_info(district_id)
    store = get_data_store(dist_cfg["id"])
    return {
        "district": dist_cfg,
        "districts_list": [
            {
                "id": d["id"],
                "name": d["name"],
                "state": d.get("state", "India"),
                "category": d.get("category", "zero_signal"),
                "description": d.get("description", ""),
            }
            for d in DISTRICTS_LIST
        ],
        "bbox": store["bbox"],
        "center": [store["center_lat"], store["center_lon"]],
        "coverage": CONFIG["coverage"],
        "congestion": CONFIG.get("congestion", {}),
        "is_demo_mode": store["is_demo"],
    }


@app.get("/api/summary")
def get_summary(
    district_id: Optional[str] = None,
    radius_km: float = Query(5.0, ge=1.0, le=15.0),
    k: int = Query(10, ge=1, le=30),
    capacity_threshold: float = Query(10000.0, ge=3000.0, le=30000.0),
):
    """Returns total pop, zero-signal pop, congested pop, well-served pop, and K tower gains."""
    radius_km = _safe_float(radius_km, 5.0)
    k = _safe_int(k, 10)
    capacity_threshold = _safe_float(capacity_threshold, 10000.0)

    dist_cfg = get_district_info(district_id)
    d_id = dist_cfg["id"]
    baseline, opt = get_or_compute_optimization(d_id, radius_km, k, capacity_threshold)
    store = get_data_store(d_id)

    return {
        "district_id": d_id,
        "district_name": str(dist_cfg["name"]),
        "state": str(dist_cfg.get("state", "India")),
        "category": str(dist_cfg.get("category", "zero_signal")),
        "bbox": store["bbox"],
        "is_demo_mode": bool(store["is_demo"]),
        "radius_km": float(radius_km),
        "k": int(k),
        "capacity_threshold": float(capacity_threshold),
        
        # Demographic & Baseline Deficits
        "total_population": int(round(baseline.total_pop)),
        "zero_signal_population": int(round(baseline.uncovered_pop)),
        "zero_signal_pct": float(round((baseline.uncovered_pop / baseline.total_pop * 100.0) if baseline.total_pop > 0 else 0.0, 2)),
        
        "congested_population": int(round(baseline.congested_pop)),
        "congested_pct": float(round(baseline.congested_pct, 2)),
        "congested_towers_count": int(baseline.congested_towers_count),
        
        "well_served_population": int(round(baseline.well_served_pop)),
        "well_served_pct": float(round(baseline.well_served_pct, 2)),
        
        "baseline_covered_population": int(round(baseline.covered_pop)),
        "baseline_coverage_pct": float(round(baseline.coverage_pct, 2)),
        
        # Post-Optimization Metrics
        "final_covered_population": int(round(opt.final_covered_pop)),
        "final_coverage_pct": float(round(opt.final_coverage_pct, 2)),
        "people_gained_total": int(round(opt.people_gained_total)),
        "people_gained_pct": float(round(opt.final_coverage_pct - baseline.coverage_pct, 2)),
        
        # Capacity & Users per Tower
        "avg_users_per_tower_before": float(baseline.avg_users_per_tower),
        "avg_users_per_tower_after": float(opt.final_avg_users_per_tower),
        "towers_placed": int(len(opt.sites)),
        "uncovered_cells_count": int(len(baseline.uncovered_pops)),
    }


@app.get("/api/gaps")
def get_gaps(
    district_id: Optional[str] = None,
    radius_km: float = Query(5.0, ge=1.0, le=15.0),
    capacity_threshold: float = Query(10000.0, ge=3000.0, le=30000.0),
    deficit_type: str = Query("all", description="all | zero_signal | congested"),
):
    radius_km = _safe_float(radius_km, 5.0)
    capacity_threshold = _safe_float(capacity_threshold, 10000.0)
    deficit_type = _safe_str(deficit_type, "all")

    dist_cfg = get_district_info(district_id)
    baseline, _ = get_or_compute_optimization(dist_cfg["id"], radius_km, 1, capacity_threshold)

    features = []

    # 1. Zero-Signal Blackspots (Red)
    if deficit_type in ("all", "zero_signal"):
        for lon, lat, pop in zip(
            baseline.uncovered_lons, baseline.uncovered_lats, baseline.uncovered_pops
        ):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [float(lon), float(lat)],
                    },
                    "properties": {
                        "gap_type": "zero_signal",
                        "label": "Zero-Signal Blackspot",
                        "population": round(float(pop), 1),
                    },
                }
            )

    # 2. Capacity Bottlenecks (Orange/Amber)
    if deficit_type in ("all", "congested"):
        for lon, lat, pop in zip(
            baseline.congested_lons, baseline.congested_lats, baseline.congested_pops
        ):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [float(lon), float(lat)],
                    },
                    "properties": {
                        "gap_type": "congested",
                        "label": "Capacity Bottleneck (>10k users/tower)",
                        "population": round(float(pop), 1),
                    },
                }
            )

    return {
        "type": "FeatureCollection",
        "district_id": dist_cfg["id"],
        "radius_km": radius_km,
        "deficit_type": deficit_type,
        "count": len(features),
        "features": features,
    }


@app.get("/api/sites")
def get_sites(
    district_id: Optional[str] = None,
    radius_km: float = Query(5.0, ge=1.0, le=15.0),
    k: int = Query(10, ge=1, le=30),
    capacity_threshold: float = Query(10000.0, ge=3000.0, le=30000.0),
):
    radius_km = _safe_float(radius_km, 5.0)
    k = _safe_int(k, 10)
    capacity_threshold = _safe_float(capacity_threshold, 10000.0)

    dist_cfg = get_district_info(district_id)
    _, opt = get_or_compute_optimization(dist_cfg["id"], radius_km, k, capacity_threshold)

    features = []
    for site in opt.sites:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [site.lon, site.lat],
                },
                "properties": {
                    "rank": site.rank,
                    "people_gained": round(site.people_gained),
                    "cumulative_covered_pop": round(site.cumulative_covered_pop),
                    "cumulative_coverage_pct": round(site.cumulative_coverage_pct, 2),
                    "radius_km": site.radius_km,
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "district_id": dist_cfg["id"],
        "radius_km": radius_km,
        "k": k,
        "features": features,
    }


@app.get("/api/towers")
def get_towers(district_id: Optional[str] = None):
    dist_cfg = get_district_info(district_id)
    store = get_data_store(dist_cfg["id"])
    t_df = store["towers_df"]

    features = []
    if t_df is not None and not t_df.empty:
        for _, row in t_df.iterrows():
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [float(row["lon"]), float(row["lat"])],
                    },
                    "properties": {
                        "radio": str(row.get("radio", "LTE")),
                        "mcc": int(row.get("mcc", 404)),
                        "net": int(row.get("net", 0)),
                        "range": float(row.get("range", 5000)),
                    },
                }
            )

    return {
        "type": "FeatureCollection",
        "district_id": dist_cfg["id"],
        "count": len(features),
        "features": features,
    }


@app.get("/api/water")
def get_water(district_id: Optional[str] = None):
    dist_cfg = get_district_info(district_id)
    store = get_data_store(dist_cfg["id"])
    geom = store.get("water_geom")

    features = []
    if geom is not None and not geom.is_empty:
        features.append(
            {
                "type": "Feature",
                "geometry": geom.__geo_interface__,
                "properties": {"type": "water_body"},
            }
        )

    return {
        "type": "FeatureCollection",
        "district_id": dist_cfg["id"],
        "features": features,
    }


FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def serve_index():
    index_file = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Rural Coverage Gap Mapper API active."}

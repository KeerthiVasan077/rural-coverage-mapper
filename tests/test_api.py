"""
API Integration and Endpoint unit tests for FastAPI backend.
Tests National Deficit Framework: Zero-Signal Blackspots, Capacity Bottlenecks, and Regional Matrix.
"""

import pytest
from backend.main import (
    get_config,
    get_summary,
    get_gaps,
    get_sites,
    get_towers,
    get_water,
    serve_index,
)


def test_api_config():
    """Verify /api/config returns national benchmark districts and congestion settings."""
    data = get_config()
    assert "district" in data
    assert "districts_list" in data
    assert len(data["districts_list"]) >= 5
    assert "is_demo_mode" in data
    assert data["is_demo_mode"] is True
    assert "congestion" in data


def test_api_summary_dual_deficit():
    """Verify /api/summary returns dual deficit metrics (zero-signal + congestion)."""
    data = get_summary(radius_km=5.0, k=10, capacity_threshold=10000.0)

    assert data["radius_km"] == 5.0
    assert data["k"] == 10
    assert data["total_population"] > 0
    assert "zero_signal_population" in data
    assert "congested_population" in data
    assert "well_served_population" in data
    assert "avg_users_per_tower_before" in data
    assert "avg_users_per_tower_after" in data
    assert data["final_coverage_pct"] >= data["baseline_coverage_pct"]


def test_api_gaps_deficit_filtering():
    """Verify /api/gaps supports deficit_type filtering (all, zero_signal, congested)."""
    # 1. All
    all_gaps = get_gaps(radius_km=5.0, deficit_type="all")
    assert all_gaps["type"] == "FeatureCollection"
    assert len(all_gaps["features"]) > 0

    # 2. Zero-Signal only
    zero_gaps = get_gaps(radius_km=5.0, deficit_type="zero_signal")
    assert all(f["properties"]["gap_type"] == "zero_signal" for f in zero_gaps["features"])

    # 3. Congested only
    congested_gaps = get_gaps(radius_km=5.0, deficit_type="congested")
    assert all(f["properties"]["gap_type"] == "congested" for f in congested_gaps["features"])


def test_api_sites_geojson():
    """Verify /api/sites returns ranked sites with people_gained."""
    data = get_sites(radius_km=5.0, k=5)

    assert data["type"] == "FeatureCollection"
    features = data["features"]
    assert len(features) <= 5

    ranks = [f["properties"]["rank"] for f in features]
    assert ranks == list(range(1, len(features) + 1))
    assert all(f["properties"]["people_gained"] > 0 for f in features)


def test_api_towers_geojson():
    """Verify /api/towers returns existing towers inside district."""
    data = get_towers()

    assert data["type"] == "FeatureCollection"
    assert "features" in data
    assert data["count"] == len(data["features"])
    assert len(data["features"]) > 0

    feat = data["features"][0]
    assert "radio" in feat["properties"]
    assert "mcc" in feat["properties"]


def test_api_water_geojson():
    """Verify /api/water returns water body polygons for candidate exclusion."""
    data = get_water()

    assert data["type"] == "FeatureCollection"
    assert "features" in data
    assert len(data["features"]) > 0
    assert data["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_serve_index():
    """Verify serve_index returns the frontend HTML file."""
    res = serve_index()
    assert hasattr(res, "path")
    assert res.path.endswith("index.html")


def test_api_national_matrix_districts():
    """Verify switching across key Report Benchmark zones (Bihar, Odisha, UP, Meghalaya, Bastar)."""
    benchmarks = [
        ("bihar-sitamarhi", "Bihar"),
        ("odisha-malkangiri", "Odisha"),
        ("up-shravasti", "Uttar Pradesh"),
        ("meghalaya-garo", "Meghalaya"),
        ("chhattisgarh-bastar", "Chhattisgarh"),
    ]

    for dist_id, expected_state in benchmarks:
        summary = get_summary(district_id=dist_id, radius_km=5.0, k=5)
        assert summary["district_id"] == dist_id
        assert summary["state"] == expected_state
        assert summary["total_population"] > 0


def test_api_caching():
    """Verify caching per (district, radius, k, capacity)."""
    res1 = get_summary(district_id="bihar-sitamarhi", radius_km=4.5, k=8, capacity_threshold=10000)
    res2 = get_summary(district_id="bihar-sitamarhi", radius_km=4.5, k=8, capacity_threshold=10000)

    assert res1 == res2

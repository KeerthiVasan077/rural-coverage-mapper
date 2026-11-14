"""
Unit tests for data loaders, demo data generator, and geospatial projections.
"""

import numpy as np
import pytest
from backend.geo import wgs84_to_local_km, local_km_to_wgs84, aggregate_population_grid
from backend.demo_data import generate_demo_dataset
from backend.loaders import load_population_data, load_towers_data, fetch_osm_water_bodies


BBOX = [71.0, 25.5, 71.8, 26.2]


def test_demo_data_generation():
    """Verify synthetic dataset generator produces valid, non-empty structures."""
    pop_df, towers_df, water_multipoly = generate_demo_dataset(BBOX, seed=42)

    # 1. Population checks
    assert not pop_df.empty, "Population dataframe should not be empty"
    assert "lon" in pop_df.columns and "lat" in pop_df.columns and "population" in pop_df.columns
    assert (pop_df["population"] > 0).all(), "All population values must be > 0"
    assert (pop_df["lon"] >= BBOX[0]).all() and (pop_df["lon"] <= BBOX[2]).all()
    assert (pop_df["lat"] >= BBOX[1]).all() and (pop_df["lat"] <= BBOX[3]).all()

    # 2. Towers checks
    assert not towers_df.empty, "Towers dataframe should not be empty"
    assert set(towers_df["radio"].unique()).issubset({"LTE", "UMTS"})
    assert set(towers_df["mcc"].unique()).issubset({404, 405})
    assert (towers_df["lon"] >= BBOX[0]).all() and (towers_df["lon"] <= BBOX[2]).all()
    assert (towers_df["lat"] >= BBOX[1]).all() and (towers_df["lat"] <= BBOX[3]).all()

    # 3. Water body checks
    assert water_multipoly.is_valid, "Water MultiPolygon must be geometrically valid"
    assert not water_multipoly.is_empty, "Water MultiPolygon should have geometries"


def test_load_population_demo_fallback():
    """Verify load_population_data falls back to demo mode when file is absent."""
    lons, lats, pops, is_demo = load_population_data(
        raster_path="data/raw/non_existent_raster.tif",
        bbox=BBOX,
    )
    assert is_demo is True
    assert len(lons) > 0
    assert len(lons) == len(lats) == len(pops)
    assert np.all(pops > 0)


def test_load_towers_demo_fallback():
    """Verify load_towers_data falls back to demo mode when file is absent."""
    towers_df, is_demo = load_towers_data(
        csv_path="data/raw/non_existent_opencellid.csv",
        bbox=BBOX,
    )
    assert is_demo is True
    assert len(towers_df) > 0
    assert "radio" in towers_df.columns
    assert "mcc" in towers_df.columns


def test_geo_equirectangular_roundtrip():
    """Verify forward and inverse equirectangular projection roundtrip."""
    center_lon = 71.4
    center_lat = 25.85

    original_lons = np.array([71.1, 71.4, 71.75])
    original_lats = np.array([25.6, 25.85, 26.15])

    # Forward projection to km
    x_km, y_km = wgs84_to_local_km(original_lons, original_lats, center_lon, center_lat)

    # Origin should project to (0, 0)
    assert pytest.approx(x_km[1], abs=1e-5) == 0.0
    assert pytest.approx(y_km[1], abs=1e-5) == 0.0

    # Inverse projection back to degrees
    roundtrip_lons, roundtrip_lats = local_km_to_wgs84(x_km, y_km, center_lon, center_lat)

    np.testing.assert_allclose(roundtrip_lons, original_lons, atol=1e-6)
    np.testing.assert_allclose(roundtrip_lats, original_lats, atol=1e-6)


def test_population_aggregation_preserves_total():
    """Verify population aggregation preserves total population exactly."""
    center_lon = 71.4
    center_lat = 25.85

    # 100 points around origin
    rng = np.random.default_rng(10)
    lons = center_lon + rng.uniform(-0.05, 0.05, 100)
    lats = center_lat + rng.uniform(-0.05, 0.05, 100)
    pops = rng.uniform(10.0, 50.0, 100)
    total_original = np.sum(pops)

    agg_lons, agg_lats, agg_pops = aggregate_population_grid(
        lons, lats, pops, grid_step_km=0.5, center_lon=center_lon, center_lat=center_lat
    )

    assert len(agg_pops) <= len(pops)
    assert pytest.approx(np.sum(agg_pops), rel=1e-6) == total_original

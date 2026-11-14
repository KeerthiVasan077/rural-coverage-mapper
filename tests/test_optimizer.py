"""
Unit tests for the Vectorized Spatial Coverage and Greedy Maximum Coverage Optimizer.

Covers:
1. Tiny hand-made case with an exact, mathematically known answer.
2. Verification that greedy selection strictly avoids double-counting population.
3. Verification that coverage percentage is monotonic non-decreasing in K.
4. Water body candidate exclusion via Shapely geometries.
5. Radio radius override testing (LTE vs UMTS).
"""

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon, MultiPolygon

from backend.geo import wgs84_to_local_km
from backend.demo_data import generate_demo_dataset
from backend.optimizer import (
    calculate_baseline_coverage,
    generate_candidate_sites,
    run_greedy_max_coverage,
)


def test_tiny_hand_made_case_with_known_answer():
    """
    Hand-crafted test scenario with mathematically proven ground truth:
    Origin at (71.0, 26.0).
    Populations:
      - P1 at (71.0, 26.0): pop = 1000
      - P2 at (71.018, 26.0): pop = 600 (~1.8 km east of P1)
      - P3 at (71.0, 26.09): pop = 800 (~10.0 km north of P1)
      - P4 at (71.09, 26.0): pop = 300 (~9.0 km east of P1)
    Existing Tower:
      - T1 at (71.09, 26.0): radius 5.0 km (covers P4, distance 0 km)
    Expected Behavior:
      - Total Pop = 2700
      - Baseline covered = 300 (P4), baseline coverage % = 11.111%
      - Uncovered pop = 2400 (P1, P2, P3)
      - With R = 5.0 km:
          * Candidate at P1 reaches P1 and P2 (dist ~1.8 km <= 5 km) -> gain = 1600
          * Candidate at P3 reaches only P3 -> gain = 800
      - Step 1 (Rank 1): Must pick P1 (or P2), gain = 1600.
      - Step 2 (Rank 2): Must pick P3, gain = 800.
      - Total final coverage = 300 + 1600 + 800 = 2700 (100.0%).
    """
    center_lon, center_lat = 71.0, 26.0

    pop_lons = np.array([71.0, 71.018, 71.0, 71.09], dtype=np.float64)
    pop_lats = np.array([26.0, 26.0, 26.09, 26.0], dtype=np.float64)
    pop_counts = np.array([1000.0, 600.0, 800.0, 300.0], dtype=np.float64)

    towers_df = pd.DataFrame(
        [{"radio": "LTE", "mcc": 404, "net": 45, "lon": 71.09, "lat": 26.0, "range": 5000}]
    )

    # 1. Baseline coverage
    baseline = calculate_baseline_coverage(
        pop_lons,
        pop_lats,
        pop_counts,
        towers_df,
        center_lon,
        center_lat,
        default_radius_km=5.0,
    )

    assert baseline.total_pop == 2700.0
    assert pytest.approx(baseline.covered_pop, abs=1.0) == 300.0
    assert pytest.approx(baseline.uncovered_pop, abs=1.0) == 2400.0
    assert pytest.approx(baseline.coverage_pct, abs=0.01) == (300.0 / 2700.0 * 100.0)

    # 2. Candidate generation from uncovered pixels
    c_lons, c_lats, c_x, c_y = generate_candidate_sites(
        baseline.uncovered_lons,
        baseline.uncovered_lats,
        baseline.uncovered_pops,
        baseline.uncovered_x_km,
        baseline.uncovered_y_km,
    )
    assert len(c_lons) == 3  # P1, P2, P3 are uncovered

    # 3. Greedy Maximum Coverage
    res = run_greedy_max_coverage(
        uncovered_lons=baseline.uncovered_lons,
        uncovered_lats=baseline.uncovered_lats,
        uncovered_pops=baseline.uncovered_pops,
        uncovered_x_km=baseline.uncovered_x_km,
        uncovered_y_km=baseline.uncovered_y_km,
        cand_lons=c_lons,
        cand_lats=c_lats,
        cand_x_km=c_x,
        cand_y_km=c_y,
        k=2,
        radius_km=5.0,
        initial_covered_pop=baseline.covered_pop,
        total_pop=baseline.total_pop,
    )

    assert len(res.sites) == 2
    # Rank 1 must cover 1600 people (P1 + P2)
    assert pytest.approx(res.sites[0].people_gained, abs=1.0) == 1600.0
    assert res.sites[0].rank == 1

    # Rank 2 must cover remaining 800 people (P3)
    assert pytest.approx(res.sites[1].people_gained, abs=1.0) == 800.0
    assert res.sites[1].rank == 2

    # Final covered population must equal 100%
    assert pytest.approx(res.final_covered_pop, abs=1.0) == 2700.0
    assert pytest.approx(res.final_coverage_pct, abs=0.01) == 100.0


def test_greedy_never_double_counts():
    """
    Verify that across multiple overlapping candidate sites,
    the greedy optimizer NEVER double-counts population.
    The sum of individual site gains must strictly equal total people gained.
    """
    bbox = [71.0, 25.5, 71.8, 26.2]
    pop_df, towers_df, _ = generate_demo_dataset(bbox, seed=99)
    center_lon = (bbox[0] + bbox[2]) / 2.0
    center_lat = (bbox[1] + bbox[3]) / 2.0

    baseline = calculate_baseline_coverage(
        pop_df["lon"].to_numpy(),
        pop_df["lat"].to_numpy(),
        pop_df["population"].to_numpy(),
        towers_df,
        center_lon,
        center_lat,
        default_radius_km=5.0,
    )

    c_lons, c_lats, c_x, c_y = generate_candidate_sites(
        baseline.uncovered_lons,
        baseline.uncovered_lats,
        baseline.uncovered_pops,
        baseline.uncovered_x_km,
        baseline.uncovered_y_km,
        max_candidates=500,
    )

    # Run for K = 15
    res = run_greedy_max_coverage(
        baseline.uncovered_lons,
        baseline.uncovered_lats,
        baseline.uncovered_pops,
        baseline.uncovered_x_km,
        baseline.uncovered_y_km,
        c_lons,
        c_lats,
        c_x,
        c_y,
        k=15,
        radius_km=5.0,
        initial_covered_pop=baseline.covered_pop,
        total_pop=baseline.total_pop,
    )

    sum_individual_gains = sum(s.people_gained for s in res.sites)

    # 1. Sum of individual site gains equals people_gained_total
    assert pytest.approx(sum_individual_gains, rel=1e-5) == res.people_gained_total

    # 2. Final covered pop equals initial covered pop + people_gained_total
    assert pytest.approx(res.final_covered_pop, rel=1e-5) == (
        res.initial_covered_pop + res.people_gained_total
    )

    # 3. People gained total cannot exceed total uncovered pop
    assert res.people_gained_total <= (baseline.uncovered_pop + 1e-5)


def test_monotonic_non_decreasing_coverage():
    """
    Verify that coverage percentage is strictly non-decreasing with increasing K.
    """
    bbox = [71.0, 25.5, 71.8, 26.2]
    pop_df, towers_df, _ = generate_demo_dataset(bbox, seed=123)
    center_lon = (bbox[0] + bbox[2]) / 2.0
    center_lat = (bbox[1] + bbox[3]) / 2.0

    baseline = calculate_baseline_coverage(
        pop_df["lon"].to_numpy(),
        pop_df["lat"].to_numpy(),
        pop_df["population"].to_numpy(),
        towers_df,
        center_lon,
        center_lat,
        default_radius_km=5.0,
    )

    c_lons, c_lats, c_x, c_y = generate_candidate_sites(
        baseline.uncovered_lons,
        baseline.uncovered_lats,
        baseline.uncovered_pops,
        baseline.uncovered_x_km,
        baseline.uncovered_y_km,
    )

    res = run_greedy_max_coverage(
        baseline.uncovered_lons,
        baseline.uncovered_lats,
        baseline.uncovered_pops,
        baseline.uncovered_x_km,
        baseline.uncovered_y_km,
        c_lons,
        c_lats,
        c_x,
        c_y,
        k=12,
        radius_km=5.0,
        initial_covered_pop=baseline.covered_pop,
        total_pop=baseline.total_pop,
    )

    # Step-by-step monotonicity
    for i in range(1, len(res.sites)):
        prev_pct = res.sites[i - 1].cumulative_coverage_pct
        curr_pct = res.sites[i].cumulative_coverage_pct
        assert curr_pct >= prev_pct - 1e-9, f"Coverage decreased from {prev_pct} to {curr_pct}"


def test_water_body_exclusion():
    """Verify candidate sites intersecting water polygons are dropped."""
    uncovered_lons = np.array([71.2, 71.5, 71.7])
    uncovered_lats = np.array([25.8, 25.8, 25.8])
    uncovered_pops = np.array([500.0, 500.0, 500.0])
    uncovered_x = np.array([-20.0, 10.0, 30.0])
    uncovered_y = np.array([0.0, 0.0, 0.0])

    # Water polygon directly covering the middle candidate (71.5, 25.8)
    water_poly = Polygon(
        [(71.45, 25.75), (71.55, 25.75), (71.55, 25.85), (71.45, 25.85)]
    )
    water_multi = MultiPolygon([water_poly])

    c_lons, c_lats, c_x, c_y = generate_candidate_sites(
        uncovered_lons,
        uncovered_lats,
        uncovered_pops,
        uncovered_x,
        uncovered_y,
        water_bodies=water_multi,
    )

    # Middle candidate at 71.5 should be excluded
    assert len(c_lons) == 2
    assert 71.5 not in c_lons
    assert pytest.approx(c_lons[0], abs=1e-4) == 71.2
    assert pytest.approx(c_lons[1], abs=1e-4) == 71.7


def test_radio_radius_override():
    """Verify per-radio radius overrides (e.g. LTE 5km vs UMTS 6km)."""
    center_lon, center_lat = 71.0, 26.0

    # Pop pixel at 5.5 km north (0.0494 deg)
    pop_lons = np.array([71.0])
    pop_lats = np.array([26.0494])  # ~5.49 km north
    pop_counts = np.array([500.0])

    # Case 1: Tower is LTE with default 5.0 km radius -> Pixel should be UNCOVERED (5.49 > 5.0)
    lte_tower = pd.DataFrame([{"radio": "LTE", "lon": 71.0, "lat": 26.0}])
    b1 = calculate_baseline_coverage(
        pop_lons,
        pop_lats,
        pop_counts,
        lte_tower,
        center_lon,
        center_lat,
        default_radius_km=5.0,
        radio_radii={"LTE": 5.0, "UMTS": 6.0},
    )
    assert b1.covered_pop == 0.0

    # Case 2: Tower is UMTS with 6.0 km radius -> Pixel should be COVERED (5.49 <= 6.0)
    umts_tower = pd.DataFrame([{"radio": "UMTS", "lon": 71.0, "lat": 26.0}])
    b2 = calculate_baseline_coverage(
        pop_lons,
        pop_lats,
        pop_counts,
        umts_tower,
        center_lon,
        center_lat,
        default_radius_km=5.0,
        radio_radii={"LTE": 5.0, "UMTS": 6.0},
    )
    assert b2.covered_pop == 500.0

"""
Synthetic demo dataset generator for Rural Coverage Gap Mapper.

When raw WorldPop (ind_pop.tif) or OpenCellID (opencellid_india.csv) files
are not present in data/raw/, this module provides a realistic synthetic
rural district environment with:
- Village population clusters (Gaussian settlements around rural nodes).
- Existing telecommunication towers (LTE / UMTS, MCC 404/405).
- A synthetic water body (river / reservoir) to demonstrate OSM candidate exclusion.
"""

from typing import Dict, List, Tuple
import numpy as np
import pandas as pd
from shapely.geometry import Polygon, MultiPolygon


def generate_demo_dataset(
    bbox: List[float],
    seed: int = 42,
) -> Tuple[pd.DataFrame, pd.DataFrame, MultiPolygon]:
    """
    Generates synthetic population points, existing towers, and water bodies.

    Args:
        bbox: [min_lon, min_lat, max_lon, max_lat]
        seed: Random seed for deterministic generation.

    Returns:
        Tuple of:
            - pop_df: DataFrame with columns ['lon', 'lat', 'population']
            - towers_df: DataFrame with columns ['radio', 'mcc', 'net', 'lon', 'lat', 'range']
            - water_multipoly: Shapely MultiPolygon representing water bodies in the district.
    """
    rng = np.random.default_rng(seed)
    min_lon, min_lat, max_lon, max_lat = bbox

    lon_span = max_lon - min_lon
    lat_span = max_lat - min_lat

    # 1. Generate 14 Village Centers with distinct population sizes
    num_villages = 14
    # Village center coordinates placed within the inner 80% of the bbox
    v_lons = min_lon + lon_span * (0.1 + 0.8 * rng.random(num_villages))
    v_lats = min_lat + lat_span * (0.1 + 0.8 * rng.random(num_villages))

    # Village total populations (from smaller hamlets of 1,200 to major panchayats of 15,000)
    village_pops = rng.integers(1200, 15000, size=num_villages)

    # For each village, generate dispersed pixel clusters (approx 100m - 300m spread)
    all_lons = []
    all_lats = []
    all_pops = []

    for vl_lon, vl_lat, v_pop in zip(v_lons, v_lats, village_pops):
        # Number of pixels making up this village settlement
        n_pixels = int(rng.integers(30, 90))
        # Spatial standard deviation in degrees (~0.006 deg ~= 650 meters)
        spread = rng.uniform(0.003, 0.008)

        px_lons = rng.normal(vl_lon, spread, n_pixels)
        px_lats = rng.normal(vl_lat, spread, n_pixels)

        # Distribute population across pixels using Dirichlet / exponential distribution
        weights = rng.exponential(scale=1.0, size=n_pixels)
        weights = weights / np.sum(weights)
        px_pops = np.round(weights * v_pop).astype(np.float64)

        # Ensure positive population
        px_pops[px_pops < 1.0] = 1.0

        # Keep pixels within bbox
        in_bounds = (
            (px_lons >= min_lon)
            & (px_lons <= max_lon)
            & (px_lats >= min_lat)
            & (px_lats <= max_lat)
        )

        all_lons.extend(px_lons[in_bounds])
        all_lats.extend(px_lats[in_bounds])
        all_pops.extend(px_pops[in_bounds])

    # Add sparse background hamlets (isolated homes / dhans common in rural India)
    n_sparse = 120
    sp_lons = rng.uniform(min_lon, max_lon, n_sparse)
    sp_lats = rng.uniform(min_lat, max_lat, n_sparse)
    sp_pops = rng.uniform(5.0, 45.0, n_sparse)

    all_lons.extend(sp_lons)
    all_lats.extend(sp_lats)
    all_pops.extend(sp_pops)

    pop_df = pd.DataFrame(
        {
            "lon": np.round(all_lons, 5),
            "lat": np.round(all_lats, 5),
            "population": np.round(all_pops, 1),
        }
    )

    # 2. Generate Existing Mobile Towers
    # Place 7 existing towers clustered near only 4 of the villages (leaving 10 villages underserved)
    tower_records = []
    mcc = 404  # India MCC

    # Cover villages index 0, 1, 2, 3 (hub corridor)
    for i in range(min(4, num_villages)):
        t_lon = v_lons[i] + rng.uniform(-0.008, 0.008)
        t_lat = v_lats[i] + rng.uniform(-0.008, 0.008)
        tower_records.append(
            {
                "radio": "LTE",
                "mcc": mcc,
                "net": int(rng.choice([10, 45, 860])),  # Airtel / Jio / Vi
                "lon": round(float(t_lon), 5),
                "lat": round(float(t_lat), 5),
                "range": 5000,
            }
        )

    # Add 3 more towers near highway/corridor
    corridor_lons = np.linspace(min_lon + 0.15 * lon_span, min_lon + 0.45 * lon_span, 3)
    corridor_lats = np.linspace(min_lat + 0.20 * lat_span, min_lat + 0.50 * lat_span, 3)
    for c_lon, c_lat in zip(corridor_lons, corridor_lats):
        tower_records.append(
            {
                "radio": rng.choice(["LTE", "UMTS"]),
                "mcc": 405 if rng.random() > 0.5 else 404,
                "net": int(rng.choice([10, 45, 860])),
                "lon": round(float(c_lon), 5),
                "lat": round(float(c_lat), 5),
                "range": 6000 if rng.random() > 0.5 else 5000,
            }
        )

    towers_df = pd.DataFrame(tower_records)

    # 3. Generate Synthetic Water Body (Seasonal River / Lake Reservoir)
    # A meandering river channel crossing part of the district
    river_x = np.linspace(min_lon + 0.55 * lon_span, min_lon + 0.75 * lon_span, 6)
    river_y = (
        min_lat
        + 0.3 * lat_span
        + 0.15 * lat_span * np.sin(np.linspace(0, np.pi * 1.5, 6))
    )
    # Build a small polygon strip
    w_pts_top = [(x, y + 0.008) for x, y in zip(river_x, river_y)]
    w_pts_bottom = [(x, y - 0.008) for x, y in reversed(list(zip(river_x, river_y)))]
    water_poly = Polygon(w_pts_top + w_pts_bottom)
    water_multipoly = MultiPolygon([water_poly])

    return pop_df, towers_df, water_multipoly

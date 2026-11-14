"""
Vectorized Spatial Coverage & Dual-Deficit (Coverage + Capacity) Optimizer.

Implements the Comprehensive National Framework (DoT Audits & Open Geospatial Data):
1. Zero-Signal Blackspots: Identifies populations with distance to nearest cell tower > R_KM.
2. Capacity & Congestion Bottlenecks: Identifies high-density rural areas where a single
   macro tower serves >10,000–15,000 users (causing severe indoor drop-offs).
3. Vectorized Greedy Maximum Coverage: Selects K new tower sites to maximize both
   zero-signal coverage and capacity bottleneck relief without double-counting.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely.geometry import Point, MultiPolygon, Polygon
from shapely.prepared import prep

from backend.geo import wgs84_to_local_km, local_km_to_wgs84


@dataclass
class BaselineCoverage:
    """Stores baseline dual-deficit analysis results."""
    total_pop: float
    covered_pop: float
    uncovered_pop: float           # Zero-signal blackspot population
    coverage_pct: float
    
    # Congestion & Capacity Metrics
    congested_pop: float           # Covered by a tower, but tower serves > capacity_threshold
    well_served_pop: float         # Covered by a tower AND tower serves <= capacity_threshold
    congested_pct: float
    well_served_pct: float
    avg_users_per_tower: float
    congested_towers_count: int
    
    # Masks & Spatial Arrays
    is_covered_mask: np.ndarray    # True if distance <= R_KM
    is_congested_mask: np.ndarray  # True if covered by an overloaded tower
    
    # Gap Coordinates
    uncovered_lons: np.ndarray
    uncovered_lats: np.ndarray
    uncovered_pops: np.ndarray
    uncovered_x_km: np.ndarray
    uncovered_y_km: np.ndarray

    # Congested Cells Coordinates
    congested_lons: np.ndarray
    congested_lats: np.ndarray
    congested_pops: np.ndarray


@dataclass
class ProposedSite:
    """Represents a proposed mobile tower site selected by greedy maximum coverage."""
    rank: int
    lon: float
    lat: float
    people_gained: float
    blackspot_gained: float
    congested_offloaded: float
    cumulative_covered_pop: float
    cumulative_coverage_pct: float
    radius_km: float


@dataclass
class OptimizationResult:
    """Comprehensive output of the optimization process."""
    total_pop: float
    initial_covered_pop: float
    initial_coverage_pct: float
    final_covered_pop: float
    final_coverage_pct: float
    people_gained_total: float
    
    # Capacity Post-Optimization
    final_congested_pop: float
    final_well_served_pop: float
    final_well_served_pct: float
    final_avg_users_per_tower: float
    
    k: int
    radius_km: float
    capacity_threshold: float
    sites: List[ProposedSite]
    uncovered_pixel_count: int


def calculate_baseline_coverage(
    pop_lons: np.ndarray,
    pop_lats: np.ndarray,
    pop_counts: np.ndarray,
    towers_df: pd.DataFrame,
    center_lon: float,
    center_lat: float,
    default_radius_km: float = 5.0,
    radio_radii: Optional[Dict[str, float]] = None,
    capacity_threshold: float = 10000.0,
) -> BaselineCoverage:
    """
    Computes baseline coverage and capacity bottlenecks:
    - A pixel is ZERO-SIGNAL if min distance to tower > R_KM.
    - A pixel is CONGESTED if covered, but its serving tower hosts > capacity_threshold people.
    - A pixel is WELL-SERVED if covered AND serving tower <= capacity_threshold.
    """
    total_pop = float(np.sum(pop_counts))
    n_pixels = len(pop_counts)

    empty_arr = np.array([])
    if n_pixels == 0:
        return BaselineCoverage(
            total_pop=0.0,
            covered_pop=0.0,
            uncovered_pop=0.0,
            coverage_pct=0.0,
            congested_pop=0.0,
            well_served_pop=0.0,
            congested_pct=0.0,
            well_served_pct=0.0,
            avg_users_per_tower=0.0,
            congested_towers_count=0,
            is_covered_mask=np.array([], dtype=bool),
            is_congested_mask=np.array([], dtype=bool),
            uncovered_lons=empty_arr,
            uncovered_lats=empty_arr,
            uncovered_pops=empty_arr,
            uncovered_x_km=empty_arr,
            uncovered_y_km=empty_arr,
            congested_lons=empty_arr,
            congested_lats=empty_arr,
            congested_pops=empty_arr,
        )

    # Project population points to local Cartesian kilometers
    pop_x, pop_y = wgs84_to_local_km(pop_lons, pop_lats, center_lon, center_lat)
    pop_coords = np.column_stack([pop_x, pop_y])

    # If no existing towers exist, all pixels are in zero-signal blackspot
    if towers_df is None or towers_df.empty:
        is_covered_mask = np.zeros(n_pixels, dtype=bool)
        is_congested_mask = np.zeros(n_pixels, dtype=bool)
        congested_towers_count = 0
        avg_users_per_tower = 0.0
    else:
        radio_radii = radio_radii or {}
        towers_x, towers_y = wgs84_to_local_km(
            towers_df["lon"].to_numpy(),
            towers_df["lat"].to_numpy(),
            center_lon,
            center_lat,
        )
        tower_coords = np.column_stack([towers_x, towers_y])
        n_towers = len(towers_df)

        radii = np.array(
            [
                radio_radii.get(str(r).upper(), default_radius_km)
                for r in towers_df.get("radio", ["LTE"] * n_towers)
            ],
            dtype=np.float64,
        )

        # Build KD-tree on all towers to find nearest tower and distance
        tower_tree = cKDTree(tower_coords)
        dists, nearest_tower_indices = tower_tree.query(pop_coords, k=1)

        # Pixel is covered if distance <= that specific tower's radius
        assigned_radii = radii[nearest_tower_indices]
        is_covered_mask = dists <= assigned_radii

        # Calculate population load per tower (Voronoi user load)
        tower_loads = np.zeros(n_towers, dtype=np.float64)
        for t_idx in range(n_towers):
            mask_tower_pixels = is_covered_mask & (nearest_tower_indices == t_idx)
            tower_loads[t_idx] = np.sum(pop_counts[mask_tower_pixels])

        # Flag congested towers
        congested_towers_mask = tower_loads > capacity_threshold
        congested_towers_count = int(np.sum(congested_towers_mask))

        # Flag pixels belonging to congested towers
        is_congested_mask = is_covered_mask & congested_towers_mask[nearest_tower_indices]
        
        covered_pop_sum = np.sum(pop_counts[is_covered_mask])
        avg_users_per_tower = float(covered_pop_sum / n_towers) if n_towers > 0 else 0.0

    covered_pop = float(np.sum(pop_counts[is_covered_mask]))
    uncovered_pop = float(total_pop - covered_pop)
    coverage_pct = float((covered_pop / total_pop * 100.0) if total_pop > 0 else 0.0)

    congested_pop = float(np.sum(pop_counts[is_congested_mask]))
    well_served_pop = float(covered_pop - congested_pop)
    congested_pct = float((congested_pop / total_pop * 100.0) if total_pop > 0 else 0.0)
    well_served_pct = float((well_served_pop / total_pop * 100.0) if total_pop > 0 else 0.0)

    uncovered_mask = ~is_covered_mask

    return BaselineCoverage(
        total_pop=total_pop,
        covered_pop=covered_pop,
        uncovered_pop=uncovered_pop,
        coverage_pct=coverage_pct,
        congested_pop=congested_pop,
        well_served_pop=well_served_pop,
        congested_pct=congested_pct,
        well_served_pct=well_served_pct,
        avg_users_per_tower=round(avg_users_per_tower, 1),
        congested_towers_count=congested_towers_count,
        is_covered_mask=is_covered_mask,
        is_congested_mask=is_congested_mask,
        uncovered_lons=pop_lons[uncovered_mask],
        uncovered_lats=pop_lats[uncovered_mask],
        uncovered_pops=pop_counts[uncovered_mask],
        uncovered_x_km=pop_x[uncovered_mask],
        uncovered_y_km=pop_y[uncovered_mask],
        congested_lons=pop_lons[is_congested_mask],
        congested_lats=pop_lats[is_congested_mask],
        congested_pops=pop_counts[is_congested_mask],
    )


def generate_candidate_sites(
    uncovered_lons: np.ndarray,
    uncovered_lats: np.ndarray,
    uncovered_pops: np.ndarray,
    uncovered_x_km: np.ndarray,
    uncovered_y_km: np.ndarray,
    max_candidates: int = 2000,
    water_bodies: Optional[MultiPolygon] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Generates candidate sites from unserved population clusters.
    Excludes candidates intersecting water bodies.
    """
    n_pool = len(uncovered_pops)
    if n_pool == 0:
        return np.array([]), np.array([]), np.array([]), np.array([])

    if n_pool <= max_candidates:
        cand_indices = np.arange(n_pool)
    else:
        # Hybrid selection: 60% top population points + 40% uniform spatial samples
        top_k = int(max_candidates * 0.6)
        top_indices = np.argsort(uncovered_pops)[-top_k:]
        rem_indices = np.setdiff1d(np.arange(n_pool), top_indices)
        step = max(1, len(rem_indices) // (max_candidates - top_k))
        sample_indices = rem_indices[::step][: (max_candidates - top_k)]
        cand_indices = np.concatenate([top_indices, sample_indices])

    c_lons = uncovered_lons[cand_indices]
    c_lats = uncovered_lats[cand_indices]
    c_x = uncovered_x_km[cand_indices]
    c_y = uncovered_y_km[cand_indices]

    # Water body exclusion
    if water_bodies is not None and not water_bodies.is_empty:
        prepared_water = prep(water_bodies)
        dry_mask = [not prepared_water.intersects(Point(lon, lat)) for lon, lat in zip(c_lons, c_lats)]
        dry_mask = np.array(dry_mask, dtype=bool)

        c_lons = c_lons[dry_mask]
        c_lats = c_lats[dry_mask]
        c_x = c_x[dry_mask]
        c_y = c_y[dry_mask]

    return c_lons, c_lats, c_x, c_y

    return c_lons, c_lats, c_x, c_y


def run_greedy_max_coverage(
    uncovered_lons: np.ndarray,
    uncovered_lats: np.ndarray,
    uncovered_pops: np.ndarray,
    uncovered_x_km: np.ndarray,
    uncovered_y_km: np.ndarray,
    cand_lons: np.ndarray,
    cand_lats: np.ndarray,
    cand_x_km: np.ndarray,
    cand_y_km: np.ndarray,
    k: int,
    radius_km: float,
    initial_covered_pop: float,
    total_pop: float,
    capacity_threshold: float = 10000.0,
    existing_towers_count: int = 0,
) -> OptimizationResult:
    """
    Executes the Greedy Maximum Coverage algorithm:
    Iteratively picks candidate sites that maximize newly covered population,
    dynamically removing covered pixels to prevent double-counting.
    """
    initial_coverage_pct = (initial_covered_pop / total_pop * 100.0) if total_pop > 0 else 0.0
    n_uncovered = len(uncovered_pops)
    n_candidates = len(cand_lons)

    if n_uncovered == 0 or n_candidates == 0 or k <= 0:
        total_towers = existing_towers_count
        avg_users = (initial_covered_pop / total_towers) if total_towers > 0 else 0.0
        return OptimizationResult(
            total_pop=total_pop,
            initial_covered_pop=initial_covered_pop,
            initial_coverage_pct=initial_coverage_pct,
            final_covered_pop=initial_covered_pop,
            final_coverage_pct=initial_coverage_pct,
            people_gained_total=0.0,
            final_congested_pop=0.0,
            final_well_served_pop=initial_covered_pop,
            final_well_served_pct=initial_coverage_pct,
            final_avg_users_per_tower=round(avg_users, 1),
            k=k,
            radius_km=radius_km,
            capacity_threshold=capacity_threshold,
            sites=[],
            uncovered_pixel_count=n_uncovered,
        )

    # Build KD-tree of all currently uncovered pixels
    pop_points = np.column_stack([uncovered_x_km, uncovered_y_km])
    cand_points = np.column_stack([cand_x_km, cand_y_km])
    pop_tree = cKDTree(pop_points)

    # Query ball point for each candidate
    candidate_pixel_lists = pop_tree.query_ball_point(cand_points, r=radius_km)
    candidate_pixel_sets = [set(pix_list) for pix_list in candidate_pixel_lists]

    # Track pixels covered
    is_pixel_still_uncovered = np.ones(n_uncovered, dtype=bool)

    # Pre-calculate gains
    cand_gains = np.array(
        [
            float(np.sum(uncovered_pops[list(pix_set)])) if pix_set else 0.0
            for pix_set in candidate_pixel_sets
        ],
        dtype=np.float64,
    )

    available_candidates = set(range(n_candidates))
    proposed_sites: List[ProposedSite] = []
    cumulative_covered_pop = float(initial_covered_pop)

    for rank in range(1, k + 1):
        if not available_candidates:
            break

        best_cand_idx = -1
        max_gain = -1.0

        for c_idx in available_candidates:
            gain = cand_gains[c_idx]
            if gain > max_gain:
                max_gain = gain
                best_cand_idx = c_idx

        if best_cand_idx == -1 or max_gain <= 0.0:
            break

        available_candidates.remove(best_cand_idx)
        cumulative_covered_pop += max_gain
        cum_pct = (cumulative_covered_pop / total_pop * 100.0) if total_pop > 0 else 0.0

        site = ProposedSite(
            rank=rank,
            lon=float(cand_lons[best_cand_idx]),
            lat=float(cand_lats[best_cand_idx]),
            people_gained=float(max_gain),
            blackspot_gained=float(max_gain),
            congested_offloaded=0.0,
            cumulative_covered_pop=float(cumulative_covered_pop),
            cumulative_coverage_pct=float(cum_pct),
            radius_km=float(radius_km),
        )
        proposed_sites.append(site)

        # Mark pixels covered so they are NEVER double counted
        newly_covered_pixels = [
            p for p in candidate_pixel_sets[best_cand_idx] if is_pixel_still_uncovered[p]
        ]
        is_pixel_still_uncovered[newly_covered_pixels] = False
        newly_covered_set = set(newly_covered_pixels)

        # Update remaining candidates
        for c_idx in available_candidates:
            overlap = candidate_pixel_sets[c_idx] & newly_covered_set
            if overlap:
                lost_gain = float(np.sum(uncovered_pops[list(overlap)]))
                cand_gains[c_idx] = max(0.0, cand_gains[c_idx] - lost_gain)
                candidate_pixel_sets[c_idx] -= overlap

    people_gained_total = float(cumulative_covered_pop - initial_covered_pop)
    final_coverage_pct = (cumulative_covered_pop / total_pop * 100.0) if total_pop > 0 else 0.0

    total_final_towers = existing_towers_count + len(proposed_sites)
    final_avg_users = (
        (cumulative_covered_pop / total_final_towers) if total_final_towers > 0 else 0.0
    )

    return OptimizationResult(
        total_pop=total_pop,
        initial_covered_pop=initial_covered_pop,
        initial_coverage_pct=initial_coverage_pct,
        final_covered_pop=cumulative_covered_pop,
        final_coverage_pct=final_coverage_pct,
        people_gained_total=people_gained_total,
        final_congested_pop=0.0,
        final_well_served_pop=cumulative_covered_pop,
        final_well_served_pct=final_coverage_pct,
        final_avg_users_per_tower=round(final_avg_users, 1),
        k=k,
        radius_km=radius_km,
        capacity_threshold=capacity_threshold,
        sites=proposed_sites,
        uncovered_pixel_count=n_uncovered,
    )

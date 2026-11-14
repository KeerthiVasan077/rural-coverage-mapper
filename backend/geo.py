"""
Geospatial coordinate conversions and spatial aggregation utilities.

Provides:
- Equirectangular projection between WGS84 (lon, lat) and local Cartesian kilometers (x, y).
- Population pixel aggregation to a coarser grid (e.g., 500 m) when pixel density is high.
"""

from typing import Tuple
import numpy as np

# Mean Earth radius in kilometers (WGS84 spherical approximation)
EARTH_RADIUS_KM: float = 6371.0088


def wgs84_to_local_km(
    lons: np.ndarray,
    lats: np.ndarray,
    center_lon: float,
    center_lat: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Projects WGS84 geographic coordinates (longitude, latitude in degrees)
    to local Cartesian coordinates (x, y in kilometers) using an Equirectangular
    projection centered on (center_lon, center_lat).

    Formulas:
        x = R * (lon - lon_0) * cos(lat_0_rad) * (pi / 180)
        y = R * (lat - lat_0) * (pi / 180)

    Args:
        lons: Array or float of longitudes in degrees.
        lats: Array or float of latitudes in degrees.
        center_lon: Longitude of the district center / projection origin.
        center_lat: Latitude of the district center / projection origin.

    Returns:
        Tuple of (x_km, y_km) relative to the projection origin.
    """
    lons = np.asarray(lons, dtype=np.float64)
    lats = np.asarray(lats, dtype=np.float64)

    center_lat_rad = np.radians(center_lat)
    deg_to_rad = np.pi / 180.0

    # Easting (x) in kilometers: scaled by cosine of the center latitude
    x_km = EARTH_RADIUS_KM * (lons - center_lon) * np.cos(center_lat_rad) * deg_to_rad
    # Northing (y) in kilometers
    y_km = EARTH_RADIUS_KM * (lats - center_lat) * deg_to_rad

    return x_km, y_km


def local_km_to_wgs84(
    x_km: np.ndarray,
    y_km: np.ndarray,
    center_lon: float,
    center_lat: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Inverse equirectangular projection: converts local Cartesian coordinates (x, y in km)
    back to WGS84 geographic coordinates (longitude, latitude in degrees).

    Formulas:
        lon = lon_0 + (x / (R * cos(lat_0_rad))) * (180 / pi)
        lat = lat_0 + (y / R) * (180 / pi)

    Args:
        x_km: Array or float of easting distances in km.
        y_km: Array or float of northing distances in km.
        center_lon: Longitude of projection origin.
        center_lat: Latitude of projection origin.

    Returns:
        Tuple of (lons, lats) in degrees.
    """
    x_km = np.asarray(x_km, dtype=np.float64)
    y_km = np.asarray(y_km, dtype=np.float64)

    center_lat_rad = np.radians(center_lat)
    rad_to_deg = 180.0 / np.pi

    cos_lat = np.cos(center_lat_rad)
    # Avoid zero division in edge cases (near poles)
    if np.abs(cos_lat) < 1e-7:
        cos_lat = 1e-7

    lons = center_lon + (x_km / (EARTH_RADIUS_KM * cos_lat)) * rad_to_deg
    lats = center_lat + (y_km / EARTH_RADIUS_KM) * rad_to_deg

    return lons, lats


def aggregate_population_grid(
    lons: np.ndarray,
    lats: np.ndarray,
    pops: np.ndarray,
    grid_step_km: float,
    center_lon: float,
    center_lat: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Aggregates dense population points to a regular Cartesian grid (e.g. 500m cells)
    in local kilometers to maintain fast performance while strictly preserving total population.

    Args:
        lons: Longitude array of pixel centroids.
        lats: Latitude array of pixel centroids.
        pops: Population counts per pixel.
        grid_step_km: Cell resolution in km (e.g., 0.5 for 500 m).
        center_lon: District center longitude.
        center_lat: District center latitude.

    Returns:
        Tuple of (agg_lons, agg_lats, agg_pops) for non-empty aggregated cells.
    """
    if len(pops) == 0:
        return np.array([]), np.array([]), np.array([])

    x_km, y_km = wgs84_to_local_km(lons, lats, center_lon, center_lat)

    # Bin coordinates into integer grid indices
    grid_x = np.floor(x_km / grid_step_km).astype(np.int64)
    grid_y = np.floor(y_km / grid_step_km).astype(np.int64)

    # Unique 2D grid cell identification
    # Pack grid_x and grid_y into a single 64-bit key or structured array
    keys = np.stack([grid_x, grid_y], axis=1)
    unique_keys, inverse_indices = np.unique(keys, axis=0, return_inverse=True)

    # Sum population in each unique cell
    agg_pops = np.bincount(inverse_indices, weights=pops)

    # Compute cell centers in km (half-step offset)
    cell_center_x = (unique_keys[:, 0] + 0.5) * grid_step_km
    cell_center_y = (unique_keys[:, 1] + 0.5) * grid_step_km

    # Project cell centers back to WGS84 degrees
    agg_lons, agg_lats = local_km_to_wgs84(
        cell_center_x, cell_center_y, center_lon, center_lat
    )

    # Filter out cells with zero or negative population
    valid_mask = agg_pops > 0
    return agg_lons[valid_mask], agg_lats[valid_mask], agg_pops[valid_mask]

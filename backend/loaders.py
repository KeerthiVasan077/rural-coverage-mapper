"""
Data loaders for WorldPop raster, OpenCellID CSV, and OpenStreetMap Overpass.

Supports automatic fallback to synthetic demo data when raw files are missing,
ensuring the application is fully operational out-of-the-box for viva demos.
"""

import os
import json
import logging
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd
import requests
from shapely.geometry import shape, MultiPolygon, Polygon
from shapely.ops import unary_union

from backend.geo import aggregate_population_grid
from backend.demo_data import generate_demo_dataset

logger = logging.getLogger("rural_mapper.loaders")


def load_population_data(
    raster_path: str,
    bbox: List[float],
    aggregation_threshold: int = 200000,
    grid_step_km: float = 0.5,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
    """
    Loads population raster within bbox, keeping pixels with value > 0.
    If raster_path is missing or cannot be read, falls back to demo data.

    Args:
        raster_path: Path to WorldPop GeoTIFF (e.g. data/raw/ind_pop.tif).
        bbox: [min_lon, min_lat, max_lon, max_lat].
        aggregation_threshold: Max pixel count before aggregating to grid_step_km.
        grid_step_km: Coarse grid cell size in km (e.g., 0.5 for 500m).

    Returns:
        Tuple of (lons, lats, populations, is_demo_mode).
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    center_lon = (min_lon + max_lon) / 2.0
    center_lat = (min_lat + max_lat) / 2.0

    if os.path.exists(raster_path):
        try:
            # Attempt to read with rasterio
            import rasterio
            from rasterio.windows import from_bounds

            with rasterio.open(raster_path) as src:
                window = from_bounds(min_lon, min_lat, max_lon, max_lat, src.transform)
                data = src.read(1, window=window)
                win_transform = src.window_transform(window)

                # Find valid non-zero, non-nodata pixels
                nodata = src.nodata if src.nodata is not None else -99999
                valid_mask = (data > 0) & (data != nodata) & ~np.isnan(data)

                rows, cols = np.where(valid_mask)
                pops = data[rows, cols].astype(np.float64)

                # Compute pixel centroid coordinates
                xs, ys = rasterio.transform.xy(win_transform, rows, cols)
                lons = np.array(xs, dtype=np.float64)
                lats = np.array(ys, dtype=np.float64)

                logger.info("Loaded %d population pixels from %s", len(pops), raster_path)

                # Aggregate if pixel count exceeds threshold
                if len(pops) > aggregation_threshold:
                    logger.info("Aggregating %d pixels to %0.1f km grid", len(pops), grid_step_km)
                    lons, lats, pops = aggregate_population_grid(
                        lons, lats, pops, grid_step_km, center_lon, center_lat
                    )

                return lons, lats, pops, False

        except ImportError:
            logger.warning("rasterio not available; checking for tifffile reader")
        except Exception as e:
            logger.error("Failed to read raster %s: %s; falling back to demo mode", raster_path, e)

    # Demo Mode Fallback
    logger.info("Raw raster not found at '%s'. Activating DEMO MODE.", raster_path)
    pop_df, _, _ = generate_demo_dataset(bbox)
    lons = pop_df["lon"].to_numpy(dtype=np.float64)
    lats = pop_df["lat"].to_numpy(dtype=np.float64)
    pops = pop_df["population"].to_numpy(dtype=np.float64)

    return lons, lats, pops, True


def load_towers_data(
    csv_path: str,
    bbox: List[float],
) -> Tuple[pd.DataFrame, bool]:
    """
    Loads OpenCellID CSV, filtering:
    - mcc in [404, 405] (India)
    - radio in ['LTE', 'UMTS']
    - inside bbox [min_lon, min_lat, max_lon, max_lat]

    If csv_path does not exist, falls back to demo towers.

    Returns:
        Tuple of (towers_df, is_demo_mode).
    """
    min_lon, min_lat, max_lon, max_lat = bbox

    if os.path.exists(csv_path):
        try:
            # Read relevant columns from CSV
            cols = ["radio", "mcc", "net", "lon", "lat", "range"]
            df = pd.read_csv(csv_path, usecols=lambda c: c.lower() in cols)
            df.columns = [c.lower() for c in df.columns]

            # Filter MCC 404 and 405 (India mobile networks)
            mcc_mask = df["mcc"].isin([404, 405])
            # Filter radio type (LTE, UMTS, NR / 5G)
            radio_mask = df["radio"].str.upper().isin(["LTE", "UMTS", "NR"])
            # Spatial bounding box mask
            bbox_mask = (
                (df["lon"] >= min_lon)
                & (df["lon"] <= max_lon)
                & (df["lat"] >= min_lat)
                & (df["lat"] <= max_lat)
            )

            filtered = df[mcc_mask & radio_mask & bbox_mask].copy()
            filtered["radio"] = filtered["radio"].str.upper()
            filtered["range"] = pd.to_numeric(filtered.get("range", 5000), errors="coerce").fillna(5000)

            logger.info("Loaded %d towers inside bbox from %s", len(filtered), csv_path)
            return filtered, False

        except Exception as e:
            logger.error("Failed to parse %s: %s; falling back to demo mode", csv_path, e)

    # Demo Mode Fallback
    logger.info("Raw towers CSV not found at '%s'. Activating DEMO MODE.", csv_path)
    _, towers_df, _ = generate_demo_dataset(bbox)
    return towers_df, True


def fetch_osm_water_bodies(
    bbox: List[float],
    cache_path: str,
    overpass_url: str = "https://overpass-api.de/api/interpreter",
    timeout_sec: int = 8,
) -> Optional[MultiPolygon]:
    """
    Retrieves water bodies (natural=water, waterway=riverbank) from OpenStreetMap
    via the Overpass API, with local JSON caching in data/cache/.
    If the network or Overpass is unavailable, returns cached data if present,
    or gracefully skips (returns None).

    Args:
        bbox: [min_lon, min_lat, max_lon, max_lat]
        cache_path: Path to save/load cached GeoJSON.
        overpass_url: Overpass interpreter URL.
        timeout_sec: Network timeout in seconds.

    Returns:
        Shapely MultiPolygon or None.
    """
    # 1. Check local cache first
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            polys = [shape(f["geometry"]) for f in cached_data.get("features", [])]
            if polys:
                union_geom = unary_union(polys)
                if isinstance(union_geom, Polygon):
                    return MultiPolygon([union_geom])
                elif isinstance(union_geom, MultiPolygon):
                    return union_geom
        except Exception as e:
            logger.warning("Failed to load cached OSM water bodies from %s: %s", cache_path, e)

    # 2. Query Overpass API
    min_lon, min_lat, max_lon, max_lat = bbox
    # Overpass bounding box format: (south, west, north, east) -> (min_lat, min_lon, max_lat, max_lon)
    query = f"""
    [out:json][timeout:{timeout_sec}];
    (
      way["natural"="water"]({min_lat},{min_lon},{max_lat},{max_lon});
      relation["natural"="water"]({min_lat},{min_lon},{max_lat},{max_lon});
      way["waterway"="riverbank"]({min_lat},{min_lon},{max_lat},{max_lon});
    );
    out geom;
    """

    try:
        resp = requests.post(
            overpass_url,
            data={"data": query},
            timeout=timeout_sec,
            headers={"User-Agent": "RuralCoverageMapper/1.0 (Hackathon Educational Tool)"},
        )
        if resp.status_code == 200:
            result = resp.json()
            features = []
            polygons = []
            for elem in result.get("elements", []):
                coords = []
                for pt in elem.get("geometry", []):
                    coords.append((pt["lon"], pt["lat"]))
                if len(coords) >= 4:
                    poly = Polygon(coords)
                    if poly.is_valid and not poly.is_empty:
                        polygons.append(poly)
                        features.append(
                            {
                                "type": "Feature",
                                "geometry": poly.__geo_interface__,
                                "properties": elem.get("tags", {}),
                            }
                        )

            if polygons:
                # Save to cache
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({"type": "FeatureCollection", "features": features}, f)

                union_geom = unary_union(polygons)
                if isinstance(union_geom, Polygon):
                    return MultiPolygon([union_geom])
                elif isinstance(union_geom, MultiPolygon):
                    return union_geom

    except Exception as e:
        logger.info("OSM Overpass query skipped (%s); continuing without water mask.", e)

    return None

"""OpenStreetMap data ingestion.

Downloads roads, buildings, and street-network blocks for a given
municipal boundary using OSMnx and the Overpass API. Results are
cached as GeoParquet under data/raw/<region_slug>/osm/.

The Overpass API has rate limits and can be slow; caching is essential
during development. Pass ``force=True`` to bypass the cache.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import osmnx as ox
from geopandas import GeoDataFrame
from shapely.geometry import Polygon
from shapely.ops import polygonize, unary_union

logger = logging.getLogger(__name__)

# Default OSM tags for buildings used as lot proxies. Each region can override
# via its YAML configuration.
DEFAULT_BUILDING_TAGS = {
    "building": True,  # any building=* tag
}

# Highway classes considered "main roads" by default. Used as the seed network
# for block (street-face polygon) generation.
MAIN_ROAD_TAGS = {
    "highway": [
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "residential",
        "unclassified",
        "living_street",
    ],
}


def _cached_or_fetch(
    cache_path: Path,
    fetch_fn,
    *,
    force: bool = False,
) -> GeoDataFrame:
    """Return ``cache_path`` as a GeoDataFrame, or build it via ``fetch_fn``."""
    if cache_path.exists() and not force:
        logger.info("Using cached OSM layer: %s", cache_path)
        return gpd.read_parquet(cache_path)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Fetching from OSM (Overpass): %s", cache_path.stem)
    gdf = fetch_fn()
    gdf.to_parquet(cache_path)
    logger.info("Cached %d features to %s", len(gdf), cache_path)
    return gdf


def load_buildings(
    boundary: GeoDataFrame,
    cache_dir: Path,
    *,
    tags: dict | None = None,
    force: bool = False,
) -> GeoDataFrame:
    """Fetch building footprints inside the boundary polygon.

    Args:
        boundary: Single-row GeoDataFrame in EPSG:4326 with the
            municipal boundary polygon.
        cache_dir: Where to cache the result.
        tags: OSM tag filter. Defaults to all ``building=*`` features.
        force: If True, re-fetch even if cached.

    Returns:
        GeoDataFrame in EPSG:4326. Geometries can be Polygon or
        MultiPolygon; callers should validate before using as lots.
    """
    cache_path = cache_dir / "buildings.parquet"
    tags = tags or DEFAULT_BUILDING_TAGS

    def _fetch() -> GeoDataFrame:
        polygon = _as_4326_polygon(boundary)
        gdf = ox.features_from_polygon(polygon, tags=tags)
        # Keep only polygonal features (drop nodes tagged as buildings, etc.)
        gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        # OSMnx returns a MultiIndex (element_type, osmid); flatten for storage.
        gdf = gdf.reset_index(drop=False)
        return _select_minimal_columns(gdf, extra=["building"])

    return _cached_or_fetch(cache_path, _fetch, force=force)


def load_roads(
    boundary: GeoDataFrame,
    cache_dir: Path,
    *,
    main_only: bool = False,
    force: bool = False,
) -> GeoDataFrame:
    """Fetch the road network as line geometries.

    Args:
        boundary: Municipal boundary in EPSG:4326.
        cache_dir: Where to cache the result.
        main_only: If True, fetch only main classes (motorway/trunk/...).
        force: If True, re-fetch even if cached.

    Returns:
        GeoDataFrame in EPSG:4326 with LineString geometries.
    """
    suffix = "main" if main_only else "all"
    cache_path = cache_dir / f"roads_{suffix}.parquet"
    tags = MAIN_ROAD_TAGS if main_only else {"highway": True}

    def _fetch() -> GeoDataFrame:
        polygon = _as_4326_polygon(boundary)
        gdf = ox.features_from_polygon(polygon, tags=tags)
        gdf = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
        gdf = gdf.reset_index(drop=False)
        return _select_minimal_columns(gdf, extra=["highway", "name"])

    return _cached_or_fetch(cache_path, _fetch, force=force)


def load_blocks(
    boundary: GeoDataFrame,
    cache_dir: Path,
    *,
    force: bool = False,
) -> GeoDataFrame:
    """Derive city blocks as polygons enclosed by the road network.

    Uses Shapely's ``polygonize`` to find the faces of the road graph,
    then clips to the municipal boundary.

    Args:
        boundary: Municipal boundary in EPSG:4326.
        cache_dir: Where to cache the result.
        force: If True, re-derive even if cached.

    Returns:
        GeoDataFrame in EPSG:4326 of block polygons, indexed sequentially.
    """
    cache_path = cache_dir / "blocks.parquet"

    def _fetch() -> GeoDataFrame:
        # Load the full road network (any highway value) and polygonize.
        roads = load_roads(boundary, cache_dir, main_only=False, force=False)
        merged = unary_union(roads.geometry.tolist())
        polygons = list(polygonize(merged))
        if not polygons:
            logger.warning("No blocks could be derived from the road network")
            return gpd.GeoDataFrame(
                {"block_id": []}, geometry=[], crs="EPSG:4326"
            )

        blocks = gpd.GeoDataFrame(
            {"block_id": [f"blk_{i:06d}" for i in range(len(polygons))]},
            geometry=polygons,
            crs="EPSG:4326",
        )

        # Clip to municipal boundary so we don't keep blocks outside the city.
        boundary_4326 = boundary.to_crs("EPSG:4326")
        clipped = gpd.overlay(blocks, boundary_4326, how="intersection")
        # Renumber after overlay (it may split or drop polygons).
        clipped["block_id"] = [f"blk_{i:06d}" for i in range(len(clipped))]
        return clipped[["block_id", "geometry"]]

    return _cached_or_fetch(cache_path, _fetch, force=force)


def _as_4326_polygon(boundary: GeoDataFrame) -> Polygon:
    """Return a single Polygon in EPSG:4326 from a GeoDataFrame."""
    if boundary.crs is None:
        raise ValueError("Boundary GeoDataFrame has no CRS set")
    b4326 = boundary.to_crs("EPSG:4326")
    geom = unary_union(b4326.geometry.tolist())
    if isinstance(geom, Polygon):
        return geom
    # MultiPolygon: OSMnx expects a single Polygon, so use the convex hull.
    # For municipal boundaries with islands, callers should handle this case.
    logger.warning(
        "Boundary is %s; passing convex hull to OSMnx", geom.geom_type
    )
    return geom.convex_hull


def _select_minimal_columns(gdf: GeoDataFrame, extra: list[str]) -> GeoDataFrame:
    """Drop OSM noise columns; keep geometry, osmid, and selected extras."""
    keep = ["geometry"]
    for col in ("osmid", "element_type", *extra):
        if col in gdf.columns:
            keep.append(col)
    return gdf[keep].copy()
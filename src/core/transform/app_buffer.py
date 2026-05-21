"""APP (Permanent Preservation Area) buffer computation.

Brazilian Forest Code (Law 12.651/2012) requires a riparian buffer
along watercourses where construction is forbidden. The minimum
margin for urban streams is 30 meters from each bank.

This module:
  1. Fetches waterway features from OpenStreetMap inside a boundary
  2. Reprojects to a projected CRS (meters)
  3. Applies the buffer (default 30 m, configurable)
  4. Dissolves overlapping buffers into a single multipolygon
  5. Saves as GeoParquet for downstream lot-attribute joins

For the MVP we use a fixed buffer. The Forest Code actually scales
the buffer with river width (30 m for <10 m wide, up to 500 m for
wide rivers), but OSM rarely tags ``width=*`` on streams; refining
this is on the project backlog.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import osmnx as ox
from geopandas import GeoDataFrame
from shapely.geometry import Polygon
from shapely.ops import unary_union

logger = logging.getLogger(__name__)

# Brazilian Forest Code minimum for urban streams (<10 m wide).
DEFAULT_BUFFER_M = 30

# OSM tag filter for natural watercourses. ``river`` and ``stream`` are
# the most common; ``canal`` and ``drain`` cover artificial channels
# that may still warrant a buffer in some legal interpretations.
WATERWAY_TAGS = {
    "waterway": ["river", "stream", "canal", "drain"],
}


def _fetch_waterways(boundary_4326: GeoDataFrame, cache_path: Path) -> GeoDataFrame:
    """Fetch OSM waterway features inside the boundary, with on-disk cache."""
    if cache_path.exists():
        logger.info("Using cached waterways: %s", cache_path)
        return gpd.read_parquet(cache_path)

    logger.info("Fetching waterways from OSM (Overpass)...")
    polygon = _as_4326_polygon(boundary_4326)
    gdf = ox.features_from_polygon(polygon, tags=WATERWAY_TAGS)
    # Keep line geometries; OSM occasionally tags points/polygons as waterways.
    gdf = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    gdf = gdf.reset_index(drop=False)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    keep_cols = [c for c in ("osmid", "waterway", "name", "geometry") if c in gdf.columns]
    gdf = gdf[keep_cols].copy()
    gdf.to_parquet(cache_path)
    logger.info("Cached %d waterway features to %s", len(gdf), cache_path)
    return gdf


def _as_4326_polygon(boundary: GeoDataFrame) -> Polygon:
    """Return a single Polygon in EPSG:4326 from a GeoDataFrame.

    OSMnx requires a single Polygon in WGS84.
    """
    if boundary.crs is None:
        raise ValueError("Boundary GeoDataFrame has no CRS set")
    b4326 = boundary.to_crs("EPSG:4326")
    geom = unary_union(b4326.geometry.tolist())
    if isinstance(geom, Polygon):
        return geom
    # MultiPolygon (e.g. municipalities with islands): use convex hull
    # so OSMnx accepts it. Anything outside the real boundary will be
    # filtered out by the buffer-clipping step later.
    logger.warning(
        "Boundary is %s; passing convex hull to OSMnx", geom.geom_type
    )
    return geom.convex_hull


def compute_app_buffer(
    boundary: GeoDataFrame,
    interim_dir: Path,
    raw_dir: Path,
    *,
    buffer_m: float = DEFAULT_BUFFER_M,
    force: bool = False,
) -> Path:
    """Compute APP polygons by buffering OSM waterways.

    Pipeline:
        1. Fetch waterways from OSM (cached under ``raw_dir/osm/``).
        2. Reproject to ``boundary.crs`` (must be projected, in meters).
        3. Buffer each waterway line by ``buffer_m``.
        4. Dissolve overlapping buffers into one (multi)polygon row.
        5. Clip to the municipal boundary.
        6. Save as GeoParquet.

    Args:
        boundary: Municipal boundary in a projected CRS (e.g. EPSG:31982).
        interim_dir: Where to write the final ``app.geoparquet``.
        raw_dir: Where to cache the raw OSM download
            (``raw_dir/osm/waterways.parquet``).
        buffer_m: Buffer width in meters. Default 30 (Forest Code minimum).
        force: If True, recompute even if the output exists.

    Returns:
        Path to the GeoParquet with a single row containing the
        dissolved APP polygon.
    """
    dst_path = interim_dir / "app.geoparquet"
    if dst_path.exists() and not force:
        logger.info("Using cached APP buffer: %s", dst_path)
        return dst_path

    if boundary.crs is None or not boundary.crs.is_projected:
        raise ValueError(
            f"Boundary must be in a projected CRS (got {boundary.crs}). "
            f"Reproject to UTM-class CRS before calling compute_app_buffer."
        )

    # Step 1: fetch waterways (cached)
    waterways_4326 = _fetch_waterways(
        boundary, raw_dir / "osm" / "waterways.parquet"
    )

    if waterways_4326.empty:
        logger.warning("No waterways found in boundary; APP buffer will be empty.")
        empty = gpd.GeoDataFrame(
            {"buffer_m": [buffer_m]},
            geometry=gpd.GeoSeries([Polygon()], crs=boundary.crs),
            crs=boundary.crs,
        )
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        empty.to_parquet(dst_path)
        return dst_path

    # Step 2: reproject waterways to boundary's projected CRS
    waterways = waterways_4326.to_crs(boundary.crs)

    # Step 3 + 4: buffer and dissolve
    logger.info(
        "Buffering %d waterway features by %g m...",
        len(waterways),
        buffer_m,
    )
    buffered = waterways.geometry.buffer(buffer_m)
    dissolved = unary_union(buffered.tolist())

    # Step 5: clip to boundary
    boundary_geom = unary_union(boundary.geometry.tolist())
    clipped = dissolved.intersection(boundary_geom)

    # Step 6: write
    out = gpd.GeoDataFrame(
        {"buffer_m": [buffer_m]},
        geometry=gpd.GeoSeries([clipped], crs=boundary.crs),
        crs=boundary.crs,
    )
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dst_path)
    logger.info(
        "APP buffer written: %s (area = %.2f km²)",
        dst_path,
        clipped.area / 1_000_000,
    )
    return dst_path
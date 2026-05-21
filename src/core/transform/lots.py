"""Lot construction (Alternative B strategy).

Generates a unified "lots" GeoDataFrame from OSM buildings and OSM-
derived blocks. This is the spatial unit on which every other feature
(slope, APP overlap, zoning) is computed.

Alternative B rationale: São José/SC has no public cadastral lot
shapefile (and most Brazilian cities don't). We approximate lots by:

  - OSM building footprints (buffered) → ``lot_type=osm_building``
  - OSM-derived blocks that contain NO buildings → ``lot_type=synthetic_block``

The buffer adds a small margin around the building to roughly capture
the surrounding lot. The block-without-buildings case keeps coverage
on undeveloped parcels where the strategy degrades gracefully to
whole-block resolution.

This is region-agnostic — any city whose region adapter provides
OSM buildings and blocks can use it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
from geopandas import GeoDataFrame
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)

# Buffer applied to building footprints (meters) to approximate the
# surrounding lot. 5 m is a conservative default — most Brazilian urban
# lots have ~3–5 m of unbuilt margin around the structure.
DEFAULT_BUILDING_BUFFER_M = 5.0

# Polygons below this area are dropped as OSM noise (slivers, mapping
# errors). 50 m² is smaller than any plausible urban lot.
DEFAULT_MIN_LOT_AREA_M2 = 50.0


def build_lots(
    buildings: GeoDataFrame,
    blocks: GeoDataFrame,
    boundary: GeoDataFrame,
    interim_dir: Path,
    *,
    building_buffer_m: float = DEFAULT_BUILDING_BUFFER_M,
    min_area_m2: float = DEFAULT_MIN_LOT_AREA_M2,
    force: bool = False,
) -> Path:
    """Combine OSM buildings and blocks into a unified lot dataset.

    Pipeline:
      1. Reproject everything to ``boundary.crs`` (must be projected).
      2. Clean inputs: drop empty/invalid geometries, drop polygons
         smaller than ``min_area_m2``.
      3. Buffer building footprints by ``building_buffer_m``.
      4. Tag buildings as ``lot_type=osm_building``.
      5. For each block, check if it spatially contains any building.
         If yes, drop the block (the buildings already cover it).
         If no, tag it as ``lot_type=synthetic_block``.
      6. Concatenate, assign sequential ``lot_id``, compute area.
      7. Save as GeoParquet.

    Args:
        buildings: OSM buildings, any CRS.
        blocks: OSM-derived blocks, any CRS.
        boundary: Municipal boundary in a projected CRS (e.g. EPSG:31982).
        interim_dir: Where to write ``lots.geoparquet``.
        building_buffer_m: Buffer in meters around building footprints.
        min_area_m2: Minimum polygon area; smaller geometries are dropped.
        force: If True, recompute even if the output exists.

    Returns:
        Path to the GeoParquet with columns
        ``lot_id, lot_type, area_m2, geometry``.
    """
    dst_path = interim_dir / "lots.geoparquet"
    if dst_path.exists() and not force:
        logger.info("Using cached lots: %s", dst_path)
        return dst_path

    if boundary.crs is None or not boundary.crs.is_projected:
        raise ValueError(
            f"Boundary must be in a projected CRS (got {boundary.crs})."
        )

    target_crs = boundary.crs
    bld = _clean_polygons(buildings.to_crs(target_crs), min_area_m2)
    blk = _clean_polygons(blocks.to_crs(target_crs), min_area_m2)
    logger.info(
        "After cleaning: %d buildings, %d blocks", len(bld), len(blk)
    )

    # Buffer buildings to approximate the surrounding lot.
    bld_lots = bld.copy()
    bld_lots["geometry"] = bld_lots.geometry.buffer(building_buffer_m)
    bld_lots["lot_type"] = "osm_building"

    # Filter blocks that already contain a building (avoid double-counting).
    blk_kept = _blocks_without_buildings(blk, bld)
    blk_kept = blk_kept.copy()
    blk_kept["lot_type"] = "synthetic_block"
    logger.info(
        "Kept %d/%d blocks (the rest already contain OSM buildings)",
        len(blk_kept),
        len(blk),
    )

    lots = gpd.GeoDataFrame(
        gpd.pd.concat(
            [
                bld_lots[["lot_type", "geometry"]],
                blk_kept[["lot_type", "geometry"]],
            ],
            ignore_index=True,
        ),
        geometry="geometry",
        crs=target_crs,
    )

    # Drop any post-buffer geometries that became invalid/empty.
    lots = lots[~lots.geometry.is_empty & lots.geometry.notna()].copy()
    lots = lots[lots.geometry.is_valid].copy()

    lots["lot_id"] = [f"lot_{i:07d}" for i in range(len(lots))]
    lots["area_m2"] = lots.geometry.area
    lots = lots[lots["area_m2"] >= min_area_m2].reset_index(drop=True)

    # Reorder columns for readability
    lots = lots[["lot_id", "lot_type", "area_m2", "geometry"]]

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    lots.to_parquet(dst_path)

    counts = lots["lot_type"].value_counts().to_dict()
    logger.info(
        "Lots written: %s (%d total: %s)",
        dst_path,
        len(lots),
        counts,
    )
    return dst_path


def _clean_polygons(gdf: GeoDataFrame, min_area_m2: float) -> GeoDataFrame:
    """Drop empty/invalid geometries and polygons below ``min_area_m2``."""
    out = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()
    out = out[out.geometry.is_valid].copy()
    out = out[out.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    out = out[out.geometry.area >= min_area_m2].copy()
    return out


def _blocks_without_buildings(
    blocks: GeoDataFrame,
    buildings: GeoDataFrame,
) -> GeoDataFrame:
    """Return blocks that do NOT intersect any building.

    Uses a spatial index (STRtree) for O(n log n) lookup instead of
    the naïve O(n*m) pairwise check.
    """
    if buildings.empty:
        return blocks.copy()

    tree = STRtree(buildings.geometry.tolist())
    keep_mask = []
    for block_geom in blocks.geometry:
        # Candidate buildings whose bbox intersects the block bbox.
        candidate_idxs = tree.query(block_geom)
        # Full intersects check on candidates (bbox query is approximate).
        has_building = any(
            buildings.geometry.iloc[idx].intersects(block_geom)
            for idx in candidate_idxs
        )
        keep_mask.append(not has_building)
    return blocks[keep_mask]
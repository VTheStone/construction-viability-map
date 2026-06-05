"""End-to-end pipeline orchestrator for a region.

Runs the whole chain in dependency order with one command:

    ingest -> slope -> app (APP buffer) -> vectors -> [labels] -> manifest

Each stage reuses an existing building-block function; this module only
sequences them and resolves paths. Idempotent: a stage skips work whose
output already exists unless ``--force`` is given. The OCR ``labels``
stage is slow and its output is gitignored, so it is excluded from
``all`` unless ``--with-labels`` is passed.

Invoked via the Makefile (``make process REGION=...``) or directly:

    python -m src.core.pipeline --region sao_jose_sc --stage all
    python -m src.core.pipeline --region sao_jose_sc --stage manifest
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import geopandas as gpd

from src.core.config import load_region_config
from src.core.ingest import osm, topodata
from src.core.transform.app_buffer import compute_app_buffer
from src.core.transform.manifest import build_manifest
from src.core.transform.master_plan_labels import extract_labels
from src.core.transform.master_plan_vectorize import vectorize_all_maps
from src.core.transform.slope import compute_slope
from src.core.transform.slope_visualize import visualize_slope
from src.regions.sao_jose_sc.adapter import SaoJoseSCAdapter

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Region adapters. New cities register here.
ADAPTERS = {
    "sao_jose_sc": SaoJoseSCAdapter,
}

# Stages in dependency order (labels is opt-in within `all`).
STAGES = ["ingest", "slope", "app", "vectors", "labels", "manifest"]


def _paths(region: str) -> dict[str, Path]:
    base_processed = PROJECT_ROOT / "data" / "processed" / region
    return {
        "raw": PROJECT_ROOT / "data" / "raw" / region,
        "interim": PROJECT_ROOT / "data" / "interim" / region,
        "processed": base_processed,
        "yaml_dir": PROJECT_ROOT / "config" / "master_plan" / region,
        "vectors": base_processed / "master_plan" / "vectors",
        "labels": base_processed / "master_plan" / "labels",
    }


def _adapter(region: str):
    if region not in ADAPTERS:
        raise ValueError(
            f"No adapter registered for region {region!r}. Known: {list(ADAPTERS)}"
        )
    return ADAPTERS[region](PROJECT_ROOT / "data" / "raw" / region)


def _dem_path(region: str) -> Path:
    """Resolve (downloading if needed) the Topodata DEM for the region."""
    adapter = _adapter(region)
    cfg = load_region_config(region)
    sheet = cfg["data_sources"]["dem"]["sheet_codes"][0]
    return topodata.load_dem(sheet, adapter.topodata_dir)


# ----- Stages --------------------------------------------------------------


def stage_ingest(region: str, *, force: bool = False) -> gpd.GeoDataFrame:
    """Cache raw inputs and write the municipal boundary to interim.

    The boundary is the linchpin: slope, vectors and labels all clip to
    it and read it from ``interim/boundary.geoparquet``.
    """
    p = _paths(region)
    adapter = _adapter(region)
    boundary = adapter.load_boundary()  # UTM, from IBGE cache

    p["interim"].mkdir(parents=True, exist_ok=True)
    boundary_path = p["interim"] / "boundary.geoparquet"
    boundary.to_parquet(boundary_path)
    logger.info("Boundary -> %s", boundary_path)

    # Pre-fetch the DEM so the slope stage doesn't block on a download.
    _dem_path(region)
    return boundary


def _load_boundary(region: str) -> gpd.GeoDataFrame:
    """Load boundary from interim; run ingest first if it's missing."""
    boundary_path = _paths(region)["interim"] / "boundary.geoparquet"
    if boundary_path.exists():
        return gpd.read_parquet(boundary_path)
    logger.info("boundary.geoparquet missing — running ingest first.")
    return stage_ingest(region)


def stage_slope(region: str, *, force: bool = False) -> None:
    p = _paths(region)
    adapter = _adapter(region)
    boundary = _load_boundary(region)
    dem_path = _dem_path(region)

    # Land = boundary minus OSM water, so slope isn't computed over the bay.
    water = osm.load_water(boundary, adapter.osm_dir).to_crs(boundary.crs)
    land_geom = boundary.geometry.union_all()
    if not water.empty:
        water_union = water.geometry.union_all()
        if water_union is not None and not water_union.is_empty:
            land_geom = land_geom.difference(water_union)
    land = gpd.GeoDataFrame(geometry=[land_geom], crs=boundary.crs)

    slope_tif = compute_slope(dem_path, land, p["interim"], force=force)
    visualize_slope(slope_tif, p["processed"], force=force)


def stage_app(region: str, *, force: bool = False) -> None:
    p = _paths(region)
    boundary = _load_boundary(region)
    compute_app_buffer(boundary, p["interim"], p["raw"], force=force)


def stage_vectors(region: str, *, force: bool = False) -> None:
    p = _paths(region)
    _load_boundary(region)  # ensure boundary.geoparquet exists for clipping
    vectorize_all_maps(
        yaml_dir=p["yaml_dir"],
        project_root=PROJECT_ROOT,
        out_dir=p["vectors"],
        boundary_path=p["interim"] / "boundary.geoparquet",
        force=force,
    )


def stage_labels(region: str, *, force: bool = False, canvas_size: int = 3500) -> None:
    p = _paths(region)
    boundary_path = p["interim"] / "boundary.geoparquet"
    _load_boundary(region)
    for yaml_path in sorted(p["yaml_dir"].glob("map_*.yaml")):
        extract_labels(
            yaml_path,
            PROJECT_ROOT,
            p["labels"],
            boundary_path=boundary_path,
            canvas_size=canvas_size,
            force=force,
        )


def stage_manifest(region: str, *, force: bool = False) -> None:
    p = _paths(region)
    adapter = _adapter(region)
    build_manifest(
        region_slug=region,
        processed_dir=p["processed"],
        interim_dir=p["interim"],
        master_plan_overlays=adapter.load_master_plan_overlays(),
        slope_json_path=p["processed"] / "slope.json",
        app_parquet_path=p["interim"] / "app.geoparquet",
    )


def run_all(
    region: str,
    *,
    force: bool = False,
    with_labels: bool = False,
    canvas_size: int = 3500,
) -> None:
    stage_ingest(region, force=force)
    stage_slope(region, force=force)
    stage_app(region, force=force)
    stage_vectors(region, force=force)
    if with_labels:
        stage_labels(region, force=force, canvas_size=canvas_size)
    stage_manifest(region, force=force)


_STAGE_FUNCS = {
    "ingest": stage_ingest,
    "slope": stage_slope,
    "app": stage_app,
    "vectors": stage_vectors,
    "manifest": stage_manifest,
    # `labels` is handled separately (extra canvas_size arg).
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the region data pipeline.")
    parser.add_argument("--region", default="sao_jose_sc")
    parser.add_argument("--stage", default="all", choices=["all", *STAGES])
    parser.add_argument(
        "--force", action="store_true", help="Recompute even if outputs exist."
    )
    parser.add_argument(
        "--with-labels",
        action="store_true",
        help="Include the slow OCR label stage in --stage all.",
    )
    parser.add_argument(
        "--canvas-size", type=int, default=3500, help="EasyOCR canvas size (labels)."
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    if args.stage == "all":
        run_all(
            args.region,
            force=args.force,
            with_labels=args.with_labels,
            canvas_size=args.canvas_size,
        )
    elif args.stage == "labels":
        stage_labels(args.region, force=args.force, canvas_size=args.canvas_size)
    else:
        _STAGE_FUNCS[args.stage](args.region, force=args.force)

    logger.info("Pipeline stage '%s' complete for %s.", args.stage, args.region)


if __name__ == "__main__":
    main()
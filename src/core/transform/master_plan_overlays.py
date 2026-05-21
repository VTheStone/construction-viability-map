"""Master Plan overlay loader.

Reads the ``master_plan_maps`` block from a region's YAML configuration
and returns metadata for each map that should be loaded as a raster
overlay in the app. The overlays themselves (GeoTIFFs) live under
``data/raw/<region_slug>/master_plan/overlays/`` and are produced by
manual georeferencing in QGIS (Phase 3d of the project).

This module is region-agnostic — any city whose YAML declares a
``master_plan_maps`` block can use it. Each region adapter calls
``load_overlays`` with its own configuration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rasterio
from rasterio.coords import BoundingBox

logger = logging.getLogger(__name__)

# Filename pattern of the georeferenced output. Phase 3d generates TIFFs
# named after the map id (e.g. "map_02" → "mapa_02_macrozoneamento.tif").
# Adapters pass the actual filenames via the YAML's `maps[].file` field
# when present; otherwise this module derives the filename from the id.


@dataclass(frozen=True)
class OverlayMetadata:
    """Metadata for one georeferenced Master Plan map.

    Attributes:
        id: Stable identifier from the YAML (e.g. "map_02").
        name: Human-readable label (e.g. "Macrozoneamento").
        annex: Annex number in the source legislation.
        path: Absolute path to the GeoTIFF on disk.
        crs: Coordinate reference system string (e.g. "EPSG:31982").
        bounds: Geographic extent (left, bottom, right, top) in `crs`.
        width: Image width in pixels.
        height: Image height in pixels.
        vectorized: True if a vectorized version of this layer exists or
            is planned (per the YAML); does NOT mean it's loaded here.
    """

    id: str
    name: str
    annex: int
    path: Path
    crs: str
    bounds: BoundingBox
    width: int
    height: int
    vectorized: bool


def _resolve_overlay_dir(
    raw_dir: Path,
    master_plan_cfg: dict[str, Any],
) -> Path:
    """Return the absolute path to the directory containing overlay TIFFs."""
    overlay_dir_rel = master_plan_cfg.get("overlay_dir", "master_plan/overlays")
    overlay_dir = raw_dir / overlay_dir_rel
    if not overlay_dir.exists():
        raise FileNotFoundError(
            f"Overlay directory does not exist: {overlay_dir}. "
            f"Run Phase 3d (manual georeferencing in QGIS) first."
        )
    return overlay_dir


def _derive_filename(map_id: str, overlay_dir: Path) -> Path:
    """Find the GeoTIFF whose name starts with the map_id.

    The convention from Phase 3d is ``<map_id_with_number>_<slug>.tif``,
    e.g. ``mapa_02_macrozoneamento.tif``. We look for files starting with
    the numeric prefix so renames of the descriptive suffix don't break
    the loader.

    Args:
        map_id: e.g. "map_02".
        overlay_dir: directory to scan.

    Returns:
        Path to the matching TIFF.

    Raises:
        FileNotFoundError: if no file matches or more than one matches.
    """
    # "map_02" → look for files starting with "mapa_02" or "map_02".
    # Phase 3d used "mapa_NN_*.tif" so this is the primary prefix.
    candidates: list[Path] = []
    number = map_id.split("_")[-1]
    for prefix in (f"mapa_{number}_", f"map_{number}_"):
        candidates.extend(overlay_dir.glob(f"{prefix}*.tif"))

    if not candidates:
        raise FileNotFoundError(
            f"No TIFF found for {map_id} in {overlay_dir}. "
            f"Expected a file like 'mapa_{number}_<description>.tif'."
        )
    if len(candidates) > 1:
        raise FileNotFoundError(
            f"Ambiguous TIFFs for {map_id} in {overlay_dir}: "
            f"{[p.name for p in candidates]}. Keep only one."
        )
    return candidates[0]


def _read_raster_metadata(tif_path: Path) -> tuple[str, BoundingBox, int, int]:
    """Open the GeoTIFF briefly to read its CRS, bounds, and dimensions."""
    with rasterio.open(tif_path) as ds:
        if ds.crs is None:
            raise ValueError(
                f"GeoTIFF has no CRS: {tif_path}. Re-export from QGIS "
                f"with a Target CRS set."
            )
        return str(ds.crs), ds.bounds, ds.width, ds.height


def load_overlays(
    raw_dir: Path,
    master_plan_cfg: dict[str, Any],
) -> list[OverlayMetadata]:
    """Load metadata for every overlay-enabled map in the configuration.

    Args:
        raw_dir: Region's raw-data root, e.g. ``data/raw/sao_jose_sc/``.
        master_plan_cfg: The ``master_plan_maps`` block from the region YAML.

    Returns:
        List of OverlayMetadata, in the order declared in the YAML.

    Raises:
        FileNotFoundError: if ``overlay: true`` is declared in the YAML
            but the corresponding TIFF is missing on disk.
        ValueError: if a TIFF exists but lacks a CRS.
    """
    overlay_dir = _resolve_overlay_dir(raw_dir, master_plan_cfg)
    maps_cfg = master_plan_cfg.get("maps", [])

    overlays: list[OverlayMetadata] = []
    for map_entry in maps_cfg:
        if not map_entry.get("overlay", False):
            continue

        map_id = map_entry["id"]
        tif_path = _derive_filename(map_id, overlay_dir)
        crs, bounds, width, height = _read_raster_metadata(tif_path)

        overlays.append(
            OverlayMetadata(
                id=map_id,
                name=map_entry.get("name", map_id),
                annex=int(map_entry.get("annex", 0)),
                path=tif_path,
                crs=crs,
                bounds=bounds,
                width=width,
                height=height,
                vectorized=bool(map_entry.get("vectorized", False)),
            )
        )

    logger.info(
        "Loaded %d Master Plan overlays from %s",
        len(overlays),
        overlay_dir,
    )
    return overlays
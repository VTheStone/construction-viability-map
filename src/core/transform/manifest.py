"""Manifest writer for processed layers.

After all transforms have run, this module emits a single ``manifest.json``
under ``data/processed/<region>/`` that lists every layer the app can
display: name, type (raster/vector), file paths, bounds, CRS, and any
metadata the UI needs (legend, opacity hint, default visibility).

The app reads this manifest exclusively — it never re-discovers layers
by globbing the filesystem. This keeps the contract between pipeline
and UI explicit and versionable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import rasterio
from rasterio.warp import transform_bounds

logger = logging.getLogger(__name__)

LayerType = Literal["raster", "vector"]
LayerGroup = Literal["terrain", "environmental", "master_plan", "reference"]


@dataclass
class LayerEntry:
    """One entry in the manifest.

    All paths are stored RELATIVE to the region's processed directory,
    so the app can move the data around without breaking the manifest.
    """

    id: str
    name: str
    group: LayerGroup
    type: LayerType
    path: str  # relative to processed_dir
    bounds_wgs84: dict[str, float]  # {west, south, east, north}
    default_visible: bool = False
    default_opacity: float = 0.6
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "group": self.group,
            "type": self.type,
            "path": self.path,
            "bounds_wgs84": self.bounds_wgs84,
            "default_visible": self.default_visible,
            "default_opacity": self.default_opacity,
            "extras": self.extras,
        }


def _raster_bounds_wgs84(tif_path: Path) -> dict[str, float]:
    """Read a raster's bounds and reproject to WGS84."""
    with rasterio.open(tif_path) as src:
        west, south, east, north = transform_bounds(
            src.crs, "EPSG:4326", *src.bounds, densify_pts=21
        )
    return {"west": west, "south": south, "east": east, "north": north}


def _vector_bounds_wgs84(parquet_path: Path) -> dict[str, float]:
    """Read a GeoParquet's total bounds and reproject to WGS84."""
    gdf = gpd.read_parquet(parquet_path)
    gdf_4326 = gdf.to_crs("EPSG:4326")
    west, south, east, north = gdf_4326.total_bounds
    return {"west": west, "south": south, "east": east, "north": north}


def build_manifest(
    *,
    region_slug: str,
    processed_dir: Path,
    interim_dir: Path,
    master_plan_overlays: list,  # list[OverlayMetadata]
    slope_json_path: Path | None = None,
    app_parquet_path: Path | None = None,
) -> Path:
    """Build the manifest and write it to ``processed_dir/manifest.json``.

    Args:
        region_slug: Region identifier (used in the manifest header).
        processed_dir: Region's processed directory.
        interim_dir: Region's interim directory (some layers live here
            during MVP; they'll move to processed in a future cleanup).
        master_plan_overlays: List of OverlayMetadata from
            ``core.transform.master_plan_overlays.load_overlays``.
        slope_json_path: Path to ``slope.json`` (the sidecar from
            ``slope_visualize``). If None, slope is skipped.
        app_parquet_path: Path to ``app.geoparquet``. If None, skipped.

    Returns:
        Path to the written manifest.
    """
    layers: list[LayerEntry] = []

    # ----- Terrain: slope --------------------------------------------------
    if slope_json_path is not None and slope_json_path.exists():
        meta = json.loads(slope_json_path.read_text(encoding="utf-8"))
        slope_png = slope_json_path.parent / "slope.png"
        layers.append(
            LayerEntry(
                id="slope",
                name="Declividade",
                group="terrain",
                type="raster",
                path=str(slope_png.resolve()),
                bounds_wgs84=meta["bounds_wgs84"],
                default_visible=True,
                default_opacity=0.6,
                extras={
                    "unit": meta.get("unit"),
                    "value_range": meta.get("value_range"),
                    "colormap": meta.get("colormap"),
                    "legend": meta.get("legend", []),
                },
            )
        )

    # ----- Environmental: APP ---------------------------------------------
    if app_parquet_path is not None and app_parquet_path.exists():
        layers.append(
            LayerEntry(
                id="app",
                name="APP — Áreas de Preservação Permanente",
                group="environmental",
                type="vector",
                path=str(app_parquet_path.resolve()),  # outside processed_dir
                bounds_wgs84=_vector_bounds_wgs84(app_parquet_path),
                default_visible=False,
                default_opacity=0.35,
                extras={
                    "style": {
                        "color": "#1f78b4",
                        "weight": 1,
                        "fillColor": "#1f78b4",
                        "fillOpacity": 0.35,
                    },
                },
            )
        )

    # ----- Master Plan vectors --------------------------------------------
    # Each map points to a GeoParquet produced by master_plan_vectorize.
    # The mapping from overlay.id to file is by convention:
    # processed/master_plan/vectors/<overlay.id>.geoparquet
    master_plan_vectors_dir = processed_dir / "master_plan" / "vectors"
    for overlay in master_plan_overlays:
        parquet_path = master_plan_vectors_dir / f"{overlay.id}.geoparquet"
        if not parquet_path.exists():
            logger.warning(
                "Skipping %s: GeoParquet not found at %s. "
                "Run scripts/vectorize_master_plan.py --map %s first.",
                overlay.id,
                parquet_path,
                overlay.id.split("_")[-1],
            )
            continue
        layers.append(
            LayerEntry(
                id=overlay.id,
                name=f"PD: {overlay.name}",
                group="master_plan",
                type="vector",
                path=str(parquet_path.resolve()),
                bounds_wgs84=_vector_bounds_wgs84(parquet_path),
                default_visible=False,
                default_opacity=0.6,
                extras={
                    "annex": overlay.annex,
                    "style": {
                        "color": "#333333",
                        "weight": 1,
                        "fillOpacity": 0.6,
                    },
                },
            )
        )

    manifest = {
        "region_slug": region_slug,
        "schema_version": 1,
        "layers": [layer.to_dict() for layer in layers],
    }

    manifest_path = processed_dir / "manifest.json"
    processed_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    logger.info(
        "Manifest written: %s (%d layers across %d groups)",
        manifest_path,
        len(layers),
        len({entry.group for entry in layers}),
    )
    return manifest_path
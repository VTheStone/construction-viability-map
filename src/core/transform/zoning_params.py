"""Load per-zone urbanistic parameters (LC 173/2024) from YAML.

The spatial layers tell you *which* zone a point is in; this table tells
you *what may be built there* (floors, floor-area ratio, occupancy, …).
Keyed by the individual zone code so the app can resolve a point via its
OCR label (co-colored subzones share one polygon but differ in params).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# repo root: src/core/transform/zoning_params.py -> parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def parameters_path(region_slug: str, project_root: Path | None = None) -> Path:
    root = project_root or PROJECT_ROOT
    return root / "config" / "master_plan" / region_slug / "parameters.yaml"


def load_zone_parameters(
    region_slug: str, project_root: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Return ``{zone_code: {param: value}}`` for a region, or {} if absent."""
    path = parameters_path(region_slug, project_root)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("zones", {})


def load_zoning_subzones(region_slug: str, project_root: Path | None = None):
    """Hand-digitized per-subzone polygons (map_03) for exact zone lookup.

    Returns a GeoDataFrame[zone_code, geometry] in WGS84 with geometries
    repaired (make_valid), or None if the file is absent. These
    authoritative polygons replace the OCR-label heuristic for resolving
    the exact subzone at a clicked point.
    """
    import geopandas as gpd

    root = project_root or PROJECT_ROOT
    path = root / "data" / "raw" / region_slug / "master_plan" / "map_03_subzones.gpkg"
    if not path.exists():
        return None
    g = gpd.read_file(path)
    g["geometry"] = g.geometry.make_valid()
    return g.to_crs("EPSG:4326")[["zone_code", "geometry"]]

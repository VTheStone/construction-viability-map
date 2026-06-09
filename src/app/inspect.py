"""Inspect terrain and Master Plan data at a clicked map point.

The Streamlit app passes a WGS84 (lat, lng) click plus the already-loaded
vector layers; this module samples the slope raster, the DEM, the APP
buffer and each enabled zone layer at that point and returns a plain dict
the UI renders. Kept free of Streamlit imports so it stays testable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import rasterio
from pyproj import Transformer
from shapely.geometry import Point

# App data is in SIRGAS 2000 / UTM 22S; map clicks arrive in WGS84.
_DATA_CRS = "EPSG:31982"
_TO_UTM = Transformer.from_crs("EPSG:4326", _DATA_CRS, always_xy=True)


def _sample(tif_path: Path | None, x: float, y: float) -> list[float | None] | None:
    """Sample every band at native coords (x, y).

    Returns one value per band (None for the raster's NoData), or None if
    the file is missing or the point falls outside the raster footprint.
    """
    if tif_path is None or not Path(tif_path).exists():
        return None
    with rasterio.open(tif_path) as src:
        b = src.bounds
        if not (b.left <= x <= b.right and b.bottom <= y <= b.top):
            return None
        values = next(src.sample([(x, y)]))
        nodata = src.nodata
    return [
        None if (nodata is not None and v == nodata) else float(v) for v in values
    ]


def inspect_point(
    lat: float,
    lng: float,
    *,
    slope_tif: Path | None = None,
    app_gdf: gpd.GeoDataFrame | None = None,
    plan_layers: list[tuple[str, gpd.GeoDataFrame]] | None = None,
) -> dict[str, Any]:
    """Return slope / elevation / APP / zone facts at a WGS84 point."""
    result: dict[str, Any] = {"lat": lat, "lng": lng}

    # Slope raster is in UTM -> reproject the click. Bands: 1=degrees,
    # 2=percent, 3=elevation (m).
    ux, uy = _TO_UTM.transform(lng, lat)
    slope = _sample(slope_tif, ux, uy)
    if slope:
        if slope[0] is not None:
            result["slope_deg"] = round(slope[0], 1)
        if len(slope) > 1 and slope[1] is not None:
            result["slope_pct"] = round(slope[1], 1)
        if len(slope) > 2 and slope[2] is not None:
            result["elevation_m"] = round(slope[2], 1)

    pt = Point(lng, lat)

    if app_gdf is not None and not app_gdf.empty:
        result["in_app"] = bool(app_gdf.geometry.iloc[0].contains(pt))

    zones = []
    for name, gdf in plan_layers or []:
        if gdf is None or gdf.empty:
            continue
        hit = gdf[gdf.geometry.contains(pt)]
        if not hit.empty:
            row = hit.iloc[0]
            zones.append(
                {
                    "layer": name,
                    "zone_code": row.get("zone_code"),
                    "zone_name": row.get("zone_name"),
                }
            )
    result["zones"] = zones
    return result
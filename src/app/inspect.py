"""Inspect terrain and Master Plan data at a clicked map point.

The Streamlit app passes a WGS84 (lat, lng) click plus the already-loaded
vector layers; this module samples the slope raster, the DEM, the APP
buffer and each enabled zone layer at that point and returns a plain dict
the UI renders. Kept free of Streamlit imports so it stays testable.
"""

from __future__ import annotations

import re
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


def _expand_group(code: str) -> list[str]:
    """Expand a grouped polygon code into individual subzone codes.

    Co-colored subzones share one polygon, so map_03's ``zone_code`` is a
    group like ``"ZA-9/ZA-10/ZB-2"`` (slash list) or ``"ZA-1..ZA-7"``
    (range). Returns the individual codes to match the per-subzone
    parameter table and the OCR labels.
    """
    out: list[str] = []
    for chunk in code.split("/"):
        chunk = chunk.strip()
        if ".." in chunk:
            a, b = chunk.split("..")
            ma, mb = re.match(r"([A-Z]+)-?(\d+)", a), re.match(r"([A-Z]+)-?(\d+)", b)
            if ma and mb:
                prefix = ma.group(1)
                out += [f"{prefix}-{n}" for n in range(int(ma.group(2)), int(mb.group(2)) + 1)]
                continue
        out.append(chunk)
    return out


def inspect_point(
    lat: float,
    lng: float,
    *,
    slope_tif: Path | None = None,
    app_gdf: gpd.GeoDataFrame | None = None,
    plan_layers: list[tuple[str, gpd.GeoDataFrame]] | None = None,
    zone_params: dict[str, dict[str, Any]] | None = None,
    label_gdf: gpd.GeoDataFrame | None = None,
) -> dict[str, Any]:
    """Return slope / elevation / APP / zone facts at a WGS84 point."""
    result: dict[str, Any] = {"lat": lat, "lng": lng}

    # Slope raster is in UTM -> reproject the click.
    ux, uy = _TO_UTM.transform(lng, lat)
    slope = _sample(slope_tif, ux, uy)
    if slope and slope[0] is not None:
        result["slope_deg"] = round(slope[0], 1)
        if len(slope) > 1 and slope[1] is not None:
            result["slope_pct"] = round(slope[1], 1)

    # Topodata DEM keeps its transform in lon/lat -> sample directly.
    elev = _sample(dem_tif, lng, lat)
    if elev and elev[0] is not None:
        result["elevation_m"] = round(elev[0], 1)

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

    # Construction potential: a co-colored polygon groups several codes, so
    # resolve the exact subzone via the nearest in-group OCR label, then
    # look up its LC 173/2024 parameters.
    if zone_params:
        for z in zones:
            candidates = _expand_group(str(z["zone_code"]))
            code = None
            if len(candidates) == 1 and candidates[0] in zone_params:
                code = candidates[0]
            elif label_gdf is not None and not label_gdf.empty:
                sub = label_gdf[label_gdf["zone_code"].isin(candidates)]
                if not sub.empty:
                    code = sub.loc[sub.geometry.distance(pt).idxmin(), "zone_code"]
            if code and code in zone_params and not zone_params[code].get("rural"):
                result["potential"] = {"zone_code": code, **zone_params[code]}
                break

    return result
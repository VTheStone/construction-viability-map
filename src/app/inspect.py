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
    subzones: gpd.GeoDataFrame | None = None,
    aei_zones: gpd.GeoDataFrame | None = None,
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

    # Construction potential: resolve the EXACT subzone by point-in-polygon
    # on the hand-digitized subzones (authoritative), then look up its
    # LC 173/2024 parameters. iloc[0] handles the rare hand-drawn sliver
    # overlap by taking the first match.
    if zone_params is not None and subzones is not None and not subzones.empty:
        hit = subzones[subzones.geometry.contains(pt)]
        if not hit.empty:
            code = str(hit.iloc[0]["zone_code"])
            p = zone_params.get(code, {})
            if p and not (p.get("rural") or p.get("preservacao")):
                result["potential"] = {"zone_code": code, **p}

    # AEI precedence: a special-interest area overlapping the base zone
    # prevails (LC 173/2024 sobreposição). A buildable AEI overrides the
    # potential; a preservation AEI (e.g. AEIA-1) flags a restriction.
    if zone_params is not None and aei_zones is not None and not aei_zones.empty:
        ahit = aei_zones[aei_zones.geometry.contains(pt)]
        if not ahit.empty:
            acode = str(ahit.iloc[0]["zone_code"])
            ap = zone_params.get(acode, {})
            if ap.get("preservacao"):
                result["preservacao_aei"] = acode
            elif ap and not ap.get("rural"):
                base_code = result.get("potential", {}).get("zone_code")
                result["potential"] = {"zone_code": acode, "prevalece_sobre": base_code, **ap}

    return result

def viability_verdict(info: dict[str, Any]) -> dict[str, Any]:
    """Coarse, indicative buildability read: potential vs constraints.

    Combines the clicked point's slope, APP membership and zone CA into a
    single signal. Heuristic — a starting flag, not a technical or legal
    assessment. Returns {level, icon, reasons}.
    """
    if info.get("in_app") or info.get("preservacao_aei"):
        reason = "Dentro de APP — faixa de preservação; construção em regra vedada."
        if info.get("preservacao_aei"):
            reason = (
                f"Em {info['preservacao_aei']} (Área de Especial Interesse Ambiental "
                "de preservação) — construção em regra vedada."
            )
        return {"level": "restrito", "icon": "🔴", "reasons": [reason]}

    reasons: list[str] = []
    pot = info.get("potential") or {}
    ca_max = pot.get("ca_maximo")
    slope_pct = info.get("slope_pct")

    constraint = "ok"
    if isinstance(slope_pct, (int, float)):
        if slope_pct > 30:
            constraint = "alto"
            reasons.append(f"Declividade alta ({slope_pct}%): obra cara e possivelmente restrita.")
        elif slope_pct > 8:
            constraint = "medio"
            reasons.append(f"Declividade moderada ({slope_pct}%): atenção a fundação e terraplenagem.")

    potential = "medio"
    if isinstance(ca_max, (int, float)):
        if ca_max >= 3:
            potential = "alto"
            reasons.append(f"Alto potencial construtivo (CA até {ca_max}).")
        elif ca_max <= 1:
            potential = "baixo"
            reasons.append(f"Potencial construtivo limitado (CA {ca_max}).")

    if constraint == "alto":
        level, icon = "baixo", "🟠"
    elif potential == "alto" and constraint == "ok":
        level, icon = "alto", "🟢"
    elif potential == "baixo":
        level, icon = "baixo", "🟠"
    else:
        level, icon = "médio", "🟡"

    if not reasons:
        reasons.append("Sem restrições fortes detectadas; potencial intermediário.")
    return {"level": level, "icon": icon, "reasons": reasons}
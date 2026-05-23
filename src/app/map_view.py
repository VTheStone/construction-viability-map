"""Folium map construction for the Streamlit app.

Overlays point to URLs served by ``static_server.py`` (not local file
paths). This keeps the Folium HTML small — the browser fetches each
PNG lazily when its layer is enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import folium
import geopandas as gpd
from folium.raster_layers import ImageOverlay


@dataclass(frozen=True)
class RasterLayerSpec:
    """A raster layer to add to the map.

    ``image_url`` is an HTTP URL (served by static_server.py), not a
    local file path — Folium will tell the browser to fetch it.
    """

    name: str
    image_url: str
    bounds_wgs84: dict[str, float]
    opacity: float
    show: bool


@dataclass(frozen=True)
class VectorLayerSpec:
    """A vector layer (already loaded into a GeoDataFrame in WGS84)."""

    name: str
    gdf: gpd.GeoDataFrame  # already reprojected to EPSG:4326
    style: dict[str, Any]
    show: bool


def _bounds_to_folium(b: dict[str, float]) -> list[list[float]]:
    """Folium expects [[south, west], [north, east]]."""
    return [[b["south"], b["west"]], [b["north"], b["east"]]]


def build_map(
    center_lat: float,
    center_lon: float,
    zoom: int,
    raster_layers: list[RasterLayerSpec],
    vector_layers: list[VectorLayerSpec],
) -> folium.Map:
    """Assemble the Folium map with all overlays + a LayerControl."""
    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    for layer in raster_layers:
        ImageOverlay(
            name=layer.name,
            image=layer.image_url,
            bounds=_bounds_to_folium(layer.bounds_wgs84),
            opacity=layer.opacity,
            interactive=False,
            cross_origin=False,
            show=layer.show,
        ).add_to(fmap)

    for layer in vector_layers:
        folium.GeoJson(
            layer.gdf.__geo_interface__,
            name=layer.name,
            style_function=lambda _f, style=layer.style: style,
            show=layer.show,
        ).add_to(fmap)

    folium.LayerControl(collapsed=False).add_to(fmap)
    return fmap
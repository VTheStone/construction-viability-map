"""Folium map construction for the Streamlit app.

Overlays point to URLs served by ``static_server.py`` (not local file
paths). This keeps the Folium HTML small — the browser fetches each
PNG lazily when its layer is enabled.

Vector layers (GeoParquet) are loaded into memory and serialized as
inline GeoJSON. Each feature can carry its own color via the
``color_property`` field on VectorLayerSpec.

For Master Plan layers, the same VectorLayerSpec can be split into
one Folium layer per zone_code, so each zone becomes an individual
checkbox in the LayerControl (grouped by parent layer name).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import folium
import geopandas as gpd
from folium.plugins import GroupedLayerControl
from folium.raster_layers import ImageOverlay


@dataclass(frozen=True)
class RasterLayerSpec:
    """A raster layer to add to the map."""

    name: str
    image: str 
    bounds_wgs84: dict[str, float]
    opacity: float
    show: bool


@dataclass(frozen=True)
class VectorLayerSpec:
    """A vector layer (already loaded into a GeoDataFrame in WGS84).

    When ``split_by`` is set, the layer is exploded into one Folium
    layer per distinct value of that column, and all of them are
    grouped under ``name`` in the LayerControl. Used by Master Plan
    layers so each zone gets its own checkbox.
    """

    name: str
    gdf: gpd.GeoDataFrame  # already reprojected to EPSG:4326
    style: dict[str, Any]
    show: bool
    color_property: str | None = None
    tooltip_fields: list[str] = field(default_factory=list)
    split_by: str | None = None  # column to split into sublayers
    split_label: str | None = None  # column whose value labels each sublayer
    # Companion zone-code labels rendered as always-on text markers,
    # tied to this layer's toggle (no separate checkbox).
    label_gdf: gpd.GeoDataFrame | None = None
    label_field: str | None = None


def _bounds_to_folium(b: dict[str, float]) -> list[list[float]]:
    """Folium expects [[south, west], [north, east]]."""
    return [[b["south"], b["west"]], [b["north"], b["east"]]]


def _make_style_function(style: dict[str, Any], color_property: str | None):
    """Build a style_function closure for folium.GeoJson."""
    def style_function(feature, base=style, prop=color_property):
        out = dict(base)
        if prop is not None:
            color = feature["properties"].get(prop)
            if color:
                out["fillColor"] = color
                out["color"] = color
        return out
    return style_function


def _add_geojson_layer(
    fmap: folium.Map,
    gdf: gpd.GeoDataFrame,
    name: str,
    style: dict[str, Any],
    color_property: str | None,
    tooltip_fields: list[str],
    show: bool,
) -> folium.FeatureGroup:
    """Add a single GeoJSON-backed FeatureGroup to the map and return it.

    We wrap the GeoJson in a FeatureGroup so GroupedLayerControl can
    reference it by Python object identity instead of by string name
    (more robust against duplicate names across maps).
    """
    fg = folium.FeatureGroup(name=name, show=show)
    tooltip = (
        folium.GeoJsonTooltip(fields=tooltip_fields, sticky=False)
        if tooltip_fields
        else None
    )
    folium.GeoJson(
        gdf.__geo_interface__,
        style_function=_make_style_function(style, color_property),
        tooltip=tooltip,
    ).add_to(fg)
    fg.add_to(fmap)
    return fg

def _add_label_markers(
    fmap: folium.Map,
    gdf: gpd.GeoDataFrame,
    name: str,
    field: str,
    show: bool,
) -> folium.FeatureGroup:
    """Add a FeatureGroup of always-visible text labels (one per point).

    Used by OCR'd zone-code layers: a DivIcon shows the code itself so
    co-colored subzones can be told apart. The CSS transform centers the
    label box on its coordinate.
    """
    # control=False keeps it out of the LayerControl: it's not an
    # independent layer, it rides with its parent's toggle.
    fg = folium.FeatureGroup(name=name, show=show, control=False)
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        text = str(row[field])
        folium.Marker(
            location=[geom.y, geom.x],
            icon=folium.DivIcon(
                html=(
                    '<div style="font-size:11px;font-weight:700;color:#1a1a1a;'
                    'background:rgba(255,255,255,0.8);border:1px solid #555;'
                    'border-radius:3px;padding:0 3px;white-space:nowrap;'
                    'transform:translate(-50%,-50%);">' + text + "</div>"
                ),
            ),
        ).add_to(fg)
    fg.add_to(fmap)
    return fg


def build_map(
    center_lat: float,
    center_lon: float,
    zoom: int,
    raster_layers: list[RasterLayerSpec],
    vector_layers: list[VectorLayerSpec],
    highlight_gdf: gpd.GeoDataFrame | None = None,
    marker: tuple[float, float] | None = None,
) -> folium.Map:
    """Assemble the Folium map with all overlays + a (grouped) LayerControl.

    OSM basemap is added with ``control=False`` so it stays permanent and
    invisible to the LayerControl. ``wheelDebounceTime`` and
    ``wheelPxPerZoomLevel`` tame the multi-zoom-per-tick behavior on
    Windows mouse wheels.
    """
    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles=None,
        control_scale=True,
        wheelDebounceTime=100,
        wheelPxPerZoomLevel=120,
    )
    folium.TileLayer(
        "OpenStreetMap",
        name="OpenStreetMap",
        control=False,
    ).add_to(fmap)

    for layer in raster_layers:
        ImageOverlay(
            name=layer.name,
            image=layer.image,
            bounds=_bounds_to_folium(layer.bounds_wgs84),
            opacity=layer.opacity,
            interactive=False,
            cross_origin=False,
            show=layer.show,
        ).add_to(fmap)

    # Track which sublayers belong to which group label, so we can build
    # a GroupedLayerControl at the end.
    groups: dict[str, list[folium.FeatureGroup]] = {}

    for layer in vector_layers:
        if layer.split_by is None or layer.split_by not in layer.gdf.columns:
            # Single layer, no splitting.
            _add_geojson_layer(
                fmap=fmap,
                gdf=layer.gdf,
                name=layer.name,
                style=layer.style,
                color_property=layer.color_property,
                tooltip_fields=layer.tooltip_fields,
                show=layer.show,
            )
            continue

        # One sublayer per distinct value of the split column. Order is
        # preserved as it appears in the GeoDataFrame.
        seen = []
        for v in layer.gdf[layer.split_by]:
            if v not in seen:
                seen.append(v)

        sublayers: list[folium.FeatureGroup] = []
        for value in seen:
            piece = layer.gdf[layer.gdf[layer.split_by] == value]
            if piece.empty:
                continue
            # Use split_label column if provided, else split_by itself
            if layer.split_label and layer.split_label in piece.columns:
                label = f"{value} — {piece[layer.split_label].iloc[0]}"
            else:
                label = str(value)
            fg = _add_geojson_layer(
                fmap=fmap,
                gdf=piece,
                name=label,
                style=layer.style,
                color_property=layer.color_property,
                tooltip_fields=layer.tooltip_fields,
                show=layer.show,
            )
            sublayers.append(fg)
        groups[layer.name] = sublayers

    # Companion zone-code labels: always-on text markers tied to their
    # parent layer. Rendered only when the parent was passed in (i.e. its
    # sidebar toggle is on) and kept OUT of the LayerControl so there's no
    # separate checkbox.
    for layer in vector_layers:
        if layer.label_gdf is not None and not layer.label_gdf.empty and layer.label_field:
            _add_label_markers(
                fmap, layer.label_gdf, layer.name, layer.label_field, layer.show
            )

    # Navigation marker (from the "go to coordinate/address" tool). When set,
    # it takes priority over fitting to the search results.
    if marker is not None:
        folium.Marker(
            location=[marker[0], marker[1]],
            tooltip="Local selecionado",
            icon=folium.Icon(color="red"),
        ).add_to(fmap)

    # Search highlight: a bright outline over the zones matching the current
    # potential-search criteria. control=False keeps it out of the
    # LayerControl (transient result, not a toggleable layer); fit_bounds
    # frames the matches (unless a nav marker already set the center).
    if highlight_gdf is not None and not highlight_gdf.empty:
        fg = folium.FeatureGroup(name="Zonas encontradas", show=True, control=False)
        folium.GeoJson(
            highlight_gdf.__geo_interface__,
            style_function=lambda _f: {
                "color": "#ff2d55",
                "weight": 3,
                "fillColor": "#ff2d55",
                "fillOpacity": 0.25,
            },
            tooltip=folium.GeoJsonTooltip(fields=["zone_code"], sticky=False),
        ).add_to(fg)
        fg.add_to(fmap)
        if marker is None:
            b = highlight_gdf.total_bounds  # minx, miny, maxx, maxy
            fmap.fit_bounds([[b[1], b[0]], [b[3], b[2]]])

    # The standard LayerControl handles non-grouped layers (rasters and
    # ungrouped vectors). GroupedLayerControl adds the grouped sublayers
    # below, with a heading per group.
    folium.LayerControl(collapsed=False).add_to(fmap)    # The standard LayerControl handles non-grouped layers (rasters and
    # ungrouped vectors). GroupedLayerControl adds the grouped sublayers
    # below, with a heading per group.
    folium.LayerControl(collapsed=False).add_to(fmap)
    if groups:
        GroupedLayerControl(
            groups={name: subs for name, subs in groups.items()},
            collapsed=False,
            exclusive_groups=False,
        ).add_to(fmap)

    return fmap
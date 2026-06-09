from __future__ import annotations

# Make the project root importable when Streamlit runs this file directly.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json

import geopandas as gpd
import streamlit as st
from streamlit_folium import st_folium

from src.app.inspect import inspect_point
from src.app.map_view import RasterLayerSpec, VectorLayerSpec, build_map

# ----- Page setup ---------------------------------------------------------

st.set_page_config(
    page_title="Construction Viability Map",
    page_icon="🗺️",
    layout="wide",
)

# ----- Region + paths -----------------------------------------------------

REGION_SLUG = "sao_jose_sc"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / f"data/processed/{REGION_SLUG}/manifest.json"
SLOPE_TIF = PROJECT_ROOT / f"data/interim/{REGION_SLUG}/slope.tif"
APP_PARQUET = PROJECT_ROOT / f"data/interim/{REGION_SLUG}/app.geoparquet"
DEM_TIF = next(
    iter(sorted((PROJECT_ROOT / f"data/raw/{REGION_SLUG}/topodata").glob("**/*.tif"))),
    None,
)

DEFAULT_CENTER_LAT = -27.595
DEFAULT_CENTER_LON = -48.615
DEFAULT_ZOOM = 12

# ----- Manifest loading --------------------------------------------------

@st.cache_data
def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        st.error(
            f"Manifest not found: {MANIFEST_PATH}\n\n"
            f"Run: python -m scripts.build_manifest"
        )
        st.stop()
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@st.cache_data
def load_vector_layer(parquet_path: str) -> gpd.GeoDataFrame:
    """Read a GeoParquet and reproject to EPSG:4326 (Folium's CRS)."""
    return gpd.read_parquet(parquet_path).to_crs("EPSG:4326")

@st.cache_data
def load_app_gdf() -> gpd.GeoDataFrame | None:
    """APP buffer in WGS84 for point-in-polygon inspection (or None)."""
    if APP_PARQUET.exists():
        return gpd.read_parquet(APP_PARQUET).to_crs("EPSG:4326")
    return None


@st.cache_data
def slope_threshold_png(threshold_pct: int) -> str:
    """Render (cached per threshold) the steep-slope highlight PNG."""
    from src.core.transform.slope_visualize import render_slope_threshold

    png = render_slope_threshold(
        SLOPE_TIF,
        PROJECT_ROOT / f"data/processed/{REGION_SLUG}",
        threshold_pct,
    )
    return str(png)


manifest = load_manifest()
layers = manifest["layers"]


# ----- Sidebar: per-layer controls ---------------------------------------

GROUP_LABELS = {
    "terrain": "Terreno",
    "environmental": "Ambiental",
    "master_plan": "Plano Diretor (LC 173/2024)",
    "reference": "Referência",
}
GROUP_ORDER = ["terrain", "environmental", "master_plan", "reference"]

st.sidebar.title("Camadas")
st.sidebar.caption(
    "Ative/desative camadas e ajuste a opacidade individualmente."
)

choices: dict[str, dict] = {}
slope_threshold = 0

for group_id in GROUP_ORDER:
    group_layers = [layer for layer in layers if layer["group"] == group_id]
    if not group_layers:
        continue

    with st.sidebar.expander(
        GROUP_LABELS[group_id],
        expanded=(group_id != "master_plan"),
    ):
        for layer in group_layers:
            show = st.checkbox(
                layer["name"],
                value=layer["default_visible"],
                key=f"show_{layer['id']}",
            )
            # Per-layer controls (opacity slider, sub-toggles, legend) are
            # rendered ONLY when the layer is enabled — keeps the sidebar
            # tidy: an unchecked layer shows just its name.
            opacity = float(layer["default_opacity"])
            show_labels = False
            if show:
                opacity = st.slider(
                    "Opacidade",
                    0.0, 1.0,
                    float(layer["default_opacity"]),
                    0.05,
                    key=f"opacity_{layer['id']}",
                    label_visibility="collapsed",
                )
                # "Show labels" sub-toggle, only for layers that have a
                # companion OCR'd label set.
                if layer.get("extras", {}).get("labels_path"):
                    # Indent + toggle (switch) so it reads as a sub-option
                    # of the layer above, not another top-level checkbox.
                    _, sub = st.columns([0.08, 0.92])
                    with sub:
                        show_labels = st.toggle(
                            "Mostrar rótulos",
                            value=False,
                            key=f"labels_{layer['id']}",
                        )
                # Color legend (e.g. the slope ramp), when present.
                legend = layer.get("extras", {}).get("legend") or []
                if legend:
                    rows = "".join(
                        f'<div style="display:flex;align-items:center;gap:6px;margin:1px 0;">'
                        f'<span style="width:14px;height:14px;border:1px solid #888;'
                        f'background:{stop["color"]};display:inline-block;"></span>'
                        f'<span style="font-size:0.78rem;">{stop["label"]}</span></div>'
                        for stop in legend
                    )
                    st.markdown(rows, unsafe_allow_html=True)

            choices[layer["id"]] = {
                "show": show,
                "opacity": opacity,
                "show_labels": show_labels,
            }

        # Slope highlight filter belongs to the Terreno group, right under
        # the Declividade layer it relates to.
        if group_id == "terrain":
            st.markdown("---")
            slope_threshold = st.slider(
                "Destacar declividade acima de (%)",
                min_value=0, max_value=100, value=0, step=5,
                key="slope_threshold",
                help="0 = desligado. Realça em vermelho onde a declividade passa do limite.",
            )

st.sidebar.markdown("---")
st.sidebar.caption(
    "Dados: IBGE (limite municipal), OSM (vias, edificações, hidrografia), "
    "INPE Topodata (declividade), LC 173/2024 (mapas do Plano Diretor)."
)


# ----- Build layer specs --------------------------------------------------

raster_specs: list[RasterLayerSpec] = []
vector_specs: list[VectorLayerSpec] = []

for layer in layers:
    choice = choices[layer["id"]]
    if not choice["show"]:
        # Skip layers the user has not enabled — keeps the Folium HTML
        # small even with many available layers.
        continue

    if layer["type"] == "raster":
        raster_specs.append(
            RasterLayerSpec(
                name=layer["name"],
                image=str(PROJECT_ROOT / layer["path"]),
                bounds_wgs84=layer["bounds_wgs84"],
                opacity=choice["opacity"],
                show=True,
            )
        )
    else:  # vector
        extras = layer.get("extras", {})
        style = dict(extras.get("style", {}))
        style["fillOpacity"] = choice["opacity"]

        is_master_plan = layer["group"] == "master_plan"

        # Companion OCR'd zone-code labels: shown only when the layer's
        # "Mostrar rótulos" sub-toggle is on.
        label_gdf = None
        labels_path = extras.get("labels_path")
        if labels_path and choice.get("show_labels"):
            label_gdf = load_vector_layer(str(PROJECT_ROOT / labels_path))

        vector_specs.append(
            VectorLayerSpec(
                name=layer["name"],
                gdf=load_vector_layer(str(PROJECT_ROOT / layer["path"])),
                style=style,
                show=True,
                color_property="color_hex" if is_master_plan else None,
                tooltip_fields=["zone_code", "zone_name"] if is_master_plan else [],
                split_by="zone_code" if is_master_plan else None,
                split_label="zone_name" if is_master_plan else None,
                label_gdf=label_gdf,
                label_field=extras.get("label_field", "zone_code"),
            )
        )


# Slope highlight overlay (Phase 8 filter). Appended after the per-layer
# specs so it draws on top of the slope ramp.
if slope_threshold > 0 and SLOPE_TIF.exists():
    slope_layer = next((lyr for lyr in layers if lyr["id"] == "slope"), None)
    if slope_layer is not None:
        raster_specs.append(
            RasterLayerSpec(
                name=f"Declividade > {slope_threshold}%",
                image=slope_threshold_png(slope_threshold),
                bounds_wgs84=slope_layer["bounds_wgs84"],
                opacity=0.7,
                show=True,
            )
        )

# ----- Render -------------------------------------------------------------

st.title("Mapa de Viabilidade de Construção — São José/SC")

# Always render the map. With no layers enabled, the user still sees
# the OSM basemap centered on the region — this is the default state.
fmap = build_map(
    center_lat=DEFAULT_CENTER_LAT,
    center_lon=DEFAULT_CENTER_LON,
    zoom=DEFAULT_ZOOM,
    raster_layers=raster_specs,
    vector_layers=vector_specs,
)
# returned_objects=["last_clicked"] makes st_folium report the last
# clicked coordinate (and rerun), which drives the inspect panel below.
map_state = st_folium(
    fmap, width=None, height=750, returned_objects=["last_clicked"]
)

# ----- Click-to-inspect panel --------------------------------------------
st.subheader("📍 Ponto inspecionado")
clicked = (map_state or {}).get("last_clicked")
if not clicked:
    st.caption(
        "Clique em qualquer ponto do mapa para ver declividade, elevação, "
        "APP e a zona do Plano Diretor das camadas ativas naquele ponto."
    )
else:
    # Only enabled Master Plan layers carry zone codes.
    plan_layers = [
        (spec.name, spec.gdf)
        for spec in vector_specs
        if "zone_code" in spec.gdf.columns
    ]
    info = inspect_point(
        clicked["lat"],
        clicked["lng"],
        slope_tif=SLOPE_TIF,
        dem_tif=DEM_TIF,
        app_gdf=load_app_gdf(),
        plan_layers=plan_layers,
    )

    c1, c2, c3 = st.columns(3)
    if "slope_deg" in info:
        pct = f" / {info['slope_pct']}%" if "slope_pct" in info else ""
        c1.metric("Declividade", f"{info['slope_deg']}°{pct}")
    else:
        c1.metric("Declividade", "—")
    c2.metric("Elevação", f"{info['elevation_m']} m" if "elevation_m" in info else "—")
    if "in_app" in info:
        c3.metric("Em APP?", "Sim" if info["in_app"] else "Não")
    else:
        c3.metric("Em APP?", "—")

    if info["zones"]:
        st.markdown("**Zonas do Plano Diretor neste ponto:**")
        for z in info["zones"]:
            st.markdown(f"- {z['layer']}: **{z['zone_code']}** — {z['zone_name']}")
    else:
        st.caption("Nenhuma camada do Plano Diretor ativa cobre este ponto.")

    st.caption(f"Coordenada: {clicked['lat']:.5f}, {clicked['lng']:.5f}")


# ----- Debug (collapsible) ------------------------------------------------

with st.expander("Manifest carregado (debug)"):
    st.json(manifest, expanded=False)
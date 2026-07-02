from __future__ import annotations

# Make the project root importable when Streamlit runs this file directly.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json

import geopandas as gpd
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from src.app.inspect import inspect_point, viability_verdict
from src.app.map_view import RasterLayerSpec, VectorLayerSpec, build_map

# ----- Page setup ---------------------------------------------------------

st.set_page_config(
    page_title="Construction Viability Map",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Trim the sidebar so the map gets more room (Streamlit's default is ~336px).
# Scope the override to the EXPANDED state only: when collapsed the rule
# drops out, so Streamlit's native collapse and the main-area reflow
# (reclaiming the freed width) keep working.
st.markdown(
    """
    <style>
      /* Compact, denser UI. Using font-size (not `zoom`) keeps Streamlit's
         BaseWeb sliders rendering correctly — page zoom desyncs the filled
         track from the thumb. */
      html { font-size: 14px; }
      section[data-testid="stSidebar"][aria-expanded="true"] {
        width: 280px !important;
        min-width: 280px !important;
        max-width: 280px !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
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

DEFAULT_CENTER_LAT = -27.605
DEFAULT_CENTER_LON = -48.63
DEFAULT_ZOOM = 13

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
def load_zone_params() -> dict:
    """Per-zone urbanistic parameters (LC 173/2024) for the inspect panel."""
    from src.core.transform.zoning_params import load_zone_parameters

    return load_zone_parameters(REGION_SLUG)


@st.cache_data
def load_subzones():
    """Hand-digitized map_03 subzones (WGS84) for exact zone resolution."""
    from src.core.transform.zoning_params import load_zoning_subzones

    return load_zoning_subzones(REGION_SLUG)


@st.cache_data
def load_aei():
    """AEI polygons (maps 04/05/07) in WGS84 — parameters prevail over zone."""
    from src.core.transform.zoning_params import load_aei_zones

    return load_aei_zones(REGION_SLUG)

@st.cache_data(show_spinner="Buscando endereço…")
def geocode_address(query: str):
    """Free-text address -> (lat, lng) via OSM Nominatim, biased to São
    José/SC. Cached so reruns don't re-hit the service; None on miss/error."""
    from geopy.geocoders import Nominatim

    try:
        loc = Nominatim(user_agent="construction-viability-map").geocode(
            f"{query}, São José, Santa Catarina, Brasil", timeout=8
        )
    except Exception:
        return None
    return (loc.latitude, loc.longitude) if loc else None

def _add_to_history(point, origin, info, verd):
    """Append the inspected point (with its info + verdict + typed input) to
    the in-memory history. Deduped by rounded coordinate."""
    hist = st.session_state.setdefault("history", [])
    key = (round(point["lat"], 5), round(point["lng"], 5))
    if any((round(e["lat"], 5), round(e["lng"], 5)) == key for e in hist):
        st.toast("Este ponto já está no histórico.")
        return
    st.session_state["history_seq"] = st.session_state.get("history_seq", 0) + 1
    hist.append(
        {
            "id": st.session_state["history_seq"],
            "kind": (origin or {}).get("kind", "click"),
            "typed": (origin or {}).get("typed", f"{point['lat']:.5f}, {point['lng']:.5f}"),
            "lat": point["lat"],
            "lng": point["lng"],
            "info": info,
            "verdict": verd,
        }
    )
    st.toast("Ponto salvo no histórico ✅")


def _history_details_md(entry):
    """Markdown block with every fact about a saved point (reused in the PDF)."""
    info, verd = entry["info"], entry["verdict"]
    lines = [
        f"**Localização digitada:** {entry['typed']}",
        f"**Coordenada:** {entry['lat']:.5f}, {entry['lng']:.5f}",
    ]
    if "slope_deg" in info:
        pct = f" / {info['slope_pct']}%" if "slope_pct" in info else ""
        lines.append(f"**Declividade:** {info['slope_deg']}°{pct}")
    if "elevation_m" in info:
        lines.append(f"**Elevação:** {info['elevation_m']} m")
    if "in_app" in info:
        lines.append(f"**Em APP:** {'Sim' if info['in_app'] else 'Não'}")
    pot = info.get("potential")
    if pot:
        pav = pot.get("pavimentos_max")
        pav_s = "Livre (até 25)" if pav == "LIVRE" else str(pav)
        zona = pot["zone_code"] + (
            f" (prevalece sobre {pot['prevalece_sobre']})" if pot.get("prevalece_sobre") else ""
        )
        lines.append(f"**Zona:** {zona}")
        lines.append(
            f"**Pavimentos:** {pav_s} · **CA:** {pot.get('ca_basico')} → "
            f"{pot.get('ca_maximo')} · **Lote mín.:** {pot.get('area_min_m2')} m²"
        )
    lines.append(f"**Veredito:** {verd['icon']} potencial {verd['level']}")
    lines += [f"- {r}" for r in verd["reasons"]]
    return "\n\n".join(lines)

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
GROUP_ORDER = ["master_plan", "terrain", "environmental", "reference"]

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
        expanded=False,
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

# ----- Zone potential search (Phase 10 M7) -------------------------------
with st.expander("🔎 Buscar zonas por potencial construtivo", expanded=False):
    _params = load_zone_params()
    f1, f2 = st.columns(2)
    min_pav = f1.slider("Pavimentos mínimos", 0, 25, 0, key="search_min_pav")
    min_ca = f2.slider("CA máximo mínimo", 0.0, 7.0, 0.0, 0.5, key="search_min_ca")

    matches = []
    for code, p in _params.items():
        if p.get("rural") or p.get("preservacao"):
            continue
        pav = p.get("pavimentos_max")
        pav_n = 25 if pav == "LIVRE" else (pav if isinstance(pav, int) else 0)
        ca_max = p.get("ca_maximo") if isinstance(p.get("ca_maximo"), (int, float)) else 0
        if pav_n >= min_pav and ca_max >= min_ca:
            matches.append({
                "Zona": code,
                "Pavimentos": "Livre (25)" if pav == "LIVRE" else pav,
                "CA básico": p.get("ca_basico"),
                "CA máx": p.get("ca_maximo"),
                "Lote mín (m²)": p.get("area_min_m2"),
            })
    matches.sort(key=lambda r: (r["CA máx"] or 0), reverse=True)
    st.dataframe(matches, use_container_width=True, hide_index=True)
    st.caption(
        f"{len(matches)} zona(s) atendem aos critérios · parâmetros LC 173/2024 "
        "(Tabela 01) · valores indicativos."
    )

    # Highlight the matches on the map — only when a filter is actually
    # active (the default 0/0 would otherwise light up every zone).
    search_highlight = None
    destacar = st.checkbox(
        "🗺️ Destacar as zonas encontradas no mapa",
        value=True,
        key="search_highlight",
    )
    if destacar and matches and (min_pav > 0 or min_ca > 0):
        _codes = {m["Zona"] for m in matches}
        _parts = [
            g[g["zone_code"].astype(str).isin(_codes)]
            for g in (load_subzones(), load_aei())
            if g is not None and not g.empty
        ]
        _parts = [g for g in _parts if not g.empty]
        if _parts:
            search_highlight = gpd.GeoDataFrame(
                pd.concat(_parts, ignore_index=True), crs="EPSG:4326"
            )

# ----- Navigate to a coordinate / address --------------------------------
with st.expander("🧭 Ir para um local (coordenada ou endereço)", expanded=False):
    _t_coord, _t_addr = st.tabs(["Coordenada", "Endereço"])
    with _t_coord:
        _c1, _c2 = st.columns(2)
        _in_lat = _c1.number_input(
            "Latitude", value=float(DEFAULT_CENTER_LAT), format="%.5f", key="nav_lat_in"
        )
        _in_lng = _c2.number_input(
            "Longitude", value=float(DEFAULT_CENTER_LON), format="%.5f", key="nav_lng_in"
        )
        if st.button("Ir para coordenada", key="nav_go_coord"):
            st.session_state["nav_target"] = (float(_in_lat), float(_in_lng))
            st.session_state["nav_input"] = {
                "kind": "coord",
                "typed": f"{float(_in_lat):.5f}, {float(_in_lng):.5f}",
            }
    with _t_addr:
        _addr = st.text_input(
            "Endereço", key="nav_addr_in", placeholder="Rua, bairro — São José/SC"
        )
        if st.button("Buscar endereço", key="nav_go_addr"):
            if _addr.strip():
                _hit = geocode_address(_addr.strip())
                if _hit:
                    st.session_state["nav_target"] = _hit
                    st.session_state["nav_input"] = {"kind": "address", "typed": _addr.strip()}
                else:
                    st.warning("Endereço não encontrado.")
    if st.session_state.get("nav_target"):
        _nt = st.session_state["nav_target"]
        _n1, _n2 = st.columns([3, 1])
        _n1.caption(f"📍 Local marcado: {_nt[0]:.5f}, {_nt[1]:.5f}")
        if _n2.button("Limpar", key="nav_clear"):
            del st.session_state["nav_target"]
            st.session_state.pop("nav_input", None)

# Map width presets. The choice sets the map:info column ratio + height.
# In "Amplo" the map goes full width and the inspect panel drops below it,
# maximized horizontally. The control itself is rendered *below* the map.
_MAP_LAYOUT = {
    "Compacto": {"ratio": [1.0, 1.0], "height": 520},
    "Médio": {"ratio": [1.8, 1.0], "height": 620},
    "Amplo": {"height": 720},
}
if "map_size" not in st.session_state:
    st.session_state["map_size"] = "Médio"
size_choice = st.session_state["map_size"]
_layout = _MAP_LAYOUT[size_choice]


def _render_map(height: int):
    # Always render the map; with no layers enabled the user still sees the
    # OSM basemap centered on São José — the default state.
    nav = st.session_state.get("nav_target")
    fmap = build_map(
        center_lat=nav[0] if nav else DEFAULT_CENTER_LAT,
        center_lon=nav[1] if nav else DEFAULT_CENTER_LON,
        zoom=16 if nav else DEFAULT_ZOOM,
        raster_layers=raster_specs,
        vector_layers=vector_specs,
        highlight_gdf=search_highlight,
        marker=nav,
    )    
    return st_folium(
        fmap, width=None, height=height, returned_objects=["last_clicked"]
    )


def _size_control():
    # Below the map; key drives st.session_state["map_size"].
    st.radio(
        "Tamanho do mapa",
        list(_MAP_LAYOUT.keys()),
        horizontal=True,
        key="map_size",
    )


def render_inspect_panel(map_state):
    st.subheader("📍 Ponto inspecionado")
    clicked = (map_state or {}).get("last_clicked")
    origin = None
    if clicked:
        origin = {"kind": "click", "typed": f"{clicked['lat']:.5f}, {clicked['lng']:.5f}"}
    elif st.session_state.get("nav_target"):
        _t = st.session_state["nav_target"]
        clicked = {"lat": _t[0], "lng": _t[1]}
        origin = st.session_state.get("nav_input") or {
            "kind": "coord",
            "typed": f"{_t[0]:.5f}, {_t[1]:.5f}",
        }
    if not clicked:  
        st.caption(
            "Clique em qualquer ponto do mapa para ver declividade, elevação, "
            "APP e a zona do Plano Diretor das camadas ativas naquele ponto."
        )
        return
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
        app_gdf=load_app_gdf(),
        plan_layers=plan_layers,
        zone_params=load_zone_params(),
        subzones=load_subzones(),
        aei_zones=load_aei(),
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

    pot = info.get("potential")
    if pot:
        titulo = pot["zone_code"]
        if pot.get("prevalece_sobre"):
            titulo = f"{pot['zone_code']} (prevalece sobre {pot['prevalece_sobre']})"
        st.markdown(f"**🏗️ Potencial construtivo — subzona {titulo}:**")
        p1, p2, p3 = st.columns(3)
        pav = pot.get("pavimentos_max")
        p1.metric("Pavimentos", "Livre (até 25)" if pav == "LIVRE" else str(pav))
        bas, mx = pot.get("ca_basico"), pot.get("ca_maximo")
        p2.metric("CA (básico → máx)", f"{bas} → {mx}" if mx is not None else str(bas))
        lote_min = pot.get("area_min_m2")
        p3.metric("Lote mínimo", f"{lote_min} m²" if lote_min else "—")

        # Buildable-area estimate: lot area × CA (defaults to the zone's min lot).
        if isinstance(bas, (int, float)):
            lote = st.number_input(
                "Área do lote (m²) — ajuste para o seu terreno",
                min_value=1.0, value=float(lote_min or 360), step=10.0,
                key="lote_area",
            )
            b1, b2 = st.columns(2)
            b1.metric(
                "Área construível (CA básico)",
                f"{lote * bas:,.0f}".replace(",", ".") + " m²",
            )
            if isinstance(mx, (int, float)):
                b2.metric(
                    "Área construível (CA máx, c/ outorga)",
                    f"{lote * mx:,.0f}".replace(",", ".") + " m²",
                )
        st.caption(
            "CA máximo via outorga onerosa · LC 173/2024 (Tabela 01) · "
            "valores indicativos — confirme na lei/prefeitura."
        )

    verd = viability_verdict(info)
    st.markdown(f"### {verd['icon']} Veredito: potencial **{verd['level']}**")
    for reason in verd["reasons"]:
        st.markdown(f"- {reason}")
    st.caption("Leitura indicativa — não substitui parecer técnico, jurídico ou ambiental.")

    # Justification (hidden by default): which segmentations cover the point
    # and which one defined the shown parameters (overlay precedence).
    with st.expander("ℹ️ Por que estes valores? (detalhes)"):
        det = []
        if pot and pot.get("prevalece_sobre"):
            det.append(
                f"Este ponto está na **{pot['zone_code']}** (Área de Especial Interesse), "
                f"que se **sobrepõe** à zona base **{pot['prevalece_sobre']}**. Pela regra de "
                "sobreposição da LC 173/2024 (Quadro 01/Anexo 16), os parâmetros da AEI "
                f"**prevalecem** — por isso os valores acima são os da {pot['zone_code']}."
            )
        elif pot:
            det.append(
                f"Este ponto está na zona base **{pot['zone_code']}**, e nenhuma Área de "
                "Especial Interesse com parâmetros próprios o cobre — por isso os valores "
                f"acima são os da própria {pot['zone_code']}."
            )
        if info.get("preservacao_aei"):
            det.append(
                f"O ponto está em **{info['preservacao_aei']}** (AEI Ambiental de preservação), "
                "onde a construção é em regra vedada — daí o veredito restritivo."
            )
        if info.get("in_app"):
            det.append(
                "O ponto cai em **APP** (faixa de preservação permanente, ex.: margem de rio), "
                "que impõe restrição independentemente da zona."
            )
        if not det:
            det.append(
                "Não há parâmetros construtivos resolvidos para este ponto (pode ser área "
                "rural, de preservação, ou fora das subzonas mapeadas)."
            )
        for d in det:
            st.markdown("- " + d)
        st.caption(
            "Resolução: subzona por ponto-em-polígono (17 subzonas desenhadas à mão) "
            "+ AEIs sobrepostas · LC 173/2024."
        )

    st.caption(f"Coordenada: {clicked['lat']:.5f}, {clicked['lng']:.5f}")

    if st.button("💾 Salvar no histórico", key="save_history"):
        _add_to_history(clicked, origin, info, verd)

if size_choice == "Amplo":
    # Map full width; controls + inspect panel below, maximized horizontally.
    map_state = _render_map(_layout["height"])
    _size_control()
    render_inspect_panel(map_state)
else:
    col_map, col_info = st.columns(_layout["ratio"], gap="medium")
    with col_map:
        map_state = _render_map(_layout["height"])
        _size_control()
    with col_info:
        render_inspect_panel(map_state)
        
# ----- Saved points history (below the map) -------------------------------
st.markdown("---")
st.subheader("🗂️ Histórico de pontos salvos")

_history = st.session_state.get("history", [])
if not _history:
    st.caption(
        "Nenhum ponto salvo ainda. Clique num ponto (ou busque um) e use "
        "**💾 Salvar no histórico** no painel do ponto."
    )
else:
    for _entry in list(_history):
        _col_exp, _col_del = st.columns([0.92, 0.08])
        with _col_exp:
            _v = _entry["verdict"]
            with st.expander(f"{_v['icon']} {_entry['typed']} — potencial {_v['level']}"):
                st.markdown(_history_details_md(_entry))
        if _col_del.button("➖", key=f"del_{_entry['id']}", help="Remover do histórico"):
            st.session_state["history"] = [e for e in _history if e["id"] != _entry["id"]]
            st.rerun()
    if st.button("🗑️ Limpar histórico", key="clear_history"):
        st.session_state["history"] = []
        st.rerun()
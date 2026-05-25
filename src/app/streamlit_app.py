from __future__ import annotations

# Make the project root importable when Streamlit runs this file directly.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import atexit
import json
import socket
import threading
import time
from urllib.request import urlopen

import geopandas as gpd
import streamlit as st
from streamlit_folium import st_folium

from src.app.map_view import RasterLayerSpec, VectorLayerSpec, build_map
from src.app.static_server import DEFAULT_PORT as STATIC_PORT
from src.app.static_server import create_app as create_static_app

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

DEFAULT_CENTER_LAT = -27.595
DEFAULT_CENTER_LON = -48.615
DEFAULT_ZOOM = 12

STATIC_SERVER_URL = f"http://127.0.0.1:{STATIC_PORT}"


# ----- Static server lifecycle -------------------------------------------

def _port_in_use(port: int) -> bool:
    """Return True if something is already listening on ``port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_health(url: str, timeout_s: float = 10.0) -> bool:
    """Poll the health endpoint until it answers or we time out."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urlopen(f"{url}/health", timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def _run_uvicorn_in_thread(app, port: int) -> None:
    """Start uvicorn on a background thread.

    Threads (not subprocesses) avoid the Windows multiprocessing-spawn
    pitfalls that bite Streamlit on Python 3.13.
    """
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Tell uvicorn to shut down cleanly when Streamlit exits.
    atexit.register(lambda: setattr(server, "should_exit", True))


@st.cache_resource
def ensure_static_server() -> None:
    """Start the static file server once per Streamlit session.

    Skips startup if the port is already taken (e.g. user re-ran
    Streamlit without killing the previous instance).
    """
    if _port_in_use(STATIC_PORT):
        return

    app = create_static_app(PROJECT_ROOT)
    _run_uvicorn_in_thread(app, STATIC_PORT)

    if not _wait_for_health(STATIC_SERVER_URL, timeout_s=10):
        st.warning(
            "Static server did not respond within 10 seconds — "
            "raster overlays may fail to load."
        )


ensure_static_server()


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


def to_static_url(absolute_path: str) -> str:
    """Convert an absolute filesystem path into the static server URL."""
    rel = Path(absolute_path).resolve().relative_to(PROJECT_ROOT)
    # Use forward slashes for URLs (Windows uses backslashes natively).
    return f"{STATIC_SERVER_URL}/files/{rel.as_posix()}"


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
            opacity = st.slider(
                "Opacidade",
                0.0, 1.0,
                float(layer["default_opacity"]),
                0.05,
                key=f"opacity_{layer['id']}",
                label_visibility="collapsed",
            )
            choices[layer["id"]] = {"show": show, "opacity": opacity}

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
                image_url=to_static_url(layer["path"]),
                bounds_wgs84=layer["bounds_wgs84"],
                opacity=choice["opacity"],
                show=True,
            )
        )
    else:  # vector
        style = dict(layer.get("extras", {}).get("style", {}))
        style["fillOpacity"] = choice["opacity"]

        # Master Plan layers carry per-feature colors in `color_hex`
        # and a `zone_code` worth showing on hover. Other vector
        # layers (e.g. APP) use a uniform style. They also split into
        # one sublayer per zone so the LayerControl shows each zone
        # as its own checkbox.
        is_master_plan = layer["group"] == "master_plan"
        vector_specs.append(
            VectorLayerSpec(
                name=layer["name"],
                gdf=load_vector_layer(layer["path"]),
                style=style,
                show=True,
                color_property="color_hex" if is_master_plan else None,
                tooltip_fields=["zone_code", "zone_name"] if is_master_plan else [],
                split_by="zone_code" if is_master_plan else None,
                split_label="zone_name" if is_master_plan else None,
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
st_folium(fmap, width=None, height=750, returned_objects=[])


# ----- Debug (collapsible) ------------------------------------------------

with st.expander("Manifest carregado (debug)"):
    st.json(manifest, expanded=False)
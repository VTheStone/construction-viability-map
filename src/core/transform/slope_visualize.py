"""Slope visualization.

Reads the slope GeoTIFF produced by ``slope.py`` and renders a colored
PNG suitable for overlaying on a web map. Output:

  - ``slope.png``  — RGBA image with transparent NoData pixels
  - ``slope.json`` — geographic bounds + colormap legend metadata

The PNG is web-ready (transparent background); the JSON is what the
Streamlit app reads to position the overlay and populate the legend.

Colormap rationale: ``RdYlGn_r`` (red-yellow-green, reversed) is a
diverging palette where green = low slope (build-friendly) and red =
high slope (problematic). The fixed range 0-45 degrees aligns with
the Brazilian Forest Code threshold for slope-induced APPs (areas
with slope > 45° are automatic Permanent Preservation Areas under
Law 12.651/2012).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import rasterio
from matplotlib import cm
from matplotlib.colors import LogNorm
from PIL import Image
from rasterio.warp import transform_bounds

logger = logging.getLogger(__name__)

# Log scale can't start at 0, so flat terrain clips to this floor (it
# gets the greenest color). Most of São José is gentle (<10°); a LOG
# ramp spreads those low values across the palette instead of crushing
# them all into one shade of green — that was the readability problem.
# 45° is the Forest Code APP threshold (Law 12.651/2012); above it = red.
SLOPE_FLOOR_DEG = 1.0
SLOPE_MAX_DEG = 45.0

# Matplotlib colormap. "_r" reverses it so low values are green
# (the natural mental model for "good to build").
COLORMAP_NAME = "RdYlGn_r"

# Legend stops shown in the app's sidebar, roughly log-spaced (1, 3, 8,
# 17, 30, 45) to match the log color ramp. Labels in Portuguese because
# the app's primary audience is Brazilian.
LEGEND_STOPS = [
    {"value": 1, "label": "Plano (≤1°)"},
    {"value": 3, "label": "Suave (3°)"},
    {"value": 8, "label": "Moderado (8°)"},
    {"value": 17, "label": "Acidentado (17°)"},
    {"value": 30, "label": "Forte (30°)"},
    {"value": 45, "label": "Íngreme (45°+, limite APP)"},
]


def _make_norm() -> LogNorm:
    """Shared log normalizer so the PNG and the legend use the same scale."""
    return LogNorm(vmin=SLOPE_FLOOR_DEG, vmax=SLOPE_MAX_DEG, clip=True)


def _hex_from_rgba(rgba: tuple[float, float, float, float]) -> str:
    """Convert matplotlib's normalized RGBA into a #RRGGBB string."""
    r, g, b, _ = rgba
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"


def _build_legend(colormap) -> list[dict]:
    """Return the legend stops with hex colors filled in from the colormap."""
    norm = _make_norm()
    legend = []
    for stop in LEGEND_STOPS:
        color = colormap(norm(stop["value"]))
        legend.append({**stop, "color": _hex_from_rgba(color)})
    return legend


def visualize_slope(
    slope_tif: Path,
    processed_dir: Path,
    *,
    band: int = 1,
    force: bool = False,
) -> tuple[Path, Path]:
    """Render a colored PNG + JSON sidecar from a slope GeoTIFF.

    Pipeline:
      1. Read the requested band (default band 1 = degrees).
      2. Mask NoData and out-of-range pixels.
      3. Normalize values to ``[SLOPE_MIN_DEG, SLOPE_MAX_DEG]``.
      4. Apply the ``RdYlGn_r`` colormap to produce RGBA.
      5. Make NoData pixels fully transparent.
      6. Save as PNG.
      7. Write a JSON sidecar with bounds, CRS, unit, and legend.

    Args:
        slope_tif: Path to the slope GeoTIFF from ``slope.py``.
        processed_dir: Output directory (e.g. ``data/processed/sao_jose_sc/``).
        band: Which band to render. 1 = degrees, 2 = percent.
        force: If True, regenerate even if the outputs already exist.

    Returns:
        Tuple ``(png_path, json_path)``.
    """
    png_path = processed_dir / "slope.png"
    json_path = processed_dir / "slope.json"

    if png_path.exists() and json_path.exists() and not force:
        logger.info("Using cached slope visualization: %s", png_path)
        return png_path, json_path

    processed_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(slope_tif) as src:
        values = src.read(band).astype(np.float32)
        nodata = src.nodata
        local_bounds = src.bounds
        local_crs = src.crs

    # Build NoData mask. Treat NoData, NaN, and negative values as missing.
    # slope.py now writes NoData (-9999) for pixels outside the municipal
    # boundary, so this catches them automatically.
    mask = np.zeros_like(values, dtype=bool)
    if nodata is not None:
        mask |= values == nodata
    mask |= np.isnan(values)
    mask |= values < 0


    # Clip to [floor, max]: flat terrain -> floor (greenest), >45° -> red.
    clipped = np.clip(values, SLOPE_FLOOR_DEG, SLOPE_MAX_DEG)

    # Apply the colormap on a LOG scale. matplotlib returns RGBA floats.
    colormap = cm.get_cmap(COLORMAP_NAME)
    norm = _make_norm()
    rgba_float = colormap(norm(clipped))  # shape (H, W, 4)

    # Zero out alpha on NoData pixels.
    rgba_float[mask, 3] = 0.0

    # Convert to uint8 for PNG.
    rgba_uint8 = (rgba_float * 255).astype(np.uint8)

    # Save PNG via Pillow.
    Image.fromarray(rgba_uint8, mode="RGBA").save(png_path)
    logger.info(
        "Slope PNG written: %s (%dx%d, %d valid pixels)",
        png_path,
        rgba_uint8.shape[1],
        rgba_uint8.shape[0],
        int((~mask).sum()),
    )

    # Convert bounds from the GeoTIFF's local CRS to WGS84 lat/lon for
    # use with Folium's ImageOverlay (which expects [[south, west],
    # [north, east]] in EPSG:4326).
    west, south, east, north = transform_bounds(
        local_crs, "EPSG:4326", *local_bounds, densify_pts=21
    )

    metadata = {
        "bounds_wgs84": {
            "west": west,
            "south": south,
            "east": east,
            "north": north,
        },
        "bounds_local": {
            "west": local_bounds.left,
            "south": local_bounds.bottom,
            "east": local_bounds.right,
            "north": local_bounds.top,
        },
        "crs_local": str(local_crs),
        "unit": "degrees",
        "value_range": [SLOPE_FLOOR_DEG, SLOPE_MAX_DEG],
        "scale": "log",
        "colormap": COLORMAP_NAME,
        "legend": _build_legend(colormap),
    }

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    logger.info("Slope metadata written: %s", json_path)
    return png_path, json_path
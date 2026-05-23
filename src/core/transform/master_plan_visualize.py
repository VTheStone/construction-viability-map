"""Master Plan map visualization.

Converts georeferenced Master Plan GeoTIFFs into web-ready PNG overlays
for use in the Streamlit app. The original TIFFs are kept on disk; the
PNGs are written under processed/<region>/master_plan/.

Why convert: Folium's ImageOverlay only accepts formats the browser can
render (PNG, JPG). GeoTIFF is not browser-friendly.

Why downsample: source TIFFs are 7000+ pixels wide (full A0 plot
resolution). Embedded as base64 in the Folium HTML, ten of those would
saturate Streamlit's 200 MB message limit. Downsampling to ~2000 px on
the longest side preserves all visual cues at typical zoom levels while
shrinking each PNG by ~10x.

Transparency strategy: the source PDFs have white-ish paper backgrounds.
We treat near-white pixels (above WHITE_THRESHOLD across all channels)
as transparent so the OpenStreetMap basemap shows through. Colored
features (zoning patches, risk markers, etc.) stay opaque.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import transform_bounds

logger = logging.getLogger(__name__)

# Pixels whose R, G, B are ALL above this threshold (0-255) are considered
# background "paper" and become transparent.
WHITE_THRESHOLD = 235

# Cap the longest side of the output PNG (pixels). The TIFFs from QGIS
# are ~7000 px wide; 2000 is enough for typical web zoom levels and
# slashes the base64 payload by ~10x.
DEFAULT_MAX_DIMENSION = 2000


@dataclass(frozen=True)
class MasterPlanPNG:
    """Result of converting one Master Plan GeoTIFF to PNG."""

    map_id: str
    png_path: Path
    bounds_wgs84: dict[str, float]


def _read_tif_rgb(tif_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read the first 3 bands of a GeoTIFF, replicating if grayscale."""
    with rasterio.open(tif_path) as src:
        if src.count >= 3:
            r = src.read(1)
            g = src.read(2)
            b = src.read(3)
        else:
            r = g = b = src.read(1)
    return (
        r.astype(np.uint8, copy=False),
        g.astype(np.uint8, copy=False),
        b.astype(np.uint8, copy=False),
    )


def _build_rgba(
    r: np.ndarray, g: np.ndarray, b: np.ndarray
) -> np.ndarray:
    """Combine R, G, B with a white-cutoff alpha into a single (H, W, 4) array."""
    near_white = (r >= WHITE_THRESHOLD) & (g >= WHITE_THRESHOLD) & (b >= WHITE_THRESHOLD)
    alpha = np.where(near_white, 0, 255).astype(np.uint8)
    return np.dstack([r, g, b, alpha])


def _downsample(rgba: np.ndarray, max_dimension: int) -> np.ndarray:
    """Resize so the longest side is at most `max_dimension`.

    Uses Pillow with LANCZOS resampling (best quality for downscaling).
    Returns the array unchanged if already small enough.
    """
    h, w = rgba.shape[:2]
    longest = max(h, w)
    if longest <= max_dimension:
        return rgba

    scale = max_dimension / longest
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))

    img = Image.fromarray(rgba, mode="RGBA")
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return np.array(img)


def _bounds_wgs84_from_tif(tif_path: Path) -> dict[str, float]:
    """Read a GeoTIFF's bounds and reproject to WGS84.

    The bounds come from the source TIFF, not the downsampled PNG —
    they're identical because resampling preserves the geographic
    footprint, only the pixel density changes.
    """
    with rasterio.open(tif_path) as src:
        west, south, east, north = transform_bounds(
            src.crs, "EPSG:4326", *src.bounds, densify_pts=21
        )
    return {"west": west, "south": south, "east": east, "north": north}


def visualize_master_plan(
    tif_path: Path,
    map_id: str,
    out_dir: Path,
    *,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    force: bool = False,
) -> MasterPlanPNG:
    """Convert one Master Plan GeoTIFF to a downsampled PNG overlay.

    Args:
        tif_path: Source georeferenced GeoTIFF.
        map_id: Identifier (e.g. ``"map_02"``); used for the output filename.
        out_dir: Where to write the PNG.
        max_dimension: Max pixels on the longest side of the output PNG.
        force: If True, re-render even if the PNG exists.

    Returns:
        MasterPlanPNG with the output path and WGS84 bounds.
    """
    png_path = out_dir / f"{map_id}.png"

    if png_path.exists() and not force:
        logger.info("Using cached Master Plan PNG: %s", png_path)
        return MasterPlanPNG(
            map_id=map_id,
            png_path=png_path,
            bounds_wgs84=_bounds_wgs84_from_tif(tif_path),
        )

    logger.info("Rendering %s -> %s", tif_path.name, png_path.name)
    r, g, b = _read_tif_rgb(tif_path)
    rgba = _build_rgba(r, g, b)
    rgba = _downsample(rgba, max_dimension)

    out_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(png_path, optimize=True)

    size_mb = png_path.stat().st_size / 1024 / 1024
    logger.info(
        "  -> %s (%dx%d, %.2f MB)",
        png_path.name,
        rgba.shape[1],
        rgba.shape[0],
        size_mb,
    )

    return MasterPlanPNG(
        map_id=map_id,
        png_path=png_path,
        bounds_wgs84=_bounds_wgs84_from_tif(tif_path),
    )


def visualize_all_master_plan_overlays(
    overlays: list,  # list[OverlayMetadata]
    out_dir: Path,
    *,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    force: bool = False,
) -> list[MasterPlanPNG]:
    """Convert every Master Plan overlay to PNG."""
    results = []
    for overlay in overlays:
        results.append(
            visualize_master_plan(
                tif_path=overlay.path,
                map_id=overlay.id,
                out_dir=out_dir,
                max_dimension=max_dimension,
                force=force,
            )
        )

    # Summary
    total_mb = sum(r.png_path.stat().st_size for r in results) / 1024 / 1024
    logger.info(
        "Generated %d PNGs, total %.1f MB on disk",
        len(results),
        total_mb,
    )
    return results
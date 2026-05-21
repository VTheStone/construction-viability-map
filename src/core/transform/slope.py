"""Slope calculation from a DEM (Digital Elevation Model).

Reads a DEM raster (e.g. Topodata GeoTIFF), reprojects to a projected
CRS (UTM-class, where pixel units are meters), clips to a region's
boundary, and computes per-pixel slope using the Horn (1981) method.

Output is a two-band GeoTIFF:
    Band 1: slope in degrees
    Band 2: slope in percent (rise/run × 100)

Two bands let downstream consumers pick whichever unit they need
without recomputing. The cost is double disk space (still small —
São José area in UTM 22S at 30 m/pixel is ~1 MB per band).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
from geopandas import GeoDataFrame
from rasterio.mask import mask as rio_mask
from rasterio.warp import Resampling, calculate_default_transform, reproject

logger = logging.getLogger(__name__)


def _reproject_dem(
    src_path: Path,
    dst_path: Path,
    dst_crs: str,
) -> None:
    """Reproject ``src_path`` to ``dst_crs`` and write to ``dst_path``.

    Bilinear resampling preserves the smoothness of elevation data
    (nearest-neighbour would introduce staircase artifacts that ruin
    slope calculations).
    """
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds
        )
        profile = src.profile.copy()
        profile.update(
            crs=dst_crs,
            transform=transform,
            width=width,
            height=height,
        )

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dst_path, "w", **profile) as dst:
            for band_idx in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, band_idx),
                    destination=rasterio.band(dst, band_idx),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                )

    logger.info("Reprojected DEM to %s -> %s", dst_crs, dst_path)


def _clip_to_boundary(
    src_path: Path,
    boundary: GeoDataFrame,
    dst_path: Path,
) -> None:
    """Clip ``src_path`` to the boundary polygon and write to ``dst_path``.

    Assumes ``boundary`` is already in the same CRS as the raster.
    """
    with rasterio.open(src_path) as src:
        if str(boundary.crs) != str(src.crs):
            raise ValueError(
                f"Boundary CRS {boundary.crs} does not match raster CRS "
                f"{src.crs}. Reproject the boundary first."
            )
        geoms = boundary.geometry.tolist()
        clipped_data, clipped_transform = rio_mask(
            src, geoms, crop=True, all_touched=True
        )
        profile = src.profile.copy()
        profile.update(
            height=clipped_data.shape[1],
            width=clipped_data.shape[2],
            transform=clipped_transform,
        )

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst_path, "w", **profile) as dst:
        dst.write(clipped_data)

    logger.info("Clipped DEM to boundary -> %s", dst_path)


def _compute_slope_arrays(
    elevation: np.ndarray,
    pixel_width: float,
    pixel_height: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute slope from an elevation array using the Horn method.

    Args:
        elevation: 2D array of elevation values (meters).
        pixel_width: Horizontal pixel size in meters (positive).
        pixel_height: Vertical pixel size in meters (positive).

    Returns:
        Tuple ``(slope_degrees, slope_percent)``, same shape as ``elevation``.
        Edge pixels (1-pixel border) are returned as 0.
    """
    # Horn (1981) 3x3 gradient kernel.
    # dz_dx ≈ ((c + 2f + i) - (a + 2d + g)) / (8 * pixel_width)
    # dz_dy ≈ ((g + 2h + i) - (a + 2b + c)) / (8 * pixel_height)
    # where the 3x3 window labels are:
    #     a b c
    #     d e f
    #     g h i

    elev = elevation.astype(np.float32, copy=False)
    h, w = elev.shape

    dz_dx = np.zeros_like(elev)
    dz_dy = np.zeros_like(elev)

    # Slice neighbours; central window is rows[1:-1], cols[1:-1].
    a = elev[0:-2, 0:-2]
    b = elev[0:-2, 1:-1]
    c = elev[0:-2, 2:]
    d = elev[1:-1, 0:-2]
    f = elev[1:-1, 2:]
    g = elev[2:, 0:-2]
    hh = elev[2:, 1:-1]
    i = elev[2:, 2:]

    dz_dx[1:-1, 1:-1] = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * pixel_width)
    dz_dy[1:-1, 1:-1] = ((g + 2 * hh + i) - (a + 2 * b + c)) / (8 * pixel_height)

    slope_rad = np.arctan(np.sqrt(dz_dx**2 + dz_dy**2))
    slope_deg = np.degrees(slope_rad).astype(np.float32)
    slope_pct = (np.tan(slope_rad) * 100.0).astype(np.float32)

    return slope_deg, slope_pct


def compute_slope(
    dem_path: Path,
    boundary: GeoDataFrame,
    interim_dir: Path,
    *,
    target_crs: str | None = None,
    force: bool = False,
) -> Path:
    """Compute slope from a DEM and save as a 2-band GeoTIFF.

    Pipeline:
        1. Reproject DEM to ``target_crs`` (defaults to boundary's CRS).
        2. Clip to ``boundary``.
        3. Compute per-pixel slope (Horn method).
        4. Write a 2-band GeoTIFF: degrees (band 1), percent (band 2).

    Args:
        dem_path: Source DEM GeoTIFF (e.g. Topodata, in EPSG:4326).
        boundary: Municipal boundary as a GeoDataFrame in a projected CRS.
        interim_dir: Where to store intermediate and final outputs.
            Final slope file is ``<interim_dir>/slope.tif``.
        target_crs: CRS for the slope calculation. Defaults to ``boundary.crs``.
            Must be projected (meters), not geographic (degrees).
        force: If True, recompute even if the output exists.

    Returns:
        Path to the 2-band slope GeoTIFF.
    """
    dst_path = interim_dir / "slope.tif"
    if dst_path.exists() and not force:
        logger.info("Using cached slope raster: %s", dst_path)
        return dst_path

    if target_crs is None:
        target_crs = str(boundary.crs)

    # Step 1: reproject DEM to projected CRS (e.g. UTM 22S)
    reprojected = interim_dir / "dem_reprojected.tif"
    _reproject_dem(dem_path, reprojected, target_crs)

    # Step 2: clip to the municipal boundary
    clipped = interim_dir / "dem_clipped.tif"
    _clip_to_boundary(reprojected, boundary, clipped)

    # Step 3 + 4: compute slope and write as 2-band GeoTIFF
    with rasterio.open(clipped) as src:
        elevation = src.read(1)
        # In rasterio, transform.a is pixel width, |transform.e| is pixel height.
        pixel_w = abs(src.transform.a)
        pixel_h = abs(src.transform.e)
        slope_deg, slope_pct = _compute_slope_arrays(elevation, pixel_w, pixel_h)

        profile = src.profile.copy()
        profile.update(
            count=2,
            dtype=rasterio.float32,
            compress="lzw",
        )

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dst_path, "w", **profile) as dst:
            dst.write(slope_deg, 1)
            dst.write(slope_pct, 2)
            dst.set_band_description(1, "slope_degrees")
            dst.set_band_description(2, "slope_percent")

    # Cleanup intermediates (optional — keeps interim_dir cleaner)
    reprojected.unlink(missing_ok=True)
    clipped.unlink(missing_ok=True)

    logger.info(
        "Slope raster written: %s (%dx%d px, 2 bands)",
        dst_path,
        slope_deg.shape[1],
        slope_deg.shape[0],
    )
    return dst_path
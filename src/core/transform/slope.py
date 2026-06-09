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


# Sentinel value for pixels outside the municipal boundary. -9999 is the
# conventional NoData marker for elevation/slope rasters and is safely
# outside the valid range (real elevation never goes that negative).
NODATA_VALUE = -9999.0


def _clip_to_boundary(
    src_path: Path,
    boundary: GeoDataFrame,
    dst_path: Path,
) -> None:
    """Clip ``src_path`` to the boundary polygon and write to ``dst_path``.

    Pixels outside the boundary are set to NODATA_VALUE and the NoData
    flag is declared in the output profile, so downstream tools
    (rasterio, GDAL, QGIS) treat them as missing data rather than
    flat-zero terrain.

    Assumes ``boundary`` is already in the same CRS as the raster.
    """
    with rasterio.open(src_path) as src:
        # Compare CRSs by identity, not by string: a boundary round-tripped
        # through GeoParquet carries a verbose PROJJSON whose str() differs
        # from the raster's compact "EPSG:31982" even though they are the
        # same CRS. EPSG code (with a WKT fallback) is representation-stable.
        b_crs, r_crs = boundary.crs, src.crs
        same_crs = b_crs is not None and r_crs is not None and (
            (b_crs.to_epsg() is not None and b_crs.to_epsg() == r_crs.to_epsg())
            or b_crs.to_wkt() == r_crs.to_wkt()
        )
        if not same_crs:
            raise ValueError(
                f"Boundary CRS {boundary.crs} does not match raster CRS "
                f"{src.crs}. Reproject the boundary first."
            )
        geoms = boundary.geometry.tolist()
        # filled=True writes NODATA_VALUE outside the polygon; combined with
        # nodata=NODATA_VALUE in the profile this becomes a real NoData mask.
        clipped_data, clipped_transform = rio_mask(
            src,
            geoms,
            crop=True,
            all_touched=True,
            nodata=NODATA_VALUE,
            filled=True,
        )
        profile = src.profile.copy()
        profile.update(
            height=clipped_data.shape[1],
            width=clipped_data.shape[2],
            transform=clipped_transform,
            nodata=NODATA_VALUE,
            dtype=rasterio.float32,
        )

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst_path, "w", **profile) as dst:
        dst.write(clipped_data.astype(np.float32))

    logger.info(
        "Clipped DEM to boundary (NoData=%s) -> %s", NODATA_VALUE, dst_path
    )


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
    # NaN inputs would propagate through the gradient and contaminate
    # neighbouring pixels. The caller marks NoData as NaN, but we run
    # the gradient on a 0-filled copy and reapply the mask outside.
    nan_mask = np.isnan(elev)
    elev = np.where(nan_mask, 0.0, elev)
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
    sea_level_m: float = 0.0,
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

    # Step 3 + 4: compute slope and write as 2-band GeoTIFF.
    # Carry the NoData mask through so the final slope file declares
    # pixels outside the municipal boundary as missing.
    with rasterio.open(clipped) as src:
        elevation = src.read(1)
        nodata_in = src.nodata
        # In rasterio, transform.a is pixel width, |transform.e| is pixel height.
        pixel_w = abs(src.transform.a)
        pixel_h = abs(src.transform.e)

        # Mark both true NoData AND sea-level pixels as missing. The IBGE
        # municipal boundary extends into Baía Sul, where the DEM reads
        # ~0 m; without this, open water slope-computes to flat (green)
        # "buildable" terrain. Raise sea_level_m by 1-2 m if a thin green
        # fringe survives along the shoreline (bilinear resampling blends
        # land and sea near the coast).
        nodata_mask = elevation <= sea_level_m
        if nodata_in is not None:
            nodata_mask |= elevation == nodata_in
        elevation_clean = np.where(nodata_mask, np.nan, elevation)

        slope_deg, slope_pct = _compute_slope_arrays(
            elevation_clean, pixel_w, pixel_h
        )

        # Reapply NoData to slope outputs: pixels outside the boundary
        # must remain marked as missing.
        slope_deg[nodata_mask] = NODATA_VALUE
        slope_pct[nodata_mask] = NODATA_VALUE

        # Band 3 carries elevation (meters) so the app can report it on
        # click without shipping the raw ~70 MB DEM to the deploy. NoData
        # (sea / outside boundary) matches the slope bands.
        elev_band = np.where(nodata_mask, NODATA_VALUE, elevation).astype(np.float32)

        profile = src.profile.copy()
        profile.update(
            count=3,
            dtype=rasterio.float32,
            compress="lzw",
            nodata=NODATA_VALUE,
        )

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dst_path, "w", **profile) as dst:
            dst.write(slope_deg.astype(np.float32), 1)
            dst.write(slope_pct.astype(np.float32), 2)
            dst.write(elev_band, 3)
            dst.set_band_description(1, "slope_degrees")
            dst.set_band_description(2, "slope_percent")
            dst.set_band_description(3, "elevation_m")

    # Cleanup intermediates (optional — keeps interim_dir cleaner)
    reprojected.unlink(missing_ok=True)
    clipped.unlink(missing_ok=True)

    logger.info(
        "Slope raster written: %s (%dx%d px, 3 bands: deg/pct/elev)",
        dst_path,
        slope_deg.shape[1],
        slope_deg.shape[0],
    )
    return dst_path
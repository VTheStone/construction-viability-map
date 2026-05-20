"""Topodata (INPE) DEM ingestion.

Topodata provides ~30m DEM tiles for Brazil, served as a 1:250,000 grid
of 1° x 1.5° sheets. Each sheet has a 6-character code following the
``LAHLON`` convention:
  - LA  = absolute latitude of the upper-left corner
  - H   = hemisphere (S/N)
  - LON = longitude in either "nn_" (whole degrees) or "nn5" (n.5)

Example: ``27S495`` covers the sheet whose upper-left corner is at
27°S, 49.5°W — which includes the São José/SC area.

The INPE server runs HTTP only (no HTTPS), so we set ``verify=False``
in requests and silence the related warning. Tiles are zipped GeoTIFFs.

Source index: http://www.dsr.inpe.br/topodata/data/geotiff/
"""

from __future__ import annotations

import logging
import shutil
import warnings
import zipfile
from pathlib import Path

import requests
from urllib3.exceptions import InsecureRequestWarning

logger = logging.getLogger(__name__)

# Topodata sheet-code for the São José/SC area. Used as a default for sanity
# checks; callers should pass an explicit sheet code per region.
SAO_JOSE_DEFAULT_SHEET = "27S495"

# Topodata products. ``ZN`` = altimetry (elevation), the only one we need.
TOPODATA_PRODUCT = "ZN"

TOPODATA_BASE_URL = "http://www.dsr.inpe.br/topodata/data/geotiff/"

# INPE serves HTTP only as of 2026. Suppress the urllib3 warning when
# verify=False is used.
warnings.filterwarnings("ignore", category=InsecureRequestWarning)

# Topodata GeoTIFFs are distributed without an embedded CRS — INPE notes that
# users are expected to set it themselves. The data follow WGS84 (lat/lon),
# the same reference frame as the source SRTM data.
TOPODATA_CRS = "EPSG:4326"


def open_dem(tif_path: Path):
    """Open a Topodata GeoTIFF with WGS84 CRS attached.

    Topodata files are distributed without an embedded CRS. This wrapper
    opens the file and overrides the missing CRS so downstream rasterio
    operations work correctly.

    Args:
        tif_path: Path to the extracted Topodata .tif.

    Returns:
        A rasterio dataset reader opened in read mode.
    """
    import rasterio
    from rasterio.crs import CRS

    dataset = rasterio.open(tif_path, "r+")
    if dataset.crs is None:
        logger.info("Topodata GeoTIFF has no embedded CRS; setting to %s", TOPODATA_CRS)
        dataset.crs = CRS.from_string(TOPODATA_CRS)
    return dataset

def _tile_filename(sheet_code: str, product: str = TOPODATA_PRODUCT) -> str:
    """Return the canonical Topodata ZIP filename for a sheet + product."""
    return f"{sheet_code}{product}.zip"


def _tile_url(sheet_code: str, product: str = TOPODATA_PRODUCT) -> str:
    """Build the Topodata download URL for a sheet + product."""
    return TOPODATA_BASE_URL + _tile_filename(sheet_code, product)


def _download_with_progress(url: str, dest: Path, chunk_size: int = 1024 * 64) -> None:
    """Stream a file to disk, logging progress every ~1 MB.

    Uses ``verify=False`` because INPE's Topodata server is HTTP-only.
    """
    logger.info("Downloading %s", url)
    response = requests.get(url, stream=True, timeout=120, verify=False)
    response.raise_for_status()

    total = int(response.headers.get("Content-Length", 0))
    downloaded = 0
    next_log_at = 1024 * 1024

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)
            if downloaded >= next_log_at:
                if total:
                    pct = 100.0 * downloaded / total
                    logger.info("  ... %d/%d bytes (%.1f%%)", downloaded, total, pct)
                else:
                    logger.info("  ... %d bytes", downloaded)
                next_log_at += 1024 * 1024

    logger.info("Saved to %s (%d bytes)", dest, downloaded)


def download_tile(
    sheet_code: str,
    cache_dir: Path,
    *,
    product: str = TOPODATA_PRODUCT,
    force: bool = False,
) -> Path:
    """Download (or fetch from cache) a Topodata sheet ZIP.

    Args:
        sheet_code: 6-character LAHLON code (e.g. ``"27S495"``).
        cache_dir: Where to cache the ZIP and its extracted contents.
            Typically ``data/raw/<region_slug>/topodata/``.
        product: Topodata product code (default ``"ZN"`` = altimetry).
        force: If True, re-download even if cached.

    Returns:
        Path to the cached ZIP file.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / _tile_filename(sheet_code, product)

    if zip_path.exists() and not force:
        logger.info("Using cached Topodata tile: %s", zip_path)
        return zip_path

    url = _tile_url(sheet_code, product)
    _download_with_progress(url, zip_path)
    return zip_path


def _extract_zip(zip_path: Path, extract_dir: Path) -> Path:
    """Extract a ZIP archive into ``extract_dir`` and return that dir."""
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
    logger.info("Extracted to %s", extract_dir)
    return extract_dir


def _find_geotiff(directory: Path) -> Path:
    """Return the first ``*.tif`` found anywhere under ``directory``."""
    tifs = list(directory.rglob("*.tif"))
    if not tifs:
        raise FileNotFoundError(f"No .tif file under {directory}")
    if len(tifs) > 1:
        logger.warning(
            "Multiple .tif files found under %s; picking %s",
            directory,
            tifs[0],
        )
    return tifs[0]


def load_dem(
    sheet_code: str,
    cache_dir: Path,
    *,
    product: str = TOPODATA_PRODUCT,
    force: bool = False,
) -> Path:
    """Return the local path to the DEM GeoTIFF for a Topodata sheet.

    Pipeline: download ZIP -> extract -> locate .tif -> return its path.

    Unlike the IBGE and OSM loaders, this function returns a *path*
    instead of an in-memory object: the GeoTIFF can be ~50-100 MB and
    is typically opened lazily by rasterio/rioxarray downstream.

    Args:
        sheet_code: 6-character LAHLON code.
        cache_dir: Local cache directory.
        product: Topodata product code (default ``"ZN"``).
        force: If True, re-download and re-extract.

    Returns:
        Path to the extracted GeoTIFF.
    """
    zip_path = download_tile(sheet_code, cache_dir, product=product, force=force)
    extract_dir = cache_dir / f"{sheet_code}{product}"

    if not extract_dir.exists() or force:
        _extract_zip(zip_path, extract_dir)

    tif_path = _find_geotiff(extract_dir)
    logger.info("Topodata DEM ready at %s", tif_path)
    return tif_path
"""IBGE data ingestion.

Downloads the IBGE municipal mesh shapefile for a Brazilian state and
filters it down to a single municipality by IBGE code.

Source: https://geoftp.ibge.gov.br/organizacao_do_territorio/
        malhas_territoriais/malhas_municipais/municipio_<year>/UFs/<UF>/

The state shapefile is ~5-50 MB depending on the state; we cache the
ZIP under data/raw/<region_slug>/ibge/ so subsequent runs are instant.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

import geopandas as gpd
import requests
from geopandas import GeoDataFrame

logger = logging.getLogger(__name__)

# Default year of the municipal mesh. Newer years exist; pin one for reproducibility.
DEFAULT_YEAR = 2022

IBGE_BASE_URL = (
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/"
    "malhas_territoriais/malhas_municipais/municipio_{year}/UFs/{uf}/"
)
IBGE_FILENAME_PATTERN = "{uf}_Municipios_{year}.zip"


def _state_zip_url(uf: str, year: int) -> str:
    """Build the canonical IBGE URL for a state's municipal-mesh ZIP."""
    base = IBGE_BASE_URL.format(year=year, uf=uf)
    filename = IBGE_FILENAME_PATTERN.format(uf=uf, year=year)
    return base + filename


def _download_with_progress(url: str, dest: Path, chunk_size: int = 1024 * 64) -> None:
    """Stream a file to disk, logging progress every ~1 MB."""
    logger.info("Downloading %s", url)
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()

    total = int(response.headers.get("Content-Length", 0))
    downloaded = 0
    next_log_at = 1024 * 1024  # 1 MB

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


def download_state_mesh(
    uf: str,
    cache_dir: Path,
    year: int = DEFAULT_YEAR,
    force: bool = False,
) -> Path:
    """Download (or fetch from cache) the IBGE state municipal-mesh ZIP.

    Args:
        uf: Two-letter state code (e.g. ``"SC"``).
        cache_dir: Directory to store the ZIP and its extracted contents.
            Typically ``data/raw/<region_slug>/ibge/``.
        year: Municipal-mesh year. Defaults to ``DEFAULT_YEAR``.
        force: If True, re-download even if cached.

    Returns:
        Path to the cached ZIP file.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / IBGE_FILENAME_PATTERN.format(uf=uf, year=year)

    if zip_path.exists() and not force:
        logger.info("Using cached IBGE ZIP: %s", zip_path)
        return zip_path

    url = _state_zip_url(uf, year)
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


def _find_shapefile(directory: Path) -> Path:
    """Return the first ``*.shp`` found anywhere under ``directory``."""
    shapefiles = list(directory.rglob("*.shp"))
    if not shapefiles:
        raise FileNotFoundError(f"No .shp file under {directory}")
    if len(shapefiles) > 1:
        logger.warning(
            "Multiple .shp files found under %s; picking %s",
            directory,
            shapefiles[0],
        )
    return shapefiles[0]


def _find_code_column(gdf: GeoDataFrame) -> str:
    """Return the IBGE municipal-code column, tolerating naming variations."""
    candidates = ["CD_MUN", "CD_GEOCMU", "CD_GEOCODI", "CODIBGE"]
    for col in candidates:
        if col in gdf.columns:
            return col
    raise KeyError(
        f"No IBGE municipal-code column found. Looked for {candidates}, "
        f"got {list(gdf.columns)}"
    )


def load_municipal_boundary(
    ibge_code: str,
    uf: str,
    cache_dir: Path,
    year: int = DEFAULT_YEAR,
    force: bool = False,
) -> GeoDataFrame:
    """Return the municipal boundary as a GeoDataFrame (EPSG:4326).

    Pipeline: download state ZIP -> extract -> read shapefile -> filter
    by IBGE code -> return.

    Args:
        ibge_code: 7-digit IBGE municipal code (e.g. ``"4216602"``).
        uf: Two-letter state code (e.g. ``"SC"``).
        cache_dir: Local cache directory.
        year: Municipal-mesh year.
        force: If True, re-download even if cached.

    Returns:
        Single-row GeoDataFrame in EPSG:4326 with the boundary geometry.
        Reprojection to the region's local CRS is the caller's responsibility.
    """
    zip_path = download_state_mesh(uf, cache_dir, year=year, force=force)
    extract_dir = cache_dir / f"{uf}_municipios_{year}"

    if not extract_dir.exists() or force:
        _extract_zip(zip_path, extract_dir)

    shp_path = _find_shapefile(extract_dir)
    logger.info("Reading shapefile: %s", shp_path)
    gdf = gpd.read_file(shp_path)

    code_col = _find_code_column(gdf)
    gdf[code_col] = gdf[code_col].astype(str)

    municipality = gdf[gdf[code_col] == str(ibge_code)].copy()
    if municipality.empty:
        raise ValueError(
            f"IBGE code {ibge_code} not found in state {uf} "
            f"({len(gdf)} municipalities loaded)"
        )

    logger.info(
        "Loaded municipality %s (IBGE %s) from %s",
        municipality.iloc[0].get("NM_MUN", "?"),
        ibge_code,
        shp_path.name,
    )
    return municipality.reset_index(drop=True)
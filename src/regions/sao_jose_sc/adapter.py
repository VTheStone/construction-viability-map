"""São José / Santa Catarina region adapter.

Implements the RegionAdapter interface for the city of São José, SC.
Wires city-specific parameters to the generic core/ingest loaders and
to the Master Plan overlay loader.

Zoning and risk vector layers are placeholders until Maps 02 and 08 of
LC 173/2024 are manually vectorized (tracked in the project backlog).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from geopandas import GeoDataFrame

from src.core.config import load_region_config
from src.core.ingest import ibge, osm
from src.core.transform.master_plan_overlays import OverlayMetadata, load_overlays

logger = logging.getLogger(__name__)


class SaoJoseSCAdapter:
    """RegionAdapter implementation for São José, SC."""

    slug: str = "sao_jose_sc"
    name: str = "São José"
    state: str = "SC"
    ibge_code: str = "4216602"
    crs_local: str = "EPSG:31982"  # SIRGAS 2000 / UTM 22S
    bbox: tuple[float, float, float, float] = (-48.72, -27.70, -48.51, -27.49)

    def __init__(self, raw_dir: Path) -> None:
        """Initialize the adapter.

        Args:
            raw_dir: Root cache directory for this region, typically
                ``data/raw/sao_jose_sc/``. Subdirectories per data source
                are created automatically.
        """
        self.raw_dir = Path(raw_dir)
        self.ibge_dir = self.raw_dir / "ibge"
        self.osm_dir = self.raw_dir / "osm"
        self.topodata_dir = self.raw_dir / "topodata"
        self.master_plan_dir = self.raw_dir / "master_plan"

    # ----- RegionAdapter interface ---------------------------------------

    def load_boundary(self) -> GeoDataFrame:
        """Return the municipal boundary in ``crs_local`` (UTM 22S)."""
        gdf = ibge.load_municipal_boundary(
            ibge_code=self.ibge_code,
            uf=self.state,
            cache_dir=self.ibge_dir,
        )
        return gdf.to_crs(self.crs_local)

    def load_buildings(self) -> GeoDataFrame:
        """Return OSM building footprints in ``crs_local``.

        Used as an optional reference overlay in the app. No buffering or
        synthetic lot derivation is applied — these are the raw OSM
        building polygons.
        """
        boundary_4326 = ibge.load_municipal_boundary(
            ibge_code=self.ibge_code,
            uf=self.state,
            cache_dir=self.ibge_dir,
        )
        buildings = osm.load_buildings(boundary_4326, self.osm_dir)
        return buildings.to_crs(self.crs_local)

    def load_master_plan_overlays(self) -> list[OverlayMetadata]:
        """Return metadata for every Master Plan map flagged as overlay.

        Reads the region YAML's ``master_plan_maps`` block and delegates
        to the generic loader in ``core.transform.master_plan_overlays``.
        """
        cfg = load_region_config(self.slug)
        master_plan_cfg = cfg["data_sources"]["master_plan_maps"]
        return load_overlays(self.raw_dir, master_plan_cfg)

    def load_zoning_vectors(self) -> GeoDataFrame:
        """Return zoning polygons (vectorized from LC 173/2024 Map 02/03).

        Currently returns an empty GeoDataFrame with the expected schema.
        Maps 02 (macrozoning) and 03 (detailed zoning) of LC 173/2024 are
        PDF-only and require manual vectorization in QGIS, tracked in the
        project backlog.
        """
        logger.warning(
            "load_zoning_vectors() returning empty GDF: Master Plan maps "
            "not yet vectorized. See PROJECT_PLAN.md backlog."
        )
        return gpd.GeoDataFrame(
            {
                "macrozone_code": pd.Series(dtype="string"),
                "zone_code": pd.Series(dtype="string"),
                "zone_name": pd.Series(dtype="string"),
            },
            geometry=gpd.GeoSeries([], crs=self.crs_local),
            crs=self.crs_local,
        )

    def load_risk_vectors(self) -> GeoDataFrame:
        """Return natural-disaster risk polygons (vectorized from Map 08).

        Currently returns an empty GeoDataFrame. Map 08 of LC 173/2024 is
        PDF-only and requires manual vectorization (tracked in backlog).
        """
        logger.warning(
            "load_risk_vectors() returning empty GDF: Map 08 not yet "
            "vectorized. See PROJECT_PLAN.md backlog."
        )
        return gpd.GeoDataFrame(
            {
                "risk_type": pd.Series(dtype="string"),
                "risk_level": pd.Series(dtype="string"),
            },
            geometry=gpd.GeoSeries([], crs=self.crs_local),
            crs=self.crs_local,
        )

    def zoning_schema(self) -> dict[str, Any]:
        """Map São José zoning codes to standardized attributes.

        Empty for now (zoning not yet vectorized). When Map 03 + Table 01
        of LC 173/2024 are vectorized, this returns one entry per zone
        with its urbanistic parameters (max_height_m, max_coverage_pct,
        max_far).
        """
        return {}
"""São José / Santa Catarina region adapter.

Implements the RegionAdapter interface for the city of São José, SC.
Wires city-specific parameters to the generic core/ingest loaders.

Lot strategy (Alternative B):
  - OSM buildings used as lot proxies (lot_type=osm_building)
  - OSM blocks without buildings kept as whole units (lot_type=synthetic_block)
  - Synthetic-lot subdivision deferred to backlog

Zoning and risk areas are temporarily disabled because the Master Plan
maps (Maps 02 and 08 of LC 173/2024) are PDF-only and require manual
georeferencing. The placeholders return empty GeoDataFrames with the
expected schema so the rest of the pipeline runs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from geopandas import GeoDataFrame

from src.core.ingest import ibge, osm

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

    def load_zoning(self) -> GeoDataFrame:
        """Return zoning polygons (LC 173/2024).

        Currently returns an empty GeoDataFrame with the expected schema:
        Maps 02 (macrozoning) and 03 (detailed zoning) of LC 173/2024 are
        PDF-only and require manual georeferencing + vectorization. This
        is tracked in the project backlog.
        """
        logger.warning(
            "load_zoning() returning empty GDF: Master Plan maps not yet "
            "vectorized. See PROJECT_PLAN.md backlog."
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

    def load_risk_areas(self) -> GeoDataFrame:
        """Return natural-disaster risk polygons (Map 08 of LC 173/2024).

        Currently returns an empty GeoDataFrame. Map 08 is PDF-only and
        requires manual vectorization (tracked in backlog).
        """
        logger.warning(
            "load_risk_areas() returning empty GDF: Map 08 not yet "
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

    def load_lots(self) -> GeoDataFrame:
        """Build the lot dataset (Alternative B: OSM buildings + blocks).

        Strategy:
            1. Load the municipal boundary (EPSG:4326 for OSMnx queries).
            2. Fetch OSM buildings inside the boundary → ``lot_type=osm_building``.
            3. Fetch OSM-derived blocks → ``lot_type=synthetic_block``.
            4. Concatenate, reproject to ``crs_local``, assign ``lot_id``.

        Returns:
            GeoDataFrame in ``crs_local`` with columns:
            ``lot_id``, ``lot_type``, ``geometry``, plus minimal OSM metadata.
        """
        # OSM loaders work in EPSG:4326; load boundary in WGS84 for OSMnx.
        boundary_4326 = ibge.load_municipal_boundary(
            ibge_code=self.ibge_code,
            uf=self.state,
            cache_dir=self.ibge_dir,
        )

        buildings = osm.load_buildings(boundary_4326, self.osm_dir)
        blocks = osm.load_blocks(boundary_4326, self.osm_dir)

        buildings_lots = self._buildings_to_lots(buildings)
        synthetic_lots = self._blocks_to_synthetic_lots(blocks)

        lots = pd.concat([buildings_lots, synthetic_lots], ignore_index=True)
        lots = gpd.GeoDataFrame(lots, geometry="geometry", crs="EPSG:4326")
        lots = lots.to_crs(self.crs_local)
        lots["lot_id"] = [f"lot_{i:07d}" for i in range(len(lots))]

        logger.info(
            "load_lots: %d total (%d OSM buildings + %d synthetic blocks)",
            len(lots),
            (lots["lot_type"] == "osm_building").sum(),
            (lots["lot_type"] == "synthetic_block").sum(),
        )
        return lots[["lot_id", "lot_type", "geometry"]]

    def zoning_schema(self) -> dict[str, Any]:
        """Map São José zoning codes to standardized attributes.

        Empty for now (zoning not yet vectorized). When Map 03 + Table 01
        of LC 173/2024 are vectorized, this returns one entry per zone
        with its urbanistic parameters (max_height_m, max_coverage_pct,
        max_far).
        """
        return {}

    # ----- Internal helpers ----------------------------------------------

    def _buildings_to_lots(self, buildings: GeoDataFrame) -> GeoDataFrame:
        """Tag OSM building footprints as lot proxies."""
        out = buildings.copy()
        out["lot_type"] = "osm_building"
        # Drop empty geometries that occasionally appear in OSM exports.
        out = out[~out.geometry.is_empty & out.geometry.notna()]
        return out[["lot_type", "geometry"]]

    def _blocks_to_synthetic_lots(self, blocks: GeoDataFrame) -> GeoDataFrame:
        """Tag whole OSM blocks as synthetic lot units (Alternative B).

        Future refinement (backlog): subdivide each block into multiple
        synthetic lots based on target area and frontage parameters.
        """
        out = blocks.copy()
        out["lot_type"] = "synthetic_block"
        out = out[~out.geometry.is_empty & out.geometry.notna()]
        return out[["lot_type", "geometry"]]
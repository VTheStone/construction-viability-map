"""Region adapter interface.

Each supported city implements ``RegionAdapter`` and exposes city-specific
data loading logic. The core pipeline depends only on this interface and
never touches city-specific code.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from geopandas import GeoDataFrame

from src.core.transform.master_plan_overlays import OverlayMetadata


@runtime_checkable
class RegionAdapter(Protocol):
    """Interface every city adapter must implement.

    Identity / metadata
    -------------------
    slug:         unique snake_case identifier (e.g. "sao_jose_sc")
    name:         display name (e.g. "São José")
    ibge_code:    7-digit Brazilian IBGE municipal code
    crs_local:    projected CRS suitable for area / distance calculations
                  (e.g. "EPSG:31982" for SC / UTM 22S)
    bbox:         (lon_min, lat_min, lon_max, lat_max) in WGS84

    Data loaders
    ------------
    All loaders return GeoDataFrames in ``crs_local`` unless otherwise noted.
    Layers that are still pending vectorization (zoning, risk) may return an
    empty GeoDataFrame with the expected schema.
    """

    slug: str
    name: str
    ibge_code: str
    crs_local: str
    bbox: tuple[float, float, float, float]

    def load_boundary(self) -> GeoDataFrame:
        """Municipal boundary polygon in ``crs_local``."""
        ...

    def load_buildings(self) -> GeoDataFrame:
        """OSM building footprints inside the boundary.

        Used as an optional reference overlay in the app. No buffering,
        no synthetic lot derivation — just the raw building polygons.
        """
        ...

    def load_master_plan_overlays(self) -> list[OverlayMetadata]:
        """Metadata for every Master Plan map flagged as overlay.

        Each entry points to a georeferenced GeoTIFF on disk plus its CRS,
        bounds, and dimensions.
        """
        ...

    def load_zoning_vectors(self) -> GeoDataFrame:
        """Zoning polygons from vectorized Master Plan maps.

        Returns an empty GeoDataFrame with the expected schema if zoning
        has not yet been vectorized.
        """
        ...

    def load_risk_vectors(self) -> GeoDataFrame:
        """Risk-area polygons from vectorized Master Plan maps.

        Returns an empty GeoDataFrame with the expected schema if risk
        areas have not yet been vectorized.
        """
        ...

    def zoning_schema(self) -> dict[str, Any]:
        """Map this region's zoning codes to standardized attributes.

        Example:
            {
                "ZR1": {"max_height_m": 9, "max_coverage_pct": 50, ...},
                "ZM1": {"max_height_m": 18, "max_coverage_pct": 70, ...},
            }
        """
        ...
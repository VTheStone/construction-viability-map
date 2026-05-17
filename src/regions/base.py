"""Region adapter interface.

Each supported city implements ``RegionAdapter`` and exposes city-specific
data loading logic. The core pipeline depends only on this interface and
never touches city-specific code.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from geopandas import GeoDataFrame


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
    All loaders must return GeoDataFrames in ``crs_local``.
    """

    slug: str
    name: str
    ibge_code: str
    crs_local: str
    bbox: tuple[float, float, float, float]

    def load_boundary(self) -> GeoDataFrame:
        """Municipal boundary polygon."""
        ...

    def load_zoning(self) -> GeoDataFrame:
        """Legal zoning polygons.

        Each row should have at minimum: ``zone_code``, ``zone_name``, geometry.
        For image-overlay strategy (no vectorized zoning yet), this may return
        an empty GeoDataFrame with the expected schema.
        """
        ...

    def load_risk_areas(self) -> GeoDataFrame:
        """Risk-area polygons (landslide, flood, etc.).

        Each row should have at minimum: ``risk_type``, geometry. May return
        an empty GeoDataFrame if no vectorized data is available yet.
        """
        ...

    def load_lots(self) -> GeoDataFrame:
        """Lot polygons (or proxies, depending on the strategy).

        Each row must have at minimum: ``lot_id``, ``lot_type``, geometry.
        ``lot_type`` documents data provenance:
          - ``osm_building``    : OSM building footprint used as proxy
          - ``synthetic_lot``   : algorithmic subdivision of a block
          - ``synthetic_block`` : whole OSM block used as unit
          - ``cadastre``        : official cadastre (when/if available)
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
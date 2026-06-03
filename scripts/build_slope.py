"""Build slope.tif + slope.png + slope.json for a region in one command.

Orchestrates the slope pipeline that until now had to be run by hand:
load boundary -> load DEM (Topodata) -> compute slope -> visualize.
"""

import logging
from pathlib import Path

import geopandas as gpd

from src.core.config import load_region_config
from src.core.ingest import osm, topodata
from src.core.transform.slope import compute_slope
from src.core.transform.slope_visualize import visualize_slope
from src.regions.sao_jose_sc.adapter import SaoJoseSCAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

REGION = "sao_jose_sc"

adapter = SaoJoseSCAdapter(Path(f"data/raw/{REGION}"))
boundary = adapter.load_boundary()  # municipal boundary in UTM 22S

cfg = load_region_config(REGION)
sheet = cfg["data_sources"]["dem"]["sheet_codes"][0]
dem_path = topodata.load_dem(sheet, adapter.topodata_dir)

# Subtract OSM water (bay/sea/lagoons) from the municipal polygon so the
# slope is computed on LAND only. Fixes the green-over-water artifact in
# Baía Sul / Baía da Palhoça that elevation thresholding can't.
water = osm.load_water(boundary, adapter.osm_dir).to_crs(boundary.crs)
land_geom = boundary.geometry.unary_union
if not water.empty:
    water_union = water.geometry.unary_union
    if water_union is not None and not water_union.is_empty:
        land_geom = land_geom.difference(water_union)
land = gpd.GeoDataFrame(geometry=[land_geom], crs=boundary.crs)

slope_tif = compute_slope(
    dem_path,
    land,
    Path(f"data/interim/{REGION}"),
    force=True,
)
png_path, json_path = visualize_slope(
    slope_tif,
    Path(f"data/processed/{REGION}"),
    force=True,
)

print(f"\nslope.tif:  {slope_tif}")
print(f"slope.png:  {png_path}")
print(f"slope.json: {json_path}")
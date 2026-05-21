"""Quick smoke test for the lots transform module."""

import logging
from pathlib import Path

from src.core.ingest import osm
from src.core.ingest.ibge import load_municipal_boundary
from src.core.transform.lots import build_lots
from src.regions.sao_jose_sc.adapter import SaoJoseSCAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

a = SaoJoseSCAdapter(Path("data/raw/sao_jose_sc"))
boundary_proj = a.load_boundary()
boundary_4326 = load_municipal_boundary(
    "4216602", "SC", Path("data/raw/sao_jose_sc/ibge")
)

buildings = osm.load_buildings(boundary_4326, Path("data/raw/sao_jose_sc/osm"))
blocks = osm.load_blocks(boundary_4326, Path("data/raw/sao_jose_sc/osm"))

out = build_lots(
    buildings,
    blocks,
    boundary_proj,
    Path("data/interim/sao_jose_sc"),
)

print(f"\nLots written to: {out}")

import geopandas as gpd
gdf = gpd.read_parquet(out)
print(f"Total lots: {len(gdf)}")
print(f"Types: {gdf['lot_type'].value_counts().to_dict()}")
print(
    f"Area stats (m^2): "
    f"min={gdf['area_m2'].min():.1f} "
    f"median={gdf['area_m2'].median():.1f} "
    f"max={gdf['area_m2'].max():.1f}"
)
print(f"Total covered area: {gdf['area_m2'].sum() / 1_000_000:.2f} km^2")
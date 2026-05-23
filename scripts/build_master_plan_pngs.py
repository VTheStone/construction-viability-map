"""Convert all Master Plan GeoTIFFs to PNG overlays."""

import logging
from pathlib import Path

from src.core.transform.master_plan_visualize import (
    visualize_all_master_plan_overlays,
)
from src.regions.sao_jose_sc.adapter import SaoJoseSCAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

REGION = "sao_jose_sc"

adapter = SaoJoseSCAdapter(Path(f"data/raw/{REGION}"))
overlays = adapter.load_master_plan_overlays()

out_dir = Path(f"data/processed/{REGION}/master_plan")
results = visualize_all_master_plan_overlays(overlays, out_dir, force=False)

print(f"\nGenerated {len(results)} PNG overlays in {out_dir}")
for r in results:
    print(f"  {r.map_id} -> {r.png_path.name}")
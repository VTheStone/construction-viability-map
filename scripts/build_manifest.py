"""Build the processed/manifest.json for a region.

This is a thin wrapper that runs the manifest builder against São José's
existing transforms. Once the full pipeline (Phase 6) exists, this
script will be folded into it.
"""

import logging
from pathlib import Path

from src.core.transform.manifest import build_manifest
from src.regions.sao_jose_sc.adapter import SaoJoseSCAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

REGION = "sao_jose_sc"

adapter = SaoJoseSCAdapter(Path(f"data/raw/{REGION}"))
master_plan_overlays = adapter.load_master_plan_overlays()

manifest_path = build_manifest(
    region_slug=REGION,
    processed_dir=Path(f"data/processed/{REGION}"),
    interim_dir=Path(f"data/interim/{REGION}"),
    master_plan_overlays=master_plan_overlays,
    slope_json_path=Path(f"data/processed/{REGION}/slope.json"),
    app_parquet_path=Path(f"data/interim/{REGION}/app.geoparquet"),
)

print(f"\nManifest written to: {manifest_path}")
print(f"Size: {manifest_path.stat().st_size / 1024:.1f} KB")
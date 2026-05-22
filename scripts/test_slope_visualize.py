"""Smoke test for slope visualization."""

import json
import logging
from pathlib import Path

from src.core.transform.slope_visualize import visualize_slope

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

slope_tif = Path("data/interim/sao_jose_sc/slope.tif")
processed_dir = Path("data/processed/sao_jose_sc")

png_path, json_path = visualize_slope(slope_tif, processed_dir)

print(f"\nPNG: {png_path}")
print(f"JSON: {json_path}")

print(f"\nPNG size: {png_path.stat().st_size / 1024:.1f} KB")

with json_path.open(encoding="utf-8") as f:
    meta = json.load(f)
print(f"\nBounds (WGS84): {meta['bounds_wgs84']}")
print(f"Value range: {meta['value_range']} {meta['unit']}")
print(f"Colormap: {meta['colormap']}")
print(f"Legend stops: {len(meta['legend'])}")
for stop in meta['legend']:
    print(f"  {stop['value']:>2}° {stop['color']} — {stop['label']}")
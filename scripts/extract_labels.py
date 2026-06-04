"""CLI wrapper around master_plan_labels (OCR zone-code point layers).

Examples:
    python -m scripts.extract_labels --region sao_jose_sc --map 03
    python -m scripts.extract_labels --region sao_jose_sc --all --force
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.transform.master_plan_labels import extract_labels  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OCR Master Plan zone labels into point layers.")
    p.add_argument("--region", required=True, help="Region slug (e.g. sao_jose_sc)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--map", help="Map number (e.g. 03)")
    g.add_argument("--all", action="store_true", help="Every map_*.yaml for the region")
    p.add_argument("--min-confidence", type=float, default=0.30)
    p.add_argument("--canvas-size", type=int, default=2560,
                   help="Max image side EasyOCR processes. Raise for big maps (e.g. 7100).")
    p.add_argument("--mag-ratio", type=float, default=1.0,
                   help="EasyOCR magnification ratio; >1 upscales small text.")
    p.add_argument("--force", action="store_true")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    yaml_dir = PROJECT_ROOT / "config" / "master_plan" / args.region
    out_dir = PROJECT_ROOT / "data" / "processed" / args.region / "master_plan" / "labels"
    boundary_path = PROJECT_ROOT / "data" / "interim" / args.region / "boundary.geoparquet"
    if not boundary_path.exists():
        logging.warning("Boundary not found at %s — labels will NOT be clipped.", boundary_path)
        boundary_path = None

    if args.all:
        yaml_paths = sorted(yaml_dir.glob("map_*.yaml"))
    else:
        n = int(args.map.lstrip("0") or "0")
        yaml_paths = [yaml_dir / f"map_{n:02d}.yaml"]

    for yaml_path in yaml_paths:
        if not yaml_path.exists():
            print(f"ERROR: config not found: {yaml_path}", file=sys.stderr)
            return 1
        result = extract_labels(
            yaml_path,
            PROJECT_ROOT,
            out_dir,
            boundary_path=boundary_path,
            min_confidence=args.min_confidence,
            canvas_size=args.canvas_size,
            mag_ratio=args.mag_ratio,
            force=args.force,
        )
        gdf = __import__("geopandas").read_parquet(result.out_path)
        sample = sorted(gdf["zone_code"].value_counts().items()) if len(gdf) else []
        print(f"\n{result.map_id}: {result.n_labels} labels -> {result.out_path}")
        for code, cnt in sample:
            print(f"  {code}: {cnt}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
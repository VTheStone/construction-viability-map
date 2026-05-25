"""CLI wrapper around master_plan_vectorize.

Examples:
    # Vectorize one map
    python -m scripts.vectorize_master_plan --region sao_jose_sc --map 03

    # Vectorize every map_*.yaml for a region
    python -m scripts.vectorize_master_plan --region sao_jose_sc --all

    # Force re-run even when the GeoParquet already exists
    python -m scripts.vectorize_master_plan --region sao_jose_sc --all --force
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the project root importable.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.transform.master_plan_vectorize import (  # noqa: E402
    vectorize_all_maps,
    vectorize_map,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Vectorize Master Plan maps from georeferenced GeoTIFFs."
    )
    p.add_argument(
        "--region",
        required=True,
        help="Region slug (e.g. sao_jose_sc)",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--map",
        help="Map number to vectorize (e.g. 03). Loads config/master_plan/<region>/map_NN.yaml.",
    )
    g.add_argument(
        "--all",
        action="store_true",
        help="Vectorize every map_*.yaml under config/master_plan/<region>/.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-run even when output GeoParquet already exists.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    yaml_dir = PROJECT_ROOT / "config" / "master_plan" / args.region
    out_dir = (
        PROJECT_ROOT
        / "data"
        / "processed"
        / args.region
        / "master_plan"
        / "vectors"
    )

    if not yaml_dir.exists():
        print(f"ERROR: config directory not found: {yaml_dir}", file=sys.stderr)
        return 1

    boundary_path = (
        PROJECT_ROOT / "data" / "interim" / args.region / "boundary.geoparquet"
    )
    if not boundary_path.exists():
        logging.warning(
            "Boundary not found at %s — output will NOT be clipped. "
            "Polygons in ocean / neighboring cities may leak through.",
            boundary_path,
        )
        boundary_path = None

    if args.all:
        results = vectorize_all_maps(
            yaml_dir=yaml_dir,
            project_root=PROJECT_ROOT,
            out_dir=out_dir,
            boundary_path=boundary_path,
            force=args.force,
        )
        print(f"\nVectorized {len(results)} maps:")
        for r in results:
            print(f"  {r.map_id}: {r.n_polygons} polygons -> {r.out_path}")
    else:
        # Normalize: "3" -> "map_03", "03" -> "map_03", "map_03" -> "map_03"
        m = args.map.lstrip("0") or "0"
        try:
            n = int(m)
        except ValueError:
            print(f"ERROR: --map must be a number, got {args.map!r}", file=sys.stderr)
            return 1
        yaml_path = yaml_dir / f"map_{n:02d}.yaml"
        if not yaml_path.exists():
            print(f"ERROR: config not found: {yaml_path}", file=sys.stderr)
            return 1

        result = vectorize_map(
            yaml_path=yaml_path,
            project_root=PROJECT_ROOT,
            out_dir=out_dir,
            boundary_path=boundary_path,
            force=args.force,
        )
        print(
            f"\n{result.map_id}: {result.n_polygons} polygons in {result.crs} "
            f"-> {result.out_path}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
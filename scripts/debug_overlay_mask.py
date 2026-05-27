"""Visualize the overlay mask on a Master Plan GeoTIFF.

For visual calibration of overlay-detection thresholds before
re-running the full vectorization pipeline.

Example:
    python scripts/debug_overlay_mask.py \\
        data/raw/sao_jose_sc/master_plan/overlays/mapa_05_aei_urbanistico.tif \\
        --output debug_map_05_overlay.png
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import cv2
import numpy as np
import rasterio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.transform.overlay_mask import detect_overlay  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tif", type=Path, help="GeoTIFF to analyze.")
    p.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("overlay_debug.png"),
        help="Output PNG path.",
    )
    p.add_argument("--no-streets", action="store_true",
                   help="Disable the streets detector.")
    p.add_argument("--no-text", action="store_true",
                   help="Disable the text/labels detector.")
    p.add_argument("--no-contours", action="store_true",
                   help="Disable the hillshade-contour detector.")
    p.add_argument("--rivers", action="store_true",
                   help="Enable the river detector (off by default).")
    p.add_argument("--max-width", type=int, default=2400,
                   help="Downscale the side-by-side composite if its "
                        "width exceeds this many pixels.")
    p.add_argument("--alpha", type=float, default=0.55,
                   help="Red overlay opacity in 0..1.")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


def _read_rgb(tif_path: Path) -> np.ndarray:
    """Load the first three bands of a GeoTIFF as an RGB uint8 array."""
    with rasterio.open(tif_path) as src:
        if src.count < 3:
            raise ValueError(f"{tif_path}: need >=3 bands, got {src.count}")
        r, g, b = src.read(1), src.read(2), src.read(3)
    return np.dstack([r, g, b]).astype(np.uint8, copy=False)


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.tif.exists():
        print(f"ERROR: file not found: {args.tif}", file=sys.stderr)
        return 1

    rgb = _read_rgb(args.tif)
    logging.info("Loaded %s: %dx%d", args.tif.name, rgb.shape[1], rgb.shape[0])

    config = {
        "overlay_detect_streets": not args.no_streets,
        "overlay_detect_text": not args.no_text,
        "overlay_detect_contours": not args.no_contours,
        "overlay_detect_rivers": args.rivers,
    }
    mask = detect_overlay(rgb, config)

    # Alpha-blend bright red over the original where mask is True.
    red = np.zeros_like(rgb)
    red[..., 0] = 255
    blended = (args.alpha * red + (1 - args.alpha) * rgb).astype(np.uint8)
    overlay_vis = np.where(mask[..., None], blended, rgb)

    side_by_side = np.hstack([rgb, overlay_vis])

    if side_by_side.shape[1] > args.max_width:
        scale = args.max_width / side_by_side.shape[1]
        new_size = (
            int(side_by_side.shape[1] * scale),
            int(side_by_side.shape[0] * scale),
        )
        side_by_side = cv2.resize(side_by_side, new_size, interpolation=cv2.INTER_AREA)

    # cv2.imwrite expects BGR.
    bgr = cv2.cvtColor(side_by_side, cv2.COLOR_RGB2BGR)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.output), bgr)
    logging.info("Wrote %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
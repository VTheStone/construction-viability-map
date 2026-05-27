"""Detect overlay pixels (streets, text, contour lines, rivers) on Master Plan maps.

These pixels do not belong to any zone — they are cartographic
annotations painted on top of the zone colors. Detecting them before
HSV segmentation lets the per-pixel classifier produce a cleaner
label_map, which in turn lets the morphological close/open kernels
shrink dramatically.

Why detect overlay BEFORE segmentation?

The original pipeline (segment → close → open → fill_holes → min_area)
cannot simultaneously handle large polygons fragmented by streets
(need aggressive close) and small narrow polygons (need tiny open).
Streets and labels are pixel-level noise; morphology fights them
shape-by-shape and parameters never generalize. Removing the noise
upstream lets the residual roughness be cleaned by much smaller kernels.

Each detector is independent. Toggle any subset on/off via config.
"""

from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# Threshold defaults. Calibrated for São José Master Plan PDFs
# rasterized at ~200 DPI (pixel ~ 9 m on the ground).
DEFAULTS: dict[str, Any] = {
    # --- Streets (white/near-white) ---
    # Real zone colors are saturated; near-white pixels are almost
    # always streets, page background, or text outlines.
    "streets_value_min": 220,
    "streets_saturation_max": 35,

    # --- Text (small dark compact blobs) ---
    # Zone labels and road names are printed in near-black ink. Real
    # zone polygons that happen to be dark (e.g. AEIU-9 dark red) have
    # much larger connected components, so an area filter separates
    # them.
    "text_value_max": 70,
    "text_max_area_px": 400,

    # --- Contour lines (thin dark-gray strokes from hillshade) ---
    # Detected via morphological black-tophat: features darker than
    # their local neighborhood and thinner than the structuring
    # element. The saturation cap protects saturated zone fills from
    # being flagged.
    "contours_blackhat_kernel_px": 7,
    "contours_intensity_min": 25,
    "contours_saturation_max": 60,

    # --- Rivers (thin blue lines) ---
    # Risky: several Master Plan zones are blue too. We rescue them by
    # requiring connected components to be thin. Off by default.
    "rivers_hue_min": 95,
    "rivers_hue_max": 130,
    "rivers_saturation_min": 80,
    "rivers_max_width_px": 6,
}


def detect_overlay(rgb: np.ndarray, config: dict[str, Any]) -> np.ndarray:
    """Return a boolean mask of pixels considered overlay (non-zone).

    Args:
        rgb: ``(H, W, 3)`` uint8 array in RGB channel order.
        config: Recognized keys:

            - ``overlay_detect_streets`` (bool, default True)
            - ``overlay_detect_text`` (bool, default True)
            - ``overlay_detect_contours`` (bool, default True)
            - ``overlay_detect_rivers`` (bool, default False)
            - ``overlay_thresholds`` (dict): per-detector threshold
              overrides; keys must match those in ``DEFAULTS``.

            Any other key is ignored.

    Returns:
        ``(H, W)`` boolean array. True where the pixel is detected as
        overlay; False otherwise.
    """
    thresholds = {**DEFAULTS, **config.get("overlay_thresholds", {})}

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    mask = np.zeros(rgb.shape[:2], dtype=bool)

    if config.get("overlay_detect_streets", True):
        m = _detect_streets(s, v, thresholds)
        mask |= m
        logger.info("  overlay.streets: %d px", int(m.sum()))

    if config.get("overlay_detect_text", True):
        m = _detect_text(v, thresholds)
        mask |= m
        logger.info("  overlay.text: %d px", int(m.sum()))

    if config.get("overlay_detect_contours", True):
        m = _detect_contours(s, v, thresholds)
        mask |= m
        logger.info("  overlay.contours: %d px", int(m.sum()))

    if config.get("overlay_detect_rivers", False):
        m = _detect_rivers(h, s, thresholds)
        mask |= m
        logger.info("  overlay.rivers: %d px", int(m.sum()))

    logger.info(
        "  overlay total: %d px (%.2f%% of image)",
        int(mask.sum()),
        100.0 * mask.sum() / mask.size,
    )
    return mask


def _detect_streets(s: np.ndarray, v: np.ndarray, t: dict[str, Any]) -> np.ndarray:
    """High luminance + low saturation: near-white pixels.

    The page background is also white but is filtered by the boundary
    clip downstream. Zone fills are saturated, so any low-S high-V
    pixel inside the boundary is almost certainly a street stroke.
    """
    return (v >= int(t["streets_value_min"])) & (s <= int(t["streets_saturation_max"]))


def _detect_text(v: np.ndarray, t: dict[str, Any]) -> np.ndarray:
    """Small dark connected components.

    Real dark zones (e.g. AEIU-9) form much larger components than
    label glyphs, so an area cap discriminates without needing color.
    """
    dark = (v <= int(t["text_value_max"])).astype(np.uint8)
    if not dark.any():
        return np.zeros_like(dark, dtype=bool)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    out = np.zeros(dark.shape, dtype=bool)
    max_area = int(t["text_max_area_px"])
    for i in range(1, n_labels):  # skip background label 0
        if stats[i, cv2.CC_STAT_AREA] <= max_area:
            out |= labels == i
    return out


def _detect_contours(s: np.ndarray, v: np.ndarray, t: dict[str, Any]) -> np.ndarray:
    """Black-tophat response: thin dark strokes on lighter background.

    Hillshade contour lines are darker than their neighborhood and
    thinner than the structuring element — exactly what black-tophat
    isolates. The saturation cap spares saturated zone fills.
    """
    k_size = int(t["contours_blackhat_kernel_px"])
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_size, k_size))
    blackhat = cv2.morphologyEx(v, cv2.MORPH_BLACKHAT, kernel)
    return (
        (blackhat >= int(t["contours_intensity_min"]))
        & (s <= int(t["contours_saturation_max"]))
    )


def _detect_rivers(h: np.ndarray, s: np.ndarray, t: dict[str, Any]) -> np.ndarray:
    """Thin saturated-blue connected components.

    Several zones are blue. To avoid eating them, we keep only
    components whose minor axis is below ``rivers_max_width_px`` —
    rivers are thin, zone polygons are not.
    """
    blue = (
        (h >= int(t["rivers_hue_min"]))
        & (h <= int(t["rivers_hue_max"]))
        & (s >= int(t["rivers_saturation_min"]))
    ).astype(np.uint8)
    if not blue.any():
        return np.zeros_like(blue, dtype=bool)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(blue, connectivity=8)
    out = np.zeros(blue.shape, dtype=bool)
    max_w = int(t["rivers_max_width_px"])
    for i in range(1, n_labels):
        w = stats[i, cv2.CC_STAT_WIDTH]
        ht = stats[i, cv2.CC_STAT_HEIGHT]
        if min(w, ht) <= max_w:
            out |= labels == i
    return out
"""Smoke tests for overlay_mask.detect_overlay."""

from __future__ import annotations

import numpy as np

from src.core.transform.overlay_mask import detect_overlay


def _solid_block(h: int, w: int, rgb: tuple[int, int, int]) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[..., 0] = rgb[0]
    img[..., 1] = rgb[1]
    img[..., 2] = rgb[2]
    return img


def test_white_line_through_purple_block_is_detected_as_street():
    # Saturated purple background (AEIU-1 color #c487f2).
    img = _solid_block(200, 400, (196, 135, 242))
    # Horizontal white line crossing the block: simulates a street.
    img[90:95, :] = (255, 255, 255)

    mask = detect_overlay(
        img,
        {
            "overlay_detect_streets": True,
            "overlay_detect_text": False,
            "overlay_detect_contours": False,
            "overlay_detect_rivers": False,
        },
    )

    # Every white pixel inside the line band must be flagged.
    assert mask[90:95, :].all()
    # No purple pixel should be flagged.
    assert not mask[:80, :].any()
    assert not mask[100:, :].any()


def test_all_detectors_off_yields_empty_mask():
    img = _solid_block(50, 50, (196, 135, 242))
    img[20, 20] = (255, 255, 255)
    img[30, 30] = (0, 0, 0)

    mask = detect_overlay(
        img,
        {
            "overlay_detect_streets": False,
            "overlay_detect_text": False,
            "overlay_detect_contours": False,
            "overlay_detect_rivers": False,
        },
    )
    assert not mask.any()


def test_small_dark_blob_is_detected_as_text():
    img = _solid_block(100, 100, (196, 135, 242))
    # 4x4 black blob — well under the default text_max_area_px=400.
    img[40:44, 40:44] = (0, 0, 0)

    mask = detect_overlay(
        img,
        {
            "overlay_detect_streets": False,
            "overlay_detect_text": True,
            "overlay_detect_contours": False,
            "overlay_detect_rivers": False,
        },
    )
    assert mask[40:44, 40:44].all()
    # The surrounding purple must remain unflagged.
    assert not mask[:35, :].any()
    assert not mask[50:, :].any()
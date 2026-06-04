"""OCR-based zone-label extraction for Master Plan maps.

Co-colored subzones (e.g. ZA-9 / ZA-10 / ZB-2 in Map 03) collapse into a
single polygon in master_plan_vectorize because they share one legend
color. This module recovers the per-subzone codes by OCR'ing the labels
printed on the georeferenced GeoTIFF and emitting them as a POINT layer
(one point per label, carrying its zone_code). The app shows these
points so users can tell co-colored subzones apart and filter by code.

Why a separate point layer instead of splitting the polygons?
Splitting a merged polygon by label needs seeded segmentation and is
fragile. A lightweight point layer of codes is enough for the UI to
disambiguate, and it keeps the two concerns (area vs identity) decoupled
— exactly the split the vectorizer docstring anticipates.

OCR engine: EasyOCR (pip-only, no system binary; robust on small,
sometimes-rotated text over busy backgrounds). The reader is built once
and reused — loading the model weights is the slow part.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely.geometry as sg

from src.core.transform.master_plan_vectorize import _read_rgb, load_map_config

logger = logging.getLogger(__name__)


# A recognized label must match this AFTER normalization (uppercased,
# inner spaces stripped, separator unified to "-"). Covers São José's
# code families:
#   ZA-9, ZB-2, ZC-1, ZR          (zoning, Map 03)
#   AEIU-1, AEIA-3, AEIP-5, AEIS-2 (special-interest areas, Maps 04/05/07/08)
#   MZ-A                          (macrozones, Map 02)
#   CI-F                          (disturbance categories, Map 10)
#   DP-3                          (population density, Map 09)
# Per-map YAML may override via `label_pattern`.
DEFAULT_LABEL_PATTERN = (
    r"^(?:Z[ABCR](?:-?\d{1,2})?|AEI[AUPS]-?\d{1,2}|MZ-?[ABCR]|CI-?[A-Z]|DP-?\d)$"
)

# Restrict EasyOCR's alphabet to the zone-code charset. This sharply
# cuts spurious detections of street/neighborhood names that would
# otherwise need filtering downstream.
OCR_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/"

# Below this EasyOCR confidence the reader is mostly guessing on these
# small glyphs.
DEFAULT_MIN_CONFIDENCE = 0.30

# Common OCR letter/digit confusions, applied ONLY to the numeric tail
# (after the hyphen) so the family prefix is never corrupted.
_TAIL_CONFUSIONS = str.maketrans(
    {"O": "0", "Q": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"}
)

_READER = None  # lazy EasyOCR singleton


@dataclass(frozen=True)
class LabelResult:
    """What the OCR pass produced for one map."""

    map_id: str
    out_path: Path
    n_labels: int
    crs: str


def _get_reader():
    """Build the EasyOCR reader once and reuse it across maps.

    Model weights (~100 MB) load on first use; doing it per map would
    dominate runtime. ``gpu=False`` keeps it CUDA-free — the maps are
    processed offline so CPU speed is acceptable. Imported lazily so the
    rest of the package never hard-depends on easyocr.
    """
    global _READER
    if _READER is None:
        import easyocr

        logger.info("Loading EasyOCR model (first call only)...")
        _READER = easyocr.Reader(["en"], gpu=False)
    return _READER


def _normalize(text: str) -> str:
    """Uppercase, drop inner spaces, unify the separator to a hyphen."""
    t = text.upper().strip().replace(" ", "")
    for sep in ("—", "–", "_", ".", "/"):
        t = t.replace(sep, "-")
    return t


# Known code families, longest-first so AEIx wins over a shorter prefix
# and ZR isn't mistaken for ZA/ZB/ZC.
_CODE_PREFIXES = ("AEIA", "AEIU", "AEIP", "AEIS", "ZA", "ZB", "ZC", "ZR", "MZ", "CI", "DP")


def _canonical(code: str) -> str:
    """Force the canonical "PREFIX-SUFFIX" spelling.

    EasyOCR sometimes drops the hyphen ("ZA4" instead of "ZA-4"), which
    would split one logical code into two distinct strings downstream.
    Re-inserting it collapses every instance of a code to one value.
    """
    bare = code.replace("-", "")
    for prefix in _CODE_PREFIXES:
        if bare.startswith(prefix):
            suffix = bare[len(prefix):]
            return f"{prefix}-{suffix}" if suffix else prefix
    return code

def _map_prefixes(zones) -> set[str]:
    """Code families this map actually defines, read from its zone codes.

    Every Master Plan overlay is drawn over the same zoning base grid, so
    OCR picks up ZA/ZB labels even on the risk or density maps. Keeping
    only the families the map declares (AEIS for the risk map, etc.) drops
    that bleed-through.
    """
    found: set[str] = set()
    for z in zones:
        code = z.code.upper()
        for prefix in _CODE_PREFIXES:
            if prefix in code:
                found.add(prefix)
    return found

def _match_code(text: str, pattern: re.Pattern) -> str | None:
    """Return a canonical zone code if ``text`` looks like one, else None.

    Two passes: the raw normalized string first, then a retry that fixes
    common digit confusions in the numeric tail only. The two-pass design
    avoids over-correcting strings that were already valid.
    """
    norm = _normalize(text)
    if pattern.match(norm):
        return _canonical(norm)
    if "-" in norm:
        head, _, tail = norm.partition("-")
        fixed = f"{head}-{tail.translate(_TAIL_CONFUSIONS)}"
        if pattern.match(fixed):
            return _canonical(fixed)
    return None


def extract_labels(
    yaml_path: Path,
    project_root: Path,
    out_dir: Path,
    *,
    boundary_path: Path | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    canvas_size: int = 2560,
    mag_ratio: float = 1.0,
    force: bool = False,
) -> LabelResult:
    """OCR the zone-code labels on one Master Plan GeoTIFF into a point layer.

    Args:
        yaml_path: Path to ``config/master_plan/<region>/map_NN.yaml``.
        project_root: Used to resolve the YAML's ``source_tif`` path.
        out_dir: Output dir (e.g. ``processed/<region>/master_plan/labels/``).
        boundary_path: Optional municipal boundary GeoParquet. Labels
            outside it (legend text, neighboring-city ink) are dropped.
        min_confidence: EasyOCR confidence floor.
        force: If False and the output exists, skip and just count.

    Returns:
        LabelResult with the output path and label count.
    """
    cfg = load_map_config(yaml_path)
    map_id = cfg["map_id"]
    out_path = out_dir / f"{map_id}_labels.geoparquet"

    if out_path.exists() and not force:
        logger.info("Cached label output: %s", out_path)
        gdf = gpd.read_parquet(out_path)
        return LabelResult(map_id, out_path, len(gdf), str(gdf.crs))

    source_tif = project_root / cfg["source_tif"]
    if not source_tif.exists():
        raise FileNotFoundError(f"GeoTIFF not found: {source_tif}")

    pattern = re.compile(str(cfg.get("label_pattern", DEFAULT_LABEL_PATTERN)))
    # Only accept codes whose family is actually defined by THIS map —
    # drops the zoning base-grid labels (ZA/ZB) that bleed onto every
    # overlay.
    valid_prefixes = _map_prefixes(cfg["zones"])

    rgb, transform, crs, width, height = _read_rgb(source_tif)
    logger.info("OCR on %s (%dx%d, CRS=%s)", source_tif.name, width, height, crs)

    reader = _get_reader()
    # allowlist biases recognition to the code charset; paragraph=False
    # keeps one detection per label (with its confidence).
    # canvas_size caps the longest image side EasyOCR works on; the
    # default (2560) downscales these ~7000 px maps ~2.7x and tiny zone
    # labels vanish. Raising it (and mag_ratio) keeps labels legible at
    # the cost of speed/memory.
    detections = reader.readtext(
        rgb,
        allowlist=OCR_ALLOWLIST,
        paragraph=False,
        canvas_size=canvas_size,
        mag_ratio=mag_ratio,
    )
    logger.info("  EasyOCR returned %d raw detections", len(detections))

    rows = []
    for bbox, text, conf in detections:
        if conf < min_confidence:
            continue
        code = _match_code(text, pattern)
        if code is None:
            continue
        prefix = next((p for p in _CODE_PREFIXES if code.startswith(p)), None)
        if valid_prefixes and prefix not in valid_prefixes:
            continue
        # bbox is 4 corner points [[x, y], ...]; use their centroid as
        # the label position, then map pixel -> CRS via the affine.
        pts = np.asarray(bbox, dtype=np.float64)
        col = float(pts[:, 0].mean())
        row = float(pts[:, 1].mean())
        world_x, world_y = transform * (col, row)
        rows.append(
            {
                "map_id": map_id,
                "zone_code": code,
                "text_raw": text,
                "confidence": round(float(conf), 3),
                "geometry": sg.Point(world_x, world_y),
            }
        )

    logger.info("  %d detections matched a zone-code pattern", len(rows))

    if rows:
        gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    else:
        gdf = gpd.GeoDataFrame(
            {"map_id": [], "zone_code": [], "text_raw": [], "confidence": []},
            geometry=gpd.GeoSeries([], crs=crs),
            crs=crs,
        )

    # Boundary clip: legends sit in the page margins (outside the
    # municipality), so dropping points outside the boundary removes
    # most legend/garbage detections.
    if boundary_path is not None and boundary_path.exists() and not gdf.empty:
        boundary = gpd.read_parquet(boundary_path).to_crs(crs).geometry.unary_union
        before = len(gdf)
        gdf = gdf[gdf.within(boundary)].reset_index(drop=True)
        logger.info("  boundary clip: kept %d / %d labels", len(gdf), before)

    # No labels matched: don't write a layer (avoids a dead "Mostrar
    # rótulos" toggle for maps with no zone-code text, e.g. the density
    # heatmap). Remove any stale file from a previous run.
    if gdf.empty:
        logger.info("No labels matched for %s — skipping layer.", map_id)
        out_path.unlink(missing_ok=True)
        return LabelResult(map_id, out_path, 0, str(crs))

    out_dir.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out_path)
    logger.info("Wrote %d labels to %s", len(gdf), out_path)

    return LabelResult(map_id, out_path, len(gdf), str(gdf.crs))
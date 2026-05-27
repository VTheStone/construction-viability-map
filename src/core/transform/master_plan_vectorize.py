"""Master Plan map vectorization via HSV color segmentation.

This module turns a georeferenced Master Plan GeoTIFF into a clean
GeoParquet of zone polygons in the region's projected CRS. The input
TIFF is the output of Phase 3d (manual georeferencing in QGIS) and
already carries the affine transform that maps pixel coordinates to
real-world meters; we read it directly with rasterio and never
recompute it.

The segmentation strategy

The PDFs from LC 173/2024 are vector containers wrapping a single
embedded JPEG of the map plus a vector legend with exact RGB colors
for each zone. Phase 3d rasterizes the whole page (around 200 DPI)
and georeferences it. The resulting GeoTIFF therefore inherits the
same color palette in its pixels, modulated by a hillshade that
darkens slopes and lightens valleys.

Hillshade preserves *hue* and lowers *value* (luminosity). It does
not change the dominant color. That makes HSV the natural color
space for segmentation: we assign each pixel to the legend color
whose hue is closest, gated by a minimum saturation so that
near-white paper and near-black contour lines stay unassigned.

After per-pixel assignment we run morphological close + open to
fill the gaps caused by street networks, river lines, and zone
labels painted on top of the polygons. The closing kernel size is
chosen per map in the YAML — wider streets need bigger kernels.

Finally rasterio.features.shapes turns the cleaned mask into vector
polygons, and shapely simplifies them with Douglas-Peucker before
they hit the GeoParquet.

Per-map configuration

Each map has a YAML at config/master_plan/<region>/map_NN.yaml that
declares the legend colors and zone codes, plus tuning knobs (HSV
tolerances, morphology kernel sizes, min polygon area). The YAML is
the single source of truth; the module here just executes it.

Co-colored subzones

The legends group several subzones under a single color (for example,
ZA-9, ZA-10 and ZB-2 all share the same orange in Map 03). The
vectorizer respects that grouping: one polygon family per color,
labeled with the joint zone code. Per-subzone disambiguation is
handled separately by master_plan_labels.py via OCR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import geopandas as gpd
import numpy as np
import rasterio
import shapely.geometry as sg
import yaml
from rasterio.features import shapes as raster_shapes
from shapely.geometry.base import BaseGeometry

from src.core.transform.overlay_mask import detect_overlay

logger = logging.getLogger(__name__)


# Default knobs. Per-map YAML can override any of these.
DEFAULTS: dict[str, Any] = {
    # Boundary clipping
    # IBGE boundaries are sometimes slightly smaller than what the
    # Master Plan PDFs depict (different cartographic sources). A
    # small positive buffer absorbs legitimate polygons that fall a
    # few hundred meters outside the IBGE limit while keeping the
    # ocean and surrounding municipalities excluded.
    "boundary_buffer_m": 500,
    # After clipping, polygons whose bbox aspect ratio exceeds this
    # threshold are discarded. Text labels and legend strips in the
    # PDF are very elongated (~10:1); real zone polygons are not.
    # Applied only to polygons that ended up partly or fully outside
    # the un-buffered boundary — i.e. fringe polygons that survived
    # only because of the buffer.
    "fringe_max_aspect_ratio": 5.0,
    # Additional fringe defense: a polygon whose centroid lies more
    # than this many meters from the un-buffered boundary is treated
    # as an outsider (logo, ocean decoration, neighboring city ink)
    # and discarded. Legitimate polygons that extend past the IBGE
    # limit have their centroid close to the limit; orphan splats in
    # the ocean have centroids hundreds of meters away.
    "fringe_max_centroid_distance_m": 150,
    # HSV gates for "this pixel is colored, not background"
    "saturation_min": 30,
    "value_min": 40,
    "value_max": 245,
    # Maximum hue distance for a pixel to be assigned to a zone.
    # In OpenCV's hue space (0..179, circular). 20 ≈ "same color family".
    # Above this, the pixel is treated as background — prevents off-palette
    # colors (e.g. blue ocean/text being snapped to the nearest legend color).
    "max_hue_distance": 20,
        # Morphology kernel sizes (pixels). Defaults are small because the
    # overlay-detection step (see overlay_mask.py) removes most of the
    # noise (streets, text, hillshade contour lines) before morphology
    # runs. Maps with unusual artifacts can override per-YAML.
    "close_kernel_px": 10,
    "open_kernel_px": 4,
    # Fill holes inside each zone's binary mask after morphology.
    # Useful when zone polygons are riddled with white pixels (street
    # networks, text labels) that the closing kernel can't bridge.
    # Set to False for maps where small disjoint polygons of the same
    # color are expected (e.g. Map 08 risk areas).
    "fill_holes": True,
    # --- Overlay pre-processing (see overlay_mask.detect_overlay) ---
    # Each detector can be toggled off independently. When all four
    # are False the pipeline behaves exactly like before this step
    # was introduced.
    "overlay_detect_streets": True,
    "overlay_detect_text": True,
    "overlay_detect_contours": True,
    "overlay_detect_rivers": False,  # risky: several zones are blue
    # Iterations of 3x3 majority voting that fill overlay pixels with
    # their neighbors' labels. Each iteration extends the labeled area
    # by ~1 pixel; 20 covers overlay regions up to ~40 px wide.
    "overlay_propagation_max_iters": 20,
    # Per-detector thresholds (see overlay_mask.DEFAULTS). Leave empty
    # to use library defaults; override keys here to retune any single
    # detector without redeclaring all of them.
    "overlay_thresholds": {},
    # Polygon filters and simplification
    "min_polygon_area_px2": 5000,
    "simplify_tolerance_px": 10,
}


@dataclass(frozen=True)
class ZoneSpec:
    """One legend entry from the YAML.

    Most zones match by hue. Zones whose legend color is grayscale
    (saturation 0 in the PDF, e.g. ZC-1 in São José Map 03) cannot be
    matched that way; for them, set ``match_by="low_saturation"`` and
    constrain via ``value_min``/``value_max`` to separate the gray
    fill from hillshade.
    """

    code: str
    name: str
    color_hex: str
    color_rgb: tuple[int, int, int]
    match_by: str = "hue"  # "hue" | "low_saturation" | "low_value"
    saturation_max: int = 25      # low_saturation only
    value_min: int = 150          # low_saturation and low_value
    value_max: int = 210          # low_saturation and low_value
    hue_target: int = 0           # low_value only: expected hue
    hue_tolerance: int = 15       # low_value only: max hue distance
    saturation_min_zone: int = 50 # low_value only: min saturation
    min_area_px2: int | None = None
    # Optional bounding box filter [west, south, east, north] in the
    # map's CRS (metres). Polygons whose centroid falls outside this
    # box are discarded. Used to exclude false positives that share a
    # color with the target zone but appear in a geographically
    # implausible location (e.g. hillshade or text on the far side of
    # the municipality).
    bbox_filter: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class VectorizeResult:
    """What the vectorizer produced for one map."""

    map_id: str
    out_path: Path
    n_polygons: int
    crs: str


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: {h!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def load_map_config(yaml_path: Path) -> dict[str, Any]:
    """Read a per-map YAML and normalize it into the shape used internally.

    The YAML schema looks like:

        map_id: map_02
        source_tif: data/raw/sao_jose_sc/master_plan/overlays/mapa_02_macrozoneamento.tif
        zones:
          - code: MZ-A
            name: Macrozona A
            color_hex: "#df9a95"
          - ...
        # optional overrides
        saturation_min: 30
        close_kernel_px: 25
        ...

    Returns a dict with parsed ZoneSpecs and resolved knobs.
    """
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if "map_id" not in raw or "zones" not in raw:
        raise ValueError(
            f"{yaml_path}: required keys 'map_id' and 'zones' missing"
        )

    zones = []
    for z in raw["zones"]:
        kwargs = dict(
            code=z["code"],
            name=z["name"],
            color_hex=z["color_hex"],
            color_rgb=_hex_to_rgb(z["color_hex"]),
        )
        if "match_by" in z:
            kwargs["match_by"] = z["match_by"]
        if "saturation_max" in z:
            kwargs["saturation_max"] = int(z["saturation_max"])
        if "value_min" in z:
            kwargs["value_min"] = int(z["value_min"])
        if "value_max" in z:
            kwargs["value_max"] = int(z["value_max"])
        if "hue_target" in z:
            kwargs["hue_target"] = int(z["hue_target"])
        if "hue_tolerance" in z:
            kwargs["hue_tolerance"] = int(z["hue_tolerance"])
        if "saturation_min_zone" in z:
            kwargs["saturation_min_zone"] = int(z["saturation_min_zone"])
        if "min_area_px2" in z:
            kwargs["min_area_px2"] = int(z["min_area_px2"])
        if "bbox_filter" in z:
            bf = z["bbox_filter"]
            kwargs["bbox_filter"] = (float(bf[0]), float(bf[1]), float(bf[2]), float(bf[3]))
        zones.append(ZoneSpec(**kwargs))

    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    cfg["map_id"] = raw["map_id"]
    cfg["source_tif"] = raw["source_tif"]
    cfg["zones"] = zones
    return cfg


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


def _read_rgb(tif_path: Path) -> tuple[np.ndarray, rasterio.Affine, str, int, int]:
    """Read first 3 bands of the GeoTIFF as an (H, W, 3) uint8 array."""
    with rasterio.open(tif_path) as src:
        if src.count < 3:
            raise ValueError(
                f"{tif_path}: expected >=3 bands (RGB), got {src.count}"
            )
        r = src.read(1)
        g = src.read(2)
        b = src.read(3)
        transform = src.transform
        crs = str(src.crs) if src.crs is not None else None
        if crs is None:
            raise ValueError(f"{tif_path}: no CRS in header; re-export from QGIS")
    rgb = np.dstack([r, g, b]).astype(np.uint8, copy=False)
    return rgb, transform, crs, rgb.shape[1], rgb.shape[0]


def _hue_dist(h1: np.ndarray, h2: int) -> np.ndarray:
    """Circular distance in OpenCV's hue space (0..179)."""
    d = np.abs(h1.astype(np.int16) - h2)
    return np.minimum(d, 180 - d)


def _segment_by_hue(
    rgb: np.ndarray,
    zones: list[ZoneSpec],
    saturation_min: int,
    value_min: int,
    value_max: int,
    max_hue_distance: int,
) -> np.ndarray:
    """Assign each pixel to a zone using per-zone matching strategy.

    Two strategies:

    - ``match_by="hue"`` (default): hue distance to the zone's target,
      gated by saturation/value floors and ``max_hue_distance``. Catches
      every chromatic zone (saturated colors).

    - ``match_by="low_saturation"``: matches by saturation < zone's
      saturation_max AND value in [zone.value_min, zone.value_max].
      Used for legend colors that are pure grayscale (e.g. ZC-1 in
      São José Map 03), which hue cannot distinguish.

    Pixels not assigned to any zone are -1 (background).

    Hue-strategy zones win first; low-saturation zones only claim pixels
    that no hue-strategy zone took. This avoids gray-area pixels stealing
    pixels from desaturated edges of chromatic zones.
    """
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    label_map = np.full(rgb.shape[:2], -1, dtype=np.int16)

    # --- Pass 0: low_value zones (pre-empt hue winner-takes-all) ---
    # These zones share hue with a brighter zone but are distinguished
    # by having a much lower value (luminosity). We claim their pixels
    # first so the hue pass doesn't assign them to the brighter rival.
    for i, z in enumerate(zones):
        if z.match_by != "low_value":
            continue
        hd = np.minimum(
            np.abs(h.astype(np.int16) - z.hue_target),
            180 - np.abs(h.astype(np.int16) - z.hue_target),
        )
        mask = (
            (hd <= z.hue_tolerance)
            & (s >= z.saturation_min_zone)
            & (v >= z.value_min)
            & (v <= z.value_max)
        )
        label_map[mask] = i

    # --- Pass 1: hue-strategy zones ---
    hue_indices = [i for i, z in enumerate(zones) if z.match_by == "hue"]
    if hue_indices:
        target_hues: list[int] = []
        for i in hue_indices:
            rgb_arr = np.uint8([[list(zones[i].color_rgb)]])
            hsv_t = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2HSV)[0][0]
            target_hues.append(int(hsv_t[0]))

        valid_hue = (s > saturation_min) & (v > value_min) & (v < value_max)
        unclaimed = label_map == -1  # don't overwrite Pass 0 assignments
        dists = np.stack([_hue_dist(h, t) for t in target_hues], axis=-1)
        winner_local = np.argmin(dists, axis=-1)
        min_dist = np.min(dists, axis=-1)
        in_palette = min_dist <= max_hue_distance
        winner_global = np.array(hue_indices, dtype=np.int16)[winner_local]
        label_map = np.where(valid_hue & in_palette & unclaimed, winner_global, label_map).astype(np.int16)

    # --- Pass 2: low_saturation zones, only on pixels not already claimed ---
    unclaimed = label_map == -1
    for i, z in enumerate(zones):
        if z.match_by != "low_saturation":
            continue
        mask = (
            unclaimed
            & (s <= z.saturation_max)
            & (v >= z.value_min)
            & (v <= z.value_max)
        )
        label_map[mask] = i
        # Update unclaimed for subsequent low_sat zones (if any)
        unclaimed = label_map == -1

    return label_map


def _propagate_labels(
    label_map: np.ndarray,
    overlay_mask: np.ndarray,
    max_iters: int = 20,
) -> np.ndarray:
    """Spread existing labels into overlay pixels via 3x3 majority voting.

    Each iteration looks at every still-unassigned overlay pixel and
    reassigns it to whichever already-classified label appears most
    often in its 8-neighborhood. The process repeats until no pixel
    changes or ``max_iters`` is reached.

    Why not cv2.inpaint? Inpaint interpolates pixel values continuously,
    which produces colors between zones and polygonizes poorly. We need
    a clean categorical propagation so the downstream ``raster_shapes``
    call produces sharp polygon boundaries.
    """
    result = label_map.copy().astype(np.int16)
    result[overlay_mask] = -1  # ensure overlay pixels start unassigned

    unique_labels = [int(v) for v in np.unique(result) if v != -1]
    if not unique_labels:
        return result

    kernel = np.ones((3, 3), dtype=np.float32)

    for _ in range(max_iters):
        max_count = np.zeros(result.shape, dtype=np.float32)
        winner = np.full(result.shape, -1, dtype=np.int16)

        for lbl in unique_labels:
            cnt = cv2.filter2D(
                (result == lbl).astype(np.float32),
                ddepth=-1,
                kernel=kernel,
                borderType=cv2.BORDER_CONSTANT,
            )
            better = cnt > max_count
            np.copyto(max_count, cnt, where=better)
            np.copyto(winner, np.int16(lbl), where=better)

        update = (result == -1) & (max_count > 0)
        if not update.any():
            break

        result = np.where(update, winner, result).astype(np.int16)

    return result


def _clean_mask(
    mask: np.ndarray, close_px: int, open_px: int, fill_holes: bool
) -> np.ndarray:
    """Morphological cleanup followed by optional hole filling.

    Closing bridges small gaps (thin streets); opening removes speckle;
    hole filling repaints any background blob fully enclosed inside the
    foreground. Hole filling is what defeats white street networks and
    text labels covering large portions of a zone — closing can only
    bridge gaps narrower than its kernel.
    """
    if close_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    if open_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_px, open_px))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    if fill_holes:
        mask = _fill_holes(mask)
    return mask


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill any background pixel fully enclosed by the foreground.

    Works by finding external contours only and redrawing each as a
    filled shape. Background blobs touching the image border (the
    "real outside") are preserved.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, color=255, thickness=cv2.FILLED)
    return filled


# ---------------------------------------------------------------------------
# Polygonization
# ---------------------------------------------------------------------------


def _mask_to_polygons(
    mask: np.ndarray,
    transform: rasterio.Affine,
    min_area_px2: float,
    simplify_tol_px: float,
    pixel_size_m: float,
) -> list[BaseGeometry]:
    """Run rasterio.features.shapes on a boolean mask.

    Polygons come out already in the GeoTIFF's CRS thanks to ``transform``.
    Filtering and simplification are applied in pixel-equivalent units,
    converted via ``pixel_size_m`` to keep YAML tolerances portable.
    """
    geoms: list[BaseGeometry] = []
    min_area_m2 = min_area_px2 * (pixel_size_m ** 2)
    simplify_tol_m = simplify_tol_px * pixel_size_m

    for geom, value in raster_shapes(
        mask.astype(np.uint8),
        mask=(mask > 0),
        transform=transform,
    ):
        if value != 1:
            continue
        shp = sg.shape(geom)
        if shp.area < min_area_m2:
            continue
        shp = shp.simplify(simplify_tol_m, preserve_topology=True)
        if shp.is_empty or not shp.is_valid:
            shp = shp.buffer(0)
            if shp.is_empty:
                continue
        geoms.append(shp)
    return geoms


def _pixel_size_meters(transform: rasterio.Affine) -> float:
    """Approximate pixel size from the affine. Uses |a| (x scale)."""
    return abs(transform.a)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def vectorize_map(
    yaml_path: Path,
    project_root: Path,
    out_dir: Path,
    *,
    boundary_path: Path | None = None,
    force: bool = False,
) -> VectorizeResult:
    """Vectorize one Master Plan map.

    Args:
        yaml_path: Path to ``config/master_plan/<region>/map_NN.yaml``.
        project_root: Project root, used to resolve ``source_tif`` relative paths.
        out_dir: Where to write the GeoParquet (e.g.
            ``data/processed/<region>/master_plan/vectors/``).
        boundary_path: Optional GeoParquet/Shapefile defining the
            municipal boundary. When provided, all output polygons are
            intersected with this boundary — anything outside (e.g.
            ocean wrongly classified by hue) is removed.
        force: If False and the output already exists, skip.

    Returns:
        VectorizeResult with the output path and polygon count.
    """
    cfg = load_map_config(yaml_path)
    map_id = cfg["map_id"]
    out_path = out_dir / f"{map_id}.geoparquet"

    if out_path.exists() and not force:
        logger.info("Cached vector output: %s", out_path)
        # Read just to count polygons for the result
        gdf = gpd.read_parquet(out_path)
        return VectorizeResult(
            map_id=map_id,
            out_path=out_path,
            n_polygons=len(gdf),
            crs=str(gdf.crs),
        )

    source_tif = project_root / cfg["source_tif"]
    if not source_tif.exists():
        raise FileNotFoundError(
            f"GeoTIFF not found: {source_tif}. "
            f"Has Phase 3d been completed for this map?"
        )

    logger.info("Vectorizing %s -> %s", source_tif.name, out_path.name)

    # 1. Read RGB + affine
    rgb, transform, crs, width, height = _read_rgb(source_tif)
    pixel_size_m = _pixel_size_meters(transform)
    logger.info(
        "  source: %dx%d, CRS=%s, pixel_size=%.3f m",
        width, height, crs, pixel_size_m,
    )

    # Optional boundary clip — eliminates polygons in the ocean or
    # neighboring municipalities that match a zone's hue by accident.
    # The buffered boundary is what we clip against; the original
    # boundary is kept separately to flag "fringe" polygons that only
    # survived because of the buffer (used to filter out elongated
    # text labels and legend strips).
    clip_geom = None
    strict_boundary = None
    boundary_buffer_m = float(cfg["boundary_buffer_m"])
    fringe_max_ar = float(cfg["fringe_max_aspect_ratio"])
    fringe_max_centroid_dist_m = float(cfg["fringe_max_centroid_distance_m"])
    if boundary_path is not None and boundary_path.exists():
        boundary_gdf = gpd.read_parquet(boundary_path).to_crs(crs)
        strict_boundary = boundary_gdf.geometry.unary_union
        clip_geom = strict_boundary.buffer(boundary_buffer_m) if boundary_buffer_m > 0 else strict_boundary
        logger.info(
            "  clipping output by boundary: %s (buffer=%.0fm, fringe AR limit=%.1f)",
            boundary_path.name,
            boundary_buffer_m,
            fringe_max_ar,
        )

    # 2. Segment by hue
    label_map = _segment_by_hue(
        rgb,
        cfg["zones"],
        saturation_min=cfg["saturation_min"],
        value_min=cfg["value_min"],
        value_max=cfg["value_max"],
        max_hue_distance=cfg["max_hue_distance"],
    )

    # 2b. Detect cartographic overlay (streets, text, hillshade
    #     contour lines, rivers) and propagate neighboring zone labels
    #     into those pixels. Skipped entirely when all detectors are off.
    overlay = detect_overlay(rgb, cfg)
    if overlay.any():
        logger.info("  propagating labels into overlay pixels")
        label_map = _propagate_labels(
            label_map,
            overlay,
            max_iters=int(cfg["overlay_propagation_max_iters"]),
        )

    # 3. Per-zone: morphology + polygonization
    close_px = int(cfg["close_kernel_px"])
    open_px = int(cfg["open_kernel_px"])
    min_area_px2 = float(cfg["min_polygon_area_px2"])
    simplify_tol_px = float(cfg["simplify_tolerance_px"])

    rows = []
    for i, zone in enumerate(cfg["zones"]):
        zone_mask = (label_map == i).astype(np.uint8)
        if zone_mask.sum() == 0:
            logger.warning("  zone %s (%s): no pixels matched", zone.code, zone.color_hex)
            continue

        # Per-zone area floor (used by low_saturation zones to suppress
        # small false-positive blobs); falls back to the map-level default.
        zone_min_area = zone.min_area_px2 if zone.min_area_px2 is not None else min_area_px2

        cleaned = _clean_mask(zone_mask * 255, close_px, open_px, fill_holes=bool(cfg["fill_holes"]))
        polygons = _mask_to_polygons(
            cleaned // 255,  # back to 0/1
            transform,
            min_area_px2=zone_min_area,
            simplify_tol_px=simplify_tol_px,
            pixel_size_m=pixel_size_m,
        )

        if not polygons:
            logger.warning("  zone %s: 0 polygons after filtering", zone.code)
            continue

        n_kept = 0
        for poly in polygons:
            if clip_geom is not None:
                poly = poly.intersection(clip_geom)
                if poly.is_empty:
                    continue
                # intersection can produce MultiPolygon — split into pieces
                if poly.geom_type == "MultiPolygon":
                    pieces = list(poly.geoms)
                else:
                    pieces = [poly]
            else:
                pieces = [poly]

            for piece in pieces:
                if piece.area < zone_min_area * (pixel_size_m ** 2):
                    continue
                # Bbox filter: if the zone declares a geographic bounding
                # box, discard any piece whose centroid falls outside it.
                if zone.bbox_filter is not None:
                    cx, cy = piece.centroid.x, piece.centroid.y
                    w, s, e, n = zone.bbox_filter
                    if not (w <= cx <= e and s <= cy <= n):
                        continue
                # Fringe filter: a piece that doesn't intersect the
                # un-buffered boundary only survived because of the
                # buffer. Two ways to flag it as bogus:
                #   - elongated bbox (text labels / legend strips)
                #   - centroid sitting far from the actual boundary
                #     (logos, ocean decorations, ink from neighboring
                #     municipalities)
                if (
                    strict_boundary is not None
                    and not piece.intersects(strict_boundary)
                ):
                    minx, miny, maxx, maxy = piece.bounds
                    w = maxx - minx
                    h = maxy - miny
                    ar = max(w, h) / max(min(w, h), 1e-6)
                    if ar > fringe_max_ar:
                        continue
                    centroid_dist = piece.centroid.distance(strict_boundary)
                    if centroid_dist > fringe_max_centroid_dist_m:
                        continue
                rows.append(
                    {
                        "map_id": map_id,
                        "zone_code": zone.code,
                        "zone_name": zone.name,
                        "color_hex": zone.color_hex,
                        "geometry": piece,
                    }
                )
                n_kept += 1
        logger.info("  zone %s: %d polygons", zone.code, n_kept)

    if not rows:
        raise RuntimeError(
            f"{map_id}: no polygons produced. "
            f"Check the YAML's color_hex values and HSV tolerances."
        )

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)

    out_dir.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out_path)

    logger.info(
        "Wrote %d polygons across %d zones to %s",
        len(gdf),
        gdf["zone_code"].nunique(),
        out_path,
    )

    return VectorizeResult(
        map_id=map_id,
        out_path=out_path,
        n_polygons=len(gdf),
        crs=crs,
    )


def vectorize_all_maps(
    yaml_dir: Path,
    project_root: Path,
    out_dir: Path,
    *,
    boundary_path: Path | None = None,
    force: bool = False,
) -> list[VectorizeResult]:
    """Vectorize every map_*.yaml in a directory."""
    yamls = sorted(yaml_dir.glob("map_*.yaml"))
    if not yamls:
        raise FileNotFoundError(f"No map_*.yaml found in {yaml_dir}")

    results = []
    for yaml_path in yamls:
        try:
            results.append(
                vectorize_map(
                    yaml_path=yaml_path,
                    project_root=project_root,
                    out_dir=out_dir,
                    boundary_path=boundary_path,
                    force=force,
                )
            )
        except Exception as exc:
            logger.error("Failed to vectorize %s: %s", yaml_path.name, exc)
            raise

    return results
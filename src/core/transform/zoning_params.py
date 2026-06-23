"""Load per-zone urbanistic parameters (LC 173/2024) from YAML.

The spatial layers tell you *which* zone a point is in; this table tells
you *what may be built there* (floors, floor-area ratio, occupancy, …).
Keyed by the individual zone code so the app can resolve a point via its
OCR label (co-colored subzones share one polygon but differ in params).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# repo root: src/core/transform/zoning_params.py -> parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def parameters_path(region_slug: str, project_root: Path | None = None) -> Path:
    root = project_root or PROJECT_ROOT
    return root / "config" / "master_plan" / region_slug / "parameters.yaml"


def load_zone_parameters(
    region_slug: str, project_root: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Return ``{zone_code: {param: value}}`` for a region, or {} if absent."""
    path = parameters_path(region_slug, project_root)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("zones", {})

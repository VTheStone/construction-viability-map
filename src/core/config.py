"""Configuration loader.

Loads global defaults from ``config/global.yaml`` and merges them with
a region-specific YAML from ``config/regions/<slug>.yaml``.

The merged config is returned as a plain ``dict`` for simplicity — keys
are documented in ``config/regions/_template.yaml``.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
GLOBAL_CONFIG_PATH = CONFIG_DIR / "global.yaml"
REGIONS_DIR = CONFIG_DIR / "regions"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base``. ``override`` wins on leaves."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping at top level of {path}")
    return data


def load_global_config() -> dict[str, Any]:
    """Load the global defaults from ``config/global.yaml``."""
    return _load_yaml(GLOBAL_CONFIG_PATH)


def load_region_config(region_slug: str) -> dict[str, Any]:
    """Load global + region YAMLs, merged.

    Args:
        region_slug: e.g. ``"sao_jose_sc"``.

    Returns:
        Merged config dict.
    """
    global_cfg = load_global_config()
    region_path = REGIONS_DIR / f"{region_slug}.yaml"
    region_cfg = _load_yaml(region_path)
    merged = _deep_merge(global_cfg, region_cfg)

    # Sanity check
    declared_slug = merged.get("region", {}).get("slug")
    if declared_slug and declared_slug != region_slug:
        raise ValueError(
            f"Region slug mismatch: file is '{region_slug}.yaml' but "
            f"declares slug='{declared_slug}'"
        )
    return merged


def list_available_regions() -> list[str]:
    """Return slugs of all region YAMLs (excluding the template)."""
    return sorted(
        p.stem for p in REGIONS_DIR.glob("*.yaml") if not p.stem.startswith("_")
    )
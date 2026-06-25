"""Tests for the indicative buildability verdict (Phase 10 M6)."""

from src.app.inspect import viability_verdict


def test_app_is_restrito():
    assert viability_verdict({"in_app": True})["level"] == "restrito"


def test_high_ca_low_slope_is_alto():
    v = viability_verdict({"slope_pct": 4, "potential": {"ca_maximo": 6.5}})
    assert v["level"] == "alto"


def test_steep_slope_lowers_level():
    v = viability_verdict({"slope_pct": 45, "potential": {"ca_maximo": 6.5}})
    assert v["level"] == "baixo"

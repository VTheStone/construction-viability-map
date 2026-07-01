"""Tests for the LC 173/2024 zone-parameter loader (Phase 10 M1)."""

from src.core.transform.zoning_params import load_zone_parameters

EXPECTED = {
    "ZA-1", "ZA-2", "ZA-3", "ZA-4", "ZA-5", "ZA-6", "ZA-7", "ZA-8",
    "ZA-9", "ZA-10", "ZA-11", "ZA-12", "ZB-1", "ZB-2", "ZB-3",
    "ZC-1", "ZC-2", "ZR",
}


def test_all_base_zones_present():
    params = load_zone_parameters("sao_jose_sc")
    assert EXPECTED.issubset(params), EXPECTED - set(params)


def test_buildable_zones_have_core_fields():
    params = load_zone_parameters("sao_jose_sc")
    required = {"ca_basico", "ca_maximo", "pavimentos_max", "area_min_m2"}
    for code, p in params.items():
        if p.get("rural") or p.get("preservacao") or p.get("especial"):
            continue  # rural / preservation / special-project zones have no fixed params
        missing = required - set(p)
        assert not missing, f"{code} missing {missing}"


def test_ca_basico_not_above_maximo():
    params = load_zone_parameters("sao_jose_sc")
    for code, p in params.items():
        bas, mx = p.get("ca_basico"), p.get("ca_maximo")
        if isinstance(bas, (int, float)) and isinstance(mx, (int, float)):
            assert bas <= mx, f"{code}: ca_basico {bas} > ca_maximo {mx}"



def test_subzones_present_and_valid():
    from src.core.transform.zoning_params import load_zoning_subzones

    sub = load_zoning_subzones("sao_jose_sc")
    assert sub is not None
    assert len(sub) == 17
    assert int((~sub.geometry.is_valid).sum()) == 0


def test_every_subzone_has_parameters():
    from src.core.transform.zoning_params import (
        load_zone_parameters,
        load_zoning_subzones,
    )

    params = load_zone_parameters("sao_jose_sc")
    sub = load_zoning_subzones("sao_jose_sc")
    for code in sub["zone_code"]:
        assert code in params, code
        
        
def test_aei_zones_load():
    from src.core.transform.zoning_params import load_aei_zones

    aei = load_aei_zones("sao_jose_sc")
    assert aei is not None and len(aei) > 0
    assert aei["zone_code"].astype(str).str.startswith("AEI").all()
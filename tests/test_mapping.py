"""Mapping engine correctness: bindings, unit normalization, missing-value policy."""

import pytest

from fl_op.canonical.enums import QualitySeverity
from fl_op.mapping import MappingEngine


@pytest.fixture(scope="module")
def engine() -> MappingEngine:
    return MappingEngine()


def _vehicle_row(**overrides):
    row = {
        "vehicle_id": "v1",
        "vehicle_type": "TRACTOR",
        "rated_power_kw": "180.0",
        "fuel_tank_l": "400",
        "fuel_consumption_l_per_h": "22",
        "current_lat": "50.1",
        "current_lon": "28.2",
        "depot_id": "depot_0",
        "travel_speed_kmh": "15",
    }
    row.update(overrides)
    return row


def test_vehicle_maps_to_asset_with_capabilities(engine: MappingEngine) -> None:
    res = engine.map_dataset("vehicles", [_vehicle_row()])
    assert len(res.assets) == 1
    asset = res.assets[0]
    assert asset.asset_id == "v1"
    assert asset.roles == ["mobile-prime-mover"]
    assert asset.capability_value("urn:xopt:capability:rated-power") == 180.0
    assert asset.location is not None
    assert asset.location.lat == pytest.approx(50.1)
    assert asset.home_depot_ref == "depot_0"


def test_categorical_set_parsed_from_stringified_list(engine: MappingEngine) -> None:
    row = {
        "implement_id": "i1",
        "implement_type": "PLOW",
        "compatible_operations": "['TILLAGE', 'SEEDING']",
        "required_power_kw": "120",
        "working_width_m": "4",
        "min_speed_kmh": "5",
        "max_speed_kmh": "12",
        "fertilizer_capacity_kg": "0",
        "depot_id": "depot_0",
    }
    res = engine.map_dataset("implements", [row])
    ops = res.assets[0].capability_value("urn:xopt:capability:compatible-operations")
    assert ops == ["TILLAGE", "SEEDING"]


def test_reject_for_planning_drops_entity_with_finding(engine: MappingEngine) -> None:
    # rated_power_kw has missingValuePolicy reject-for-planning.
    res = engine.map_dataset("vehicles", [_vehicle_row(rated_power_kw="")])
    assert res.assets == []
    assert "v1" in res.excluded["vehicles"]
    assert any(
        f.severity == QualitySeverity.ERROR and f.field_ref == "rated_power_kw"
        for f in res.findings
    )


def test_fallback_policy_substitutes_and_records_finding(engine: MappingEngine) -> None:
    # travel_speed_kmh has missingValuePolicy fallback-to-conservative-value.
    res = engine.map_dataset("vehicles", [_vehicle_row(travel_speed_kmh="")])
    assert len(res.assets) == 1
    assert any(
        f.action_applied == "fallback-to-conservative-value"
        and f.field_ref == "travel_speed_kmh"
        for f in res.findings
    )


def test_unit_conversion_applied(engine: MappingEngine) -> None:
    # Identity path: canonical unit kW, source already kW -> unchanged.
    res = engine.map_dataset("vehicles", [_vehicle_row(rated_power_kw="200")])
    assert res.assets[0].capability_value("urn:xopt:capability:rated-power") == 200.0

"""Sensor and observation mapping: stationary assets, optional reading payloads."""

import pytest

from fl_op.canonical.enums import AssetMobility
from fl_op.mapping import MappingEngine


@pytest.fixture(scope="module")
def engine() -> MappingEngine:
    return MappingEngine()


def _sensor_row(**overrides):
    row = {
        "sensor_id": "sensor_1",
        "sensor_type": "SOIL_MOISTURE_PROBE",
        "field_id": "field_000001",
        "lat": "50.2",
        "lon": "28.4",
        "mobility": "stationary",
        "last_service_at": "2026-01-15T00:00:00+00:00",
        "service_interval_days": "180",
    }
    row.update(overrides)
    return row


def _reading_row(**overrides):
    row = {
        "reading_id": "reading_1",
        "sensor_id": "sensor_1",
        "metric": "battery-level",
        "value": "17.5",
        "state_value": "",
        "unit": "%",
        "observed_at": "2026-06-01T06:00:00+00:00",
    }
    row.update(overrides)
    return row


def test_sensor_maps_to_stationary_asset_with_state(engine: MappingEngine) -> None:
    res = engine.map_dataset("sensors", [_sensor_row()])
    assert len(res.assets) == 1
    asset = res.assets[0]
    assert asset.asset_id == "sensor_1"
    assert asset.mobility == AssetMobility.STATIONARY.value
    assert asset.roles == ["stationary-equipment"]
    assert asset.home_depot_ref == "field_000001"
    interval = asset.capability_value("urn:xopt:maintenance:service-interval")
    assert float(interval) == 180.0
    assert asset.capability_value("urn:xopt:maintenance:last-service-at") is not None


def test_numeric_reading_maps_to_observation(engine: MappingEngine) -> None:
    res = engine.map_dataset("sensor-readings", [_reading_row()])
    assert len(res.observations) == 1
    obs = res.observations[0]
    assert obs.entity_ref == "sensor_1"
    assert obs.metric == "battery-level"
    assert obs.value == pytest.approx(17.5)
    assert obs.observed_at is not None


def test_raw_metric_code_is_normalized_via_metric_codes(engine: MappingEngine) -> None:
    # The sensor-readings mapping declares metricCodes: battery_pct -> battery-level.
    res = engine.map_dataset("sensor-readings", [_reading_row(metric="battery_pct")])
    assert res.observations[0].metric == "battery-level"


def test_unmapped_metric_code_passes_through(engine: MappingEngine) -> None:
    res = engine.map_dataset("sensor-readings", [_reading_row(metric="vibration_hz")])
    assert res.observations[0].metric == "vibration_hz"


def test_categorical_reading_with_empty_value_is_kept(engine: MappingEngine) -> None:
    # A reading carries either a numeric value or a categorical state; the
    # accept-optional policy must keep the row without quality findings.
    row = _reading_row(value="", state_value="degraded", metric="health-status", unit="")
    res = engine.map_dataset("sensor-readings", [row])
    assert len(res.observations) == 1
    obs = res.observations[0]
    assert obs.value is None
    assert obs.state_value == "degraded"
    assert res.findings == []
    assert res.excluded == {}


def test_reading_without_sensor_ref_is_rejected(engine: MappingEngine) -> None:
    res = engine.map_dataset("sensor-readings", [_reading_row(sensor_id="")])
    assert res.observations == []
    assert "sensor-readings" in res.excluded

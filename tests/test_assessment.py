"""Statistical observation assessment: outliers, faults, drift, confidence."""

from datetime import datetime, timedelta, timezone

import pytest

from fl_op.canonical.observation import Observation
from fl_op.core.constants import METRIC_BATTERY_LEVEL
from fl_op.snapshot.assessment import assess_observations
from fl_op.snapshot.monitoring import derive_service_tasks
from fl_op.canonical.asset import Asset
from fl_op.canonical.enums import AssetMobility

_NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _series(values: list[float], metric: str = METRIC_BATTERY_LEVEL, entity: str = "s1"):
    return [
        Observation(
            observation_id=f"o-{entity}-{metric}-{i}",
            entity_ref=entity,
            metric=metric,
            value=v,
            observed_at=_NOW - timedelta(hours=len(values) - i),
            source_ref=f"sensor-readings:o-{i}",
        )
        for i, v in enumerate(values)
    ]


def _sensor(asset_id: str = "s1") -> Asset:
    return Asset(
        asset_id=asset_id,
        asset_type="SOIL_MOISTURE_PROBE",
        roles=["stationary-equipment"],
        mobility=AssetMobility.STATIONARY.value,
        home_depot_ref="field_1",
    )


def test_outlier_reading_is_excluded_with_finding() -> None:
    obs = _series([80.0, 79.5, 79.0, 78.5, 5.0, 78.0])
    result = assess_observations(obs, _NOW)
    values = [o.value for o in result.observations]
    assert 5.0 not in values
    assert len(result.observations) == 5
    outlier_findings = [f for f in result.findings if f.rule_id == "dq://observation/outlier"]
    assert len(outlier_findings) == 1
    assert result.error_rates["sensor-readings"] > 0


def test_single_bad_reading_does_not_trigger_service_task() -> None:
    # Healthy battery with one spurious near-zero reading: the assessment
    # removes the outlier so monitoring sees only the healthy trend.
    obs = _series([82.0, 81.5, 81.0, 80.5, 2.0, 80.0])
    result = assess_observations(obs, _NOW)
    tasks = derive_service_tasks([_sensor()], result.observations, _NOW)
    assert tasks == []


def test_rising_battery_marks_series_suspect_and_suppresses_tasks() -> None:
    # Battery recharging by 7 pct without a service visit (moderate enough not
    # to be cut as an outlier) marks the series as a suspected fault.
    obs = _series([20.0, 19.5, 19.0, 26.0, 25.5, 25.0, 24.5, 24.0])
    result = assess_observations(obs, _NOW)
    fault_findings = [
        f for f in result.findings if f.rule_id == "dq://observation/sensor-fault"
    ]
    assert len(fault_findings) == 1
    assert all(o.confidence == 0.0 for o in result.observations)
    # Even though readings sit below the low-battery threshold, the suspect
    # series must not derive a service task.
    tasks = derive_service_tasks([_sensor()], result.observations, _NOW)
    assert tasks == []


def test_frozen_nonzero_series_is_suspect_but_zero_is_not() -> None:
    frozen = assess_observations(_series([55.0] * 8), _NOW)
    assert any(f.rule_id == "dq://observation/sensor-fault" for f in frozen.findings)

    dead = assess_observations(_series([0.0] * 8), _NOW)
    assert not any(f.rule_id == "dq://observation/sensor-fault" for f in dead.findings)
    # A constant-zero battery is a real signal: the service task must fire.
    tasks = derive_service_tasks([_sensor()], dead.observations, _NOW)
    assert len(tasks) == 1


def test_drifting_metric_yields_calibration_task() -> None:
    # Soil moisture stepping to a new level mid-series: drift, not battery.
    values = [30.0, 30.4, 29.8, 30.2, 44.8, 45.2, 45.4, 44.6]
    obs = _series(values, metric="soil-moisture")
    result = assess_observations(obs, _NOW)
    assert result.drifting_metrics == {"s1": ["soil-moisture"]}
    tasks = derive_service_tasks(
        [_sensor()], result.observations, _NOW, calibration_needs=result.drifting_metrics
    )
    assert len(tasks) == 1
    assert "calibration:soil-moisture-drift" in tasks[0].source_ref


def test_battery_decline_is_not_drift() -> None:
    obs = _series([90.0, 85.0, 80.0, 75.0, 70.0, 65.0, 60.0, 55.0])
    result = assess_observations(obs, _NOW)
    assert result.drifting_metrics == {}


def test_clean_series_has_full_confidence_and_no_findings() -> None:
    obs = _series([80.0, 79.6, 79.1, 78.7, 78.2, 77.8])
    result = assess_observations(obs, _NOW)
    assert result.findings == []
    assert all(o.confidence == 1.0 for o in result.observations)
    assert result.error_rates == {"sensor-readings": 0.0}


def test_bad_quality_flag_excludes_reading_with_finding() -> None:
    obs = _series([80.0, 79.5, 79.0, 78.5, 78.0])
    obs[2] = obs[2].model_copy(update={"quality_flag": "bad"})
    result = assess_observations(obs, _NOW)
    assert len(result.observations) == 4
    assert 79.0 not in [o.value for o in result.observations]
    flagged = [f for f in result.findings if f.rule_id == "dq://observation/source-flagged"]
    assert len(flagged) == 1
    assert result.error_rates["sensor-readings"] == pytest.approx(0.2)


def test_suspect_quality_flag_caps_reading_confidence() -> None:
    obs = _series([80.0, 79.5, 79.0])
    obs[-1] = obs[-1].model_copy(update={"quality_flag": "suspect"})
    result = assess_observations(obs, _NOW)
    by_id = {o.observation_id: o for o in result.observations}
    assert by_id[obs[-1].observation_id].confidence == pytest.approx(0.5)
    assert by_id[obs[0].observation_id].confidence == pytest.approx(1.0)


def test_retention_window_drops_old_readings() -> None:
    recent = _series([80.0, 79.5, 79.0])
    stale = [
        o.model_copy(update={"observed_at": _NOW - timedelta(days=30), "observation_id": "old-1"})
        for o in _series([60.0])
    ]
    result = assess_observations(stale + recent, _NOW, as_of=_NOW)
    ids = {o.observation_id for o in result.observations}
    assert "old-1" not in ids
    assert len(result.observations) == 3


def test_long_series_is_aggregated_into_windows_preserving_endpoints() -> None:
    values = [80.0 - 0.5 * i for i in range(50)]
    obs = _series(values)
    result = assess_observations(obs, _NOW, as_of=_NOW)
    # One representative per time window, plus the always-preserved oldest reading.
    assert len(result.observations) <= 33
    assert len(result.observations) < 50
    kept_values = [o.value for o in result.observations]
    assert values[0] in kept_values
    assert values[-1] in kept_values


def test_window_representatives_carry_min_mean_max_aggregates() -> None:
    """Downsampling preserves extremes: a spike inside a window survives as
    the representative's window_min/window_max even when its reading is gone."""
    values = [80.0] * 50
    values[10] = 5.0  # spike that evenly-spaced sampling could drop
    obs = _series(values)
    result = assess_observations(obs, _NOW, as_of=_NOW)

    aggregated = [o for o in result.observations if o.window_n is not None]
    assert aggregated, "expected windowed representatives on a long series"
    assert min(o.window_min for o in aggregated) == pytest.approx(5.0)
    assert max(o.window_max for o in aggregated) == pytest.approx(80.0)
    for rep in aggregated:
        assert rep.window_min <= rep.window_mean <= rep.window_max
        assert rep.window_n >= 1


def test_short_series_readings_carry_no_window_aggregates() -> None:
    result = assess_observations(_series([80.0, 79.5, 79.0]), _NOW, as_of=_NOW)
    assert all(o.window_n is None for o in result.observations)

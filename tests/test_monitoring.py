"""Monitoring policy: service-task derivation for stationary equipment."""

from datetime import datetime, timedelta, timezone

from fl_op.canonical.asset import Asset, Capability
from fl_op.canonical.enums import AssetMobility, HealthStatus
from fl_op.canonical.observation import Observation
from fl_op.contracts.profile import MonitoringPolicyOverride, MonitoringPolicySpec
from fl_op.core.constants import (
    BATTERY_LOW_THRESHOLD_PCT,
    EQUIPMENT_SERVICE_OPERATION,
    METRIC_BATTERY_LEVEL,
    METRIC_HEALTH_STATUS,
)
from fl_op.snapshot.monitoring import derive_service_tasks, latest_observations

_NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _sensor(asset_id: str = "s1", anchor: str = "field_1", **kwargs) -> Asset:
    return Asset(
        asset_id=asset_id,
        asset_type="SOIL_MOISTURE_PROBE",
        roles=["stationary-equipment"],
        mobility=AssetMobility.STATIONARY.value,
        home_depot_ref=anchor,
        **kwargs,
    )


def _battery_obs(level: float, asset_id: str = "s1", at: datetime = _NOW) -> Observation:
    return Observation(
        observation_id=f"o-{asset_id}-{level}",
        entity_ref=asset_id,
        metric=METRIC_BATTERY_LEVEL,
        value=level,
        observed_at=at,
    )


def test_low_battery_yields_service_task() -> None:
    tasks = derive_service_tasks([_sensor()], [_battery_obs(BATTERY_LOW_THRESHOLD_PCT - 1)], _NOW)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.operation_type == EQUIPMENT_SERVICE_OPERATION
    assert task.location_ref == "field_1"
    assert task.task_id == "service-s1"
    assert "battery-low" in task.source_ref


def test_healthy_battery_yields_no_task() -> None:
    tasks = derive_service_tasks([_sensor()], [_battery_obs(90.0)], _NOW)
    assert tasks == []


def test_critical_battery_escalates_priority_and_deadline() -> None:
    tasks = derive_service_tasks([_sensor()], [_battery_obs(3.0)], _NOW)
    assert len(tasks) == 1
    task = tasks[0]
    assert "escalated:battery-critical:3.0pct" in task.source_ref
    assert task.priority_class == 1
    assert task.deadline == _NOW + timedelta(days=1)


def test_failed_health_escalates() -> None:
    obs = Observation(
        observation_id="o-f",
        entity_ref="s1",
        metric=METRIC_HEALTH_STATUS,
        state_value=HealthStatus.FAILED.value,
        observed_at=_NOW,
    )
    tasks = derive_service_tasks([_sensor()], [obs], _NOW)
    assert len(tasks) == 1
    assert "escalated:health:failed" in tasks[0].source_ref
    assert tasks[0].priority_class == 1


def test_latest_observation_wins() -> None:
    old = _battery_obs(5.0, at=_NOW - timedelta(days=2))
    new = _battery_obs(95.0, at=_NOW)
    tasks = derive_service_tasks([_sensor()], [old, new], _NOW)
    assert tasks == []
    latest = latest_observations([old, new])
    assert latest[("s1", METRIC_BATTERY_LEVEL)].value == 95.0


def test_degraded_health_yields_service_task() -> None:
    obs = Observation(
        observation_id="o-h",
        entity_ref="s1",
        metric=METRIC_HEALTH_STATUS,
        state_value=HealthStatus.DEGRADED.value,
        observed_at=_NOW,
    )
    tasks = derive_service_tasks([_sensor()], [obs], _NOW)
    assert len(tasks) == 1
    assert "health:degraded" in tasks[0].source_ref


def test_overdue_service_interval_yields_service_task() -> None:
    asset = _sensor(
        capabilities=[
            Capability(
                capability_id="lastServiceAt",
                semantic_term="urn:xopt:maintenance:last-service-at",
                value=(_NOW - timedelta(days=400)).isoformat(),
            ),
            Capability(
                capability_id="serviceIntervalDays",
                semantic_term="urn:xopt:maintenance:service-interval",
                value=180.0,
                canonical_unit="d",
            ),
        ]
    )
    tasks = derive_service_tasks([asset], [], _NOW)
    assert len(tasks) == 1
    assert "service-overdue" in tasks[0].source_ref


def test_mobile_assets_are_ignored() -> None:
    mobile = Asset(
        asset_id="v1",
        asset_type="TRACTOR",
        roles=["mobile-prime-mover"],
        home_depot_ref="depot_1",
    )
    tasks = derive_service_tasks([mobile], [_battery_obs(1.0, asset_id="v1")], _NOW)
    assert tasks == []


def test_asset_without_anchor_is_skipped() -> None:
    orphan = _sensor(asset_id="s2", anchor=None)
    tasks = derive_service_tasks([orphan], [_battery_obs(1.0, asset_id="s2")], _NOW)
    assert tasks == []


def test_asset_type_override_layers_on_base_policy() -> None:
    policy = MonitoringPolicySpec(
        assetTypeOverrides={
            "WEATHER_STATION": MonitoringPolicyOverride(batteryLowThresholdPct=30.0)
        }
    )
    weather = Asset(
        asset_id="w1",
        asset_type="WEATHER_STATION",
        roles=["stationary-equipment"],
        mobility=AssetMobility.STATIONARY.value,
        home_depot_ref="field_2",
    )
    probe = _sensor()  # SOIL_MOISTURE_PROBE keeps the base 20.0 threshold
    obs = [_battery_obs(25.0, asset_id="w1"), _battery_obs(25.0, asset_id="s1")]
    tasks = derive_service_tasks([weather, probe], obs, _NOW, policy)
    assert [t.task_id for t in tasks] == ["service-w1"]


def test_instance_override_layers_on_type_override() -> None:
    """A single critical station tightens its class threshold by asset id."""
    policy = MonitoringPolicySpec(
        assetTypeOverrides={
            "WEATHER_STATION": MonitoringPolicyOverride(batteryLowThresholdPct=10.0)
        },
        assetOverrides={
            "w-critical": MonitoringPolicyOverride(batteryLowThresholdPct=40.0)
        },
    )

    def station(asset_id: str) -> Asset:
        return Asset(
            asset_id=asset_id,
            asset_type="WEATHER_STATION",
            roles=["stationary-equipment"],
            mobility=AssetMobility.STATIONARY.value,
            home_depot_ref="field_2",
        )

    # 25% battery: below the critical station's 40% override, above the
    # type-level 10% threshold its sibling keeps.
    obs = [
        _battery_obs(25.0, asset_id="w-critical"),
        _battery_obs(25.0, asset_id="w-normal"),
    ]
    tasks = derive_service_tasks(
        [station("w-critical"), station("w-normal")], obs, _NOW, policy
    )
    assert [t.task_id for t in tasks] == ["service-w-critical"]


def test_instance_override_inherits_unset_fields_from_type_override() -> None:
    policy = MonitoringPolicySpec(
        assetTypeOverrides={
            "WEATHER_STATION": MonitoringPolicyOverride(servicePriorityClass=1)
        },
        assetOverrides={
            "w1": MonitoringPolicyOverride(batteryLowThresholdPct=40.0)
        },
    )
    effective = policy.for_asset("WEATHER_STATION", "w1")
    assert effective.batteryLowThresholdPct == 40.0
    assert effective.servicePriorityClass == 1


def _maintained(asset: Asset, last_service: datetime, interval_days: float) -> Asset:
    return asset.model_copy(
        update={
            "capabilities": [
                Capability(
                    capability_id="lastServiceAt",
                    semantic_term="urn:xopt:maintenance:last-service-at",
                    value=last_service.isoformat(),
                ),
                Capability(
                    capability_id="serviceIntervalDays",
                    semantic_term="urn:xopt:maintenance:service-interval",
                    value=interval_days,
                    canonical_unit="d",
                ),
            ]
        }
    )


def test_composite_health_combines_subcritical_signals() -> None:
    # Battery 22 (above the 20 threshold), unknown health, service due in one
    # day: no individual rule fires, but the weighted score falls below 0.35.
    asset = _maintained(_sensor(), _NOW - timedelta(days=179), 180.0)
    obs = [
        _battery_obs(22.0),
        Observation(
            observation_id="o-h",
            entity_ref="s1",
            metric=METRIC_HEALTH_STATUS,
            state_value="unknown",
            observed_at=_NOW,
        ),
    ]
    tasks = derive_service_tasks([asset], obs, _NOW)
    assert len(tasks) == 1
    assert "composite-health:" in tasks[0].source_ref


def test_composite_needs_at_least_two_signals() -> None:
    # Battery 22 alone: subcritical for the individual rule, and a single
    # signal must not produce a composite task.
    tasks = derive_service_tasks([_sensor()], [_battery_obs(22.0)], _NOW)
    assert tasks == []


def test_composite_weights_are_profile_tunable() -> None:
    """The same signals score healthy when the profile weighs health only:
    the composite tradeoff belongs to the domain, not the engine constants."""
    asset = _maintained(_sensor(), _NOW - timedelta(days=179), 180.0)
    obs = [
        _battery_obs(22.0),
        Observation(
            observation_id="o-h",
            entity_ref="s1",
            metric=METRIC_HEALTH_STATUS,
            state_value="unknown",
            observed_at=_NOW,
        ),
    ]
    assert len(derive_service_tasks([asset], obs, _NOW)) == 1
    health_only = MonitoringPolicySpec(
        compositeWeightBattery=0.0, compositeWeightService=0.0
    )
    assert derive_service_tasks([asset], obs, _NOW, health_only) == []


def test_composite_battery_headroom_is_profile_tunable() -> None:
    """A wider headroom shrinks the battery subscore until the composite
    fires for readings the default policy scores healthy."""
    asset = _maintained(_sensor(), _NOW - timedelta(days=179), 180.0)
    obs = [
        _battery_obs(50.0),
        Observation(
            observation_id="o-h",
            entity_ref="s1",
            metric=METRIC_HEALTH_STATUS,
            state_value="unknown",
            observed_at=_NOW,
        ),
    ]
    assert derive_service_tasks([asset], obs, _NOW) == []
    wide_headroom = MonitoringPolicySpec(compositeBatteryHeadroomPct=300.0)
    tasks = derive_service_tasks([asset], obs, _NOW, wide_headroom)
    assert len(tasks) == 1
    assert "composite-health:" in tasks[0].source_ref


def test_profile_policy_overrides_threshold() -> None:
    policy = MonitoringPolicySpec(batteryLowThresholdPct=50.0)
    obs = [_battery_obs(40.0)]
    assert derive_service_tasks([_sensor()], obs, _NOW) == []
    tasks = derive_service_tasks([_sensor()], obs, _NOW, policy)
    assert len(tasks) == 1
    assert "battery-low:40.0pct" in tasks[0].source_ref


def test_drain_trend_derives_predictive_service_task() -> None:
    # 40 -> 30 over two days projects 30 - 5*3 = 15 <= threshold within horizon.
    obs = [
        _battery_obs(40.0, at=_NOW - timedelta(days=2)),
        _battery_obs(30.0, at=_NOW),
    ]
    tasks = derive_service_tasks([_sensor()], obs, _NOW)
    assert len(tasks) == 1
    assert "battery-forecast:15.0pct-in-3d" in tasks[0].source_ref


def test_stable_battery_yields_no_forecast_task() -> None:
    obs = [
        _battery_obs(80.0, at=_NOW - timedelta(days=2)),
        _battery_obs(79.5, at=_NOW),
    ]
    assert derive_service_tasks([_sensor()], obs, _NOW) == []

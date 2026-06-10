"""Corrective rescheduling: asset-loss release, withdrawal, escalation records."""

from datetime import datetime, timezone

from fl_op.adapters.rolling.corrective import (
    carried_asset_loss_actions,
    escalated_service_tasks,
    release_lost_asset_assignments,
    withdrawn_service_actions,
)
from fl_op.canonical.common import TimeInterval, VersionDimensions
from fl_op.canonical.enums import CorrectiveActionType, PlanningMode
from fl_op.canonical.observation import Observation
from fl_op.canonical.plan import Assignment, Plan
from fl_op.canonical.snapshot import PlanningSnapshot
from fl_op.canonical.task import Task
from fl_op.core.constants import METRIC_BATTERY_LEVEL

_TS = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _assignment(task_id: str, asset_ids: list[str]) -> Assignment:
    return Assignment(
        assignment_id=f"as-{task_id}",
        task_id=task_id,
        bundle_id=f"bundle-{task_id}",
        asset_ids=asset_ids,
        planned_start=_TS,
        planned_finish=_TS,
    )


def _snapshot(tasks: list[Task] = None, observations: list[Observation] = None) -> PlanningSnapshot:
    return PlanningSnapshot(
        snapshot_id="snap-1",
        effective_at=_TS,
        generated_at=_TS,
        planning_mode=PlanningMode.ROLLING,
        planning_horizon=TimeInterval(**{"from": _TS}),
        version_dimensions=VersionDimensions(),
        tasks=tasks or [],
        observations=observations or [],
    )


def _plan(assignments: list[Assignment]) -> Plan:
    return Plan(
        plan_id="plan-1",
        revision_id="rev-1",
        origin_plan_id="plan-1",
        planning_mode=PlanningMode.ROLLING,
        snapshot_id="snap-0",
        version_dimensions=VersionDimensions(),
        adapter_id="ortools-rolling",
        adapter_version="0.1.0",
        generated_at=_TS,
        effective_from=_TS,
        assignments=assignments,
    )


def _service_task(asset_id: str, reasons: str) -> Task:
    return Task(
        task_id=f"service-{asset_id}",
        order_id=f"monitoring-{asset_id}",
        operation_type="EQUIPMENT_SERVICE",
        location_ref="field_1",
        source_ref=f"monitoring:{asset_id}:{reasons}",
    )


def test_frozen_assignment_with_lost_asset_is_released() -> None:
    prev = {"order_1": _assignment("order_1", ["vehicle_1", "implement_1"])}
    kept, actions = release_lost_asset_assignments(
        frozen_ids={"order_1"},
        previous_by_task=prev,
        available_asset_ids={"implement_1"},
        current_task_ids={"order_1"},
    )
    assert kept == set()
    assert len(actions) == 1
    assert actions[0].action == CorrectiveActionType.REASSIGNED_AFTER_ASSET_LOSS
    assert actions[0].evidence["lost_assets"] == ["vehicle_1"]


def test_frozen_assignment_with_live_assets_stays_frozen() -> None:
    prev = {"order_1": _assignment("order_1", ["vehicle_1"])}
    kept, actions = release_lost_asset_assignments(
        frozen_ids={"order_1"},
        previous_by_task=prev,
        available_asset_ids={"vehicle_1"},
        current_task_ids={"order_1"},
    )
    assert kept == {"order_1"}
    assert actions == []


def test_carried_assignment_with_lost_asset_is_recorded() -> None:
    assignments = [_assignment("order_2", ["vehicle_9"])]
    actions = carried_asset_loss_actions(
        previous_assignments=assignments,
        frozen_ids=set(),
        current_task_ids={"order_2"},
        available_asset_ids=set(),
    )
    assert len(actions) == 1
    assert actions[0].task_id == "order_2"


def test_withdrawn_service_task_records_derivation_and_evidence() -> None:
    previous_plan = _plan([_assignment("service-sensor_1", ["vehicle_1"])])
    reasons = {"service-sensor_1": "monitoring:sensor_1:battery-low:15.0pct"}
    snapshot = _snapshot(
        observations=[
            Observation(
                observation_id="o-1",
                entity_ref="sensor_1",
                metric=METRIC_BATTERY_LEVEL,
                value=85.0,
                observed_at=_TS,
            )
        ]
    )
    actions = withdrawn_service_actions(previous_plan, set(), snapshot, reasons)
    assert len(actions) == 1
    action = actions[0]
    assert action.action == CorrectiveActionType.SERVICE_WITHDRAWN
    assert "battery-low:15.0pct" in action.detail
    assert action.evidence["battery_level_pct"] == 85.0


def test_newly_escalated_service_task_is_forced_to_resolve() -> None:
    task = _service_task("sensor_1", "escalated:battery-critical:3.0pct")
    snapshot = _snapshot(tasks=[task])
    reasons = {"service-sensor_1": "monitoring:sensor_1:battery-forecast:15.0pct-in-3d"}
    prev = {"service-sensor_1": _assignment("service-sensor_1", ["vehicle_1"])}
    force_resolve, actions = escalated_service_tasks(snapshot, reasons, prev)
    assert force_resolve == {"service-sensor_1"}
    assert len(actions) == 1
    assert actions[0].action == CorrectiveActionType.SERVICE_ESCALATED
    assert "was: monitoring:sensor_1:battery-forecast" in actions[0].detail


def test_already_escalated_task_yields_no_new_action() -> None:
    task = _service_task("sensor_1", "escalated:health:failed")
    snapshot = _snapshot(tasks=[task])
    reasons = {"service-sensor_1": "monitoring:sensor_1:escalated:health:failed"}
    force_resolve, actions = escalated_service_tasks(snapshot, reasons, {})
    assert force_resolve == set()
    assert actions == []
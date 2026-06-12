"""Material reservations across rolling revisions: carry-forward semantics.

Re-solved tasks get fresh reservations from the chain's material charging;
frozen/carried tasks re-publish their previous revision's reservations so
every revision's reservation list is self-contained.
"""

from datetime import datetime, timezone

from fl_op.adapters.rolling.compiler import compile_rolling_state
from fl_op.adapters.rolling.normalizer import normalize_rolling_result
from fl_op.canonical.asset import Asset
from fl_op.canonical.common import TimeInterval, VersionDimensions
from fl_op.canonical.enums import PlanningMode, ReservationStatus
from fl_op.canonical.plan import Assignment, MaterialReservation, Plan
from fl_op.canonical.snapshot import PlanningSnapshot
from fl_op.canonical.task import Task

_TS = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _assignment(task_id: str, asset_ids: list[str]) -> Assignment:
    return Assignment(
        assignment_id=f"as-{task_id}",
        task_id=task_id,
        bundle_id=f"bundle-{task_id}",
        asset_ids=asset_ids,
        material_reservation_refs=[f"res-{task_id}"],
        planned_start=_TS,
        planned_finish=_TS,
    )


def _reservation(task_id: str) -> MaterialReservation:
    return MaterialReservation(
        reservation_id=f"res-{task_id}",
        task_id=task_id,
        material_type="fertilizer",
        inventory_location_ref="depot_1",
        quantity=500.0,
        canonical_unit="kg",
        status=ReservationStatus.CONFIRMED,
    )


def _snapshot(tasks: list[Task], assets: list[Asset]) -> PlanningSnapshot:
    return PlanningSnapshot(
        snapshot_id="snap-1",
        effective_at=_TS,
        generated_at=_TS,
        planning_mode=PlanningMode.ROLLING,
        planning_horizon=TimeInterval(**{"from": _TS}),
        version_dimensions=VersionDimensions(),
        tasks=tasks,
        assets=assets,
    )


def _previous_plan() -> Plan:
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
        assignments=[_assignment("order_1", ["vehicle_1"])],
        material_reservations=[_reservation("order_1"), _reservation("order_gone")],
    )


def test_preserved_task_reservation_carries_into_next_revision() -> None:
    """The frozen task's reservation is re-published; the disappeared task's
    reservation is not."""
    task = Task(
        task_id="order_1",
        order_id="c1",
        operation_type="SPRAYING",
        location_ref="f1",
        status="started",
    )
    vehicle = Asset(
        asset_id="vehicle_1", asset_type="TRACTOR", roles=["mobile-prime-mover"]
    )
    snapshot = _snapshot([task], [vehicle])

    result = compile_rolling_state(
        snapshot, {"previous_plan": _previous_plan(), "now": _TS}
    )
    assert [r.task_id for r in result.carried_reservations] == ["order_1"]

    plan = normalize_rolling_result(result, snapshot)
    assert [r.task_id for r in plan.material_reservations] == ["order_1"]
    frozen = next(a for a in plan.assignments if a.task_id == "order_1")
    assert frozen.material_reservation_refs == ["res-order_1"]

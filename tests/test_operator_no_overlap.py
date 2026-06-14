"""Operator no-overlap (the operator time dimension) in the routing model.

A cluster can run several prime+related pairs in parallel, but a single
certified operator can only run one task at a time. When two tasks resolve to
the same operator their execution intervals must not overlap, even on different
vehicles. The two tests share one narrow window and two vehicles and vary only
the operator mapping: with a shared operator the tasks serialize and the window
cannot hold both in series, so one is dropped; with distinct operators the same
tasks run in parallel and both dispatch. The contrast isolates the operator
dimension as the sole cause of the drop.
"""

from datetime import datetime, timezone

from fl_op.solver.cluster_solver import solve_cluster
from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow, SiteRow, TaskRow

_SYNTHETIC_NOW = datetime(2027, 6, 1, 6, 0, tzinfo=timezone.utc)
# One two-hour window: wide enough for a single 90-minute task, not two in series.
_WINDOW = "2027-06-01T06:00:00+00:00/2027-06-01T08:00:00+00:00"
_SERVICE_MIN = 90.0


def _order(task_id: str) -> TaskRow:
    return TaskRow.from_canonical_dict({
        "task_id": task_id, "location_ref": "f0", "operation_type": "SPRAYING",
        "area": "10", "deadline": "2027-06-02T00:00:00+00:00",
        "penalty_per_day": "100", "status": "pending",
        "revenue": "2000", "order_ref": "c0",
        "service_duration_min": _SERVICE_MIN, "time_windows": [_WINDOW],
    })


def _vehicle(vid: str) -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict({
        "asset_id": vid, "asset_type": "TRACTOR", "rated_power": "150",
        "fuel_tank_volume": "400", "fuel_consumption_rate": "18",
        "lat": "48.5", "lon": "32.0", "home_depot_ref": "d0", "travel_speed": "15",
    })


def _implement(iid: str) -> RelatedRow:
    return RelatedRow.from_canonical_dict({
        "asset_id": iid, "asset_type": "SPRAYER",
        "compatible_operations": "['SPRAYING']", "required_power": "100",
        "working_width": "24", "min_speed": "5", "max_speed": "12",
        "material_capacity": "500", "home_depot_ref": "d0",
    })


def _field() -> SiteRow:
    return SiteRow.from_canonical_dict(
        {"location_id": "f0", "lat": "48.5", "lon": "32.0", "area": "10"})


def _depot() -> DepotRow:
    return DepotRow.from_canonical_dict(
        {"location_id": "d0", "lat": "48.5", "lon": "32.0"})


def _cluster(task_operators: dict[str, str]) -> dict:
    return {
        "cluster_id": "cl0", "depot_ref": "d0", "task_ids": ["o0", "o1"],
        "allocated_prime_related": {"v0": ["i0"], "v1": ["i1"]},
        "total_penalty_per_day": 200.0,
        "task_operators": task_operators,
    }


def _solve(task_operators: dict[str, str]):
    now_epoch = int(_SYNTHETIC_NOW.timestamp())
    return solve_cluster(
        _cluster(task_operators),
        [_order("o0"), _order("o1")],
        [_vehicle("v0"), _vehicle("v1")],
        [_implement("i0"), _implement("i1")],
        [_field()], [_depot()], {},
        {"v0": 0, "v1": 1}, {"i0": 0, "i1": 1},
        None, None, None, now_epoch,
    )


def test_shared_operator_serializes_and_drops_second() -> None:
    """Two 90-minute tasks on one operator cannot both fit a two-hour window."""
    dispatch, _ = _solve({"o0": "op0", "o1": "op0"})
    assert len(dispatch) == 1


def test_distinct_operators_run_in_parallel() -> None:
    """The same two tasks on different operators both dispatch in parallel."""
    dispatch, _ = _solve({"o0": "op0", "o1": "op1"})
    assert len(dispatch) == 2

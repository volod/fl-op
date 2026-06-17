"""In-model held-operator breaks.

A held operator (carried/frozen on another assignment of a rolling plan) carries
its busy calendar into an incremental re-solve. The routing model now blocks that
calendar exactly -- each of the operator's tasks must run in a real gap -- the
same in-model time blocking prime movers and implements already get as vehicle
breaks, rather than relying on hold-aware allocation scoring alone. The tests
vary only the held window and isolate it as the cause: a window covering the
task's only opportunity drops it; a partial hold shifts the start into the gap.
"""

from datetime import datetime, timezone

from fl_op.solver.cluster_solver import solve_cluster
from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow, SiteRow, TaskRow

_NOW = datetime(2027, 6, 1, 6, 0, tzinfo=timezone.utc)
_NOW_EPOCH = int(_NOW.timestamp())
# A two-hour window, wide enough for a single 90-minute task.
_WINDOW = "2027-06-01T06:00:00+00:00/2027-06-01T08:00:00+00:00"
_SERVICE_MIN = 90.0


def _order(task_id: str, window: str = _WINDOW) -> TaskRow:
    return TaskRow.from_canonical_dict({
        "task_id": task_id, "location_ref": "f0", "operation_type": "SPRAYING",
        "area": "10", "deadline": "2027-06-02T00:00:00+00:00",
        "penalty_per_day": "100", "status": "pending", "revenue": "2000",
        "order_ref": "c0", "service_duration_min": _SERVICE_MIN,
        "time_windows": [window],
    })


def _vehicle() -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict({
        "asset_id": "v0", "asset_type": "TRACTOR", "rated_power": "150",
        "fuel_tank_volume": "400", "fuel_consumption_rate": "18",
        "lat": "48.5", "lon": "32.0", "home_depot_ref": "d0", "travel_speed": "15",
    })


def _implement() -> RelatedRow:
    return RelatedRow.from_canonical_dict({
        "asset_id": "i0", "asset_type": "SPRAYER",
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


def _cluster() -> dict:
    return {
        "cluster_id": "cl0", "depot_ref": "d0", "task_ids": ["o0"],
        "allocated_prime_related": {"v0": ["i0"]},
        "total_penalty_per_day": 100.0, "operator_ref": "op0",
    }


def _solve(held_windows, order=None):
    return solve_cluster(
        _cluster(), [order or _order("o0")], [_vehicle()], [_implement()],
        [_field()], [_depot()], {}, {"v0": 0}, {"i0": 0},
        held_windows, None, None, _NOW_EPOCH,
    )


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def test_unheld_operator_serves_the_task():
    """Baseline: with no hold the operator's single task dispatches."""
    dispatch, _ = _solve(None)
    assert len(dispatch) == 1


def test_held_operator_blocks_task_across_its_only_window():
    """An operator busy for the task's whole window cannot serve it -> dropped."""
    held = {"op0": [(_epoch("2027-06-01T06:00:00+00:00"),
                     _epoch("2027-06-01T08:00:00+00:00"))]}
    dispatch, infeasible = _solve(held)
    assert dispatch == []
    assert len(infeasible) == 1


def test_held_operator_only_blocks_its_own_tasks():
    """A hold on a different operator leaves op0's task untouched."""
    held = {"other-op": [(_epoch("2027-06-01T06:00:00+00:00"),
                          _epoch("2027-06-01T08:00:00+00:00"))]}
    dispatch, _ = _solve(held)
    assert len(dispatch) == 1


def test_held_operator_shifts_start_into_the_gap():
    """A partial hold pushes the start to when the operator frees up."""
    wide = "2027-06-01T06:00:00+00:00/2027-06-01T12:00:00+00:00"
    free_at = "2027-06-01T09:00:00+00:00"
    held = {"op0": [(_epoch("2027-06-01T06:00:00+00:00"), _epoch(free_at))]}
    dispatch, _ = _solve(held, order=_order("o0", window=wide))
    assert len(dispatch) == 1
    assert _epoch(dispatch[0]["scheduled_start"]) >= _epoch(free_at)

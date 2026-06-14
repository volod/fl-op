"""Finish-within-window enforcement in the routing model.

A task's whole execution interval [start, start + service] must land inside one
declared workable window, not merely start inside it. When the service duration
exceeds the only window the task cannot be scheduled (it is dropped); when the
window is wide enough the task dispatches and finishes before the window closes.
The two tests share one window and vary only the service duration, isolating the
finish constraint as the cause of the drop.
"""

from datetime import datetime, timezone

from fl_op.solver.cluster_solver import solve_cluster
from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow, SiteRow, TaskRow

_SYNTHETIC_NOW = datetime(2027, 6, 1, 6, 0, tzinfo=timezone.utc)
# A single two-hour workable window starting at the synthetic origin.
_WINDOW = "2027-06-01T06:00:00+00:00/2027-06-01T08:00:00+00:00"


def _order(service_min: float) -> TaskRow:
    return TaskRow.from_canonical_dict({
        "task_id": "o0", "location_ref": "f0", "operation_type": "SPRAYING",
        "area": "10", "deadline": "2027-06-02T00:00:00+00:00",
        "penalty_per_day": "100", "status": "pending",
        "revenue": "2000", "order_ref": "c0",
        "service_duration_min": service_min, "time_windows": [_WINDOW],
    })


def _vehicle(vid: str = "v0") -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict({
        "asset_id": vid, "asset_type": "TRACTOR", "rated_power": "150",
        "fuel_tank_volume": "400", "fuel_consumption_rate": "18",
        "lat": "48.5", "lon": "32.0", "home_depot_ref": "d0", "travel_speed": "15",
    })


def _implement(iid: str = "i0") -> RelatedRow:
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


def _cluster() -> dict:
    return {
        "cluster_id": "cl0", "depot_ref": "d0", "task_ids": ["o0"],
        "allocated_prime_related": {"v0": ["i0"]}, "total_penalty_per_day": 100.0,
    }


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def _solve(order: TaskRow):
    now_epoch = int(_SYNTHETIC_NOW.timestamp())
    return solve_cluster(
        _cluster(), [order], [_vehicle()], [_implement()],
        [_field()], [_depot()], {}, {"v0": 0}, {"i0": 0},
        None, None, None, now_epoch,
    )


def test_task_dropped_when_service_exceeds_only_window() -> None:
    """Six hours of work cannot fit a two-hour window, so the task is dropped."""
    dispatch, _ = _solve(_order(service_min=360))
    assert dispatch == []


def test_task_dispatched_and_finishes_inside_window() -> None:
    """Thirty minutes of work fits and both start and finish stay in the window."""
    dispatch, _ = _solve(_order(service_min=30))
    assert len(dispatch) == 1
    start = _epoch(dispatch[0]["scheduled_start"])
    end = _epoch(dispatch[0]["scheduled_end"])
    assert start >= _epoch("2027-06-01T06:00:00+00:00")
    assert end <= _epoch("2027-06-01T08:00:00+00:00")

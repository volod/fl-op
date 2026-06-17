"""Sequential operator-sharing solve (OPERATOR_SHARING_SEQUENTIAL).

When a scarce backup operator is shared across clusters with overlapping demand
windows, those clusters must solve sequentially so the operator's committed
intervals in one feed the next as in-model breaks -- never double-booked. The
flag is off by default; these tests drive it on and contrast with the parallel
default. Both clusters share backup operator ``op_b`` for a SPRAYING task within
the same wide window, leaving room for the operator to do both in series.
"""

from datetime import datetime, timezone

from fl_op.core import constants
from fl_op.solver.cluster_pool import pool_solve
from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow, SiteRow, TaskRow

_NOW = int(datetime(2027, 6, 1, 6, 0, tzinfo=timezone.utc).timestamp())
# A six-hour window: room for two 90-minute tasks on one operator in series.
_WINDOW = "2027-06-01T06:00:00+00:00/2027-06-01T12:00:00+00:00"
_SERVICE_MIN = 90.0


def _order(task_id: str) -> TaskRow:
    return TaskRow.from_canonical_dict({
        "task_id": task_id, "location_ref": "f0", "operation_type": "SPRAYING",
        "area": "10", "deadline": "2027-06-02T00:00:00+00:00",
        "penalty_per_day": "100", "status": "pending", "revenue": "2000",
        "order_ref": "c0", "service_duration_min": _SERVICE_MIN,
        "time_windows": [_WINDOW],
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


def _cluster(cluster_id, task_id, vehicle_id, implement_id, penalty, shared,
             operator="op_b"):
    cd = {
        "cluster_id": cluster_id, "depot_ref": "d0", "task_ids": [task_id],
        "allocated_prime_related": {vehicle_id: [implement_id]},
        "total_penalty_per_day": penalty,
        # The task is served by the named scarce backup operator.
        "operator_ref": "prime_op", "task_operators": {task_id: operator},
    }
    if shared:
        cd["shared_backup_operators"] = [operator]
    return cd


def _solve(shared: bool):
    clusters = [
        _cluster("cA", "o0", "v0", "i0", 200.0, shared),
        _cluster("cB", "o1", "v1", "i1", 100.0, shared),
    ]
    return pool_solve(
        clusters,
        [_order("o0"), _order("o1")],
        [_vehicle("v0"), _vehicle("v1")],
        [_implement("i0"), _implement("i1")],
        [_field()], [_depot()], {},
        {"v0": 0, "v1": 1}, {"i0": 0, "i1": 1},
        now_epoch=_NOW,
    )


def _operator_intervals(dispatch, operator="op_b"):
    intervals = []
    for dp in dispatch:
        if dp.get("operator_asset_id") == operator:
            start = int(datetime.fromisoformat(dp["scheduled_start"]).timestamp())
            end = int(datetime.fromisoformat(dp["scheduled_end"]).timestamp())
            intervals.append((start, end))
    return sorted(intervals)


def _overlap(intervals) -> bool:
    for i in range(len(intervals)):
        for j in range(i + 1, len(intervals)):
            if intervals[i][0] < intervals[j][1] and intervals[j][0] < intervals[i][1]:
                return True
    return False


def test_sequential_sharing_serves_both_without_double_booking(monkeypatch):
    monkeypatch.setattr(constants, "OPERATOR_SHARING_SEQUENTIAL", True)
    dispatch, infeasible, telemetry = _solve(shared=True)
    assert [dp["task_id"] for dp in dispatch].count("o0") == 1
    assert len(dispatch) == 2  # both tasks served
    intervals = _operator_intervals(dispatch)
    assert len(intervals) == 2
    # The shared operator runs the two tasks back to back, never overlapping.
    assert not _overlap(intervals)


def test_parallel_default_can_double_book_the_shared_operator(monkeypatch):
    # Flag off: the clusters solve independently in parallel, so each places its
    # task at the window start and the shared operator is double-booked -- the
    # very conflict the sequential path (and the conservative allocation that
    # forbids overlapping shares) exists to prevent.
    monkeypatch.setattr(constants, "OPERATOR_SHARING_SEQUENTIAL", False)
    dispatch, _, _ = _solve(shared=False)
    assert len(dispatch) == 2
    assert _overlap(_operator_intervals(dispatch))


def test_independent_clusters_unaffected_by_flag(monkeypatch):
    # Clusters that do not share an operator are never grouped, so they keep
    # solving in the parallel pool even with the flag on.
    monkeypatch.setattr(constants, "OPERATOR_SHARING_SEQUENTIAL", True)
    clusters = [
        _cluster("cA", "o0", "v0", "i0", 200.0, shared=False),
        _cluster("cB", "o1", "v1", "i1", 100.0, shared=False),
    ]
    # Distinct operators -> no sharing stamp -> independent.
    clusters[1]["task_operators"] = {"o1": "op_other"}
    dispatch, _, _ = pool_solve(
        clusters,
        [_order("o0"), _order("o1")],
        [_vehicle("v0"), _vehicle("v1")],
        [_implement("i0"), _implement("i1")],
        [_field()], [_depot()], {},
        {"v0": 0, "v1": 1}, {"i0": 0, "i1": 1},
        now_epoch=_NOW,
    )
    assert len(dispatch) == 2


def test_group_time_budget_is_value_weighted(monkeypatch):
    monkeypatch.setattr(constants, "OPERATOR_SHARING_SEQUENTIAL", True)
    monkeypatch.setattr(constants, "OPERATOR_SHARING_GROUP_TIME_LIMIT_S", 90)
    _, _, telemetry = _solve(shared=True)
    by_cluster = {t["cluster_id"]: t for t in telemetry}
    # Equal size (1 task, 1 vehicle each), so difficulty cancels and the 90s
    # group budget splits by penalty (cA 200, cB 100) into 60s / 30s.
    assert by_cluster["cA"]["time_limit_s"] == 60
    assert by_cluster["cB"]["time_limit_s"] == 30


def test_group_budget_weights_by_penalty_and_difficulty():
    from fl_op.solver.cluster_pool import _group_budgets

    # Equal penalty, different difficulty: the larger (harder) cluster, where
    # extra search actually helps, gets the larger share of the group budget.
    small = {"cluster_id": "s", "total_penalty_per_day": 100.0,
             "task_ids": ["t1"], "allocated_prime_related": {"v": ["i"]}}
    big = {"cluster_id": "b", "total_penalty_per_day": 100.0,
           "task_ids": ["t1", "t2", "t3", "t4"],
           "allocated_prime_related": {"v": ["i"]}}
    budgets = _group_budgets([big, small], per_cluster_limit=60, group_total_limit=100)
    assert budgets["b"] > budgets["s"]
    assert budgets["b"] + budgets["s"] <= 100
    # No group cap -> every cluster keeps the per-cluster limit.
    assert _group_budgets([big, small], 60, 0) == {"b": 60, "s": 60}


def test_independent_groups_run_in_parallel(monkeypatch):
    # Two independent sharing groups: (cA, cB) share op_b1 and (cC, cD) share
    # op_b2. They run concurrently in the pool, each internally sequential, and
    # neither shared operator is double-booked.
    monkeypatch.setattr(constants, "OPERATOR_SHARING_SEQUENTIAL", True)
    clusters = [
        _cluster("cA", "o0", "v0", "i0", 200.0, True, operator="op_b1"),
        _cluster("cB", "o1", "v1", "i1", 100.0, True, operator="op_b1"),
        _cluster("cC", "o2", "v2", "i2", 200.0, True, operator="op_b2"),
        _cluster("cD", "o3", "v3", "i3", 100.0, True, operator="op_b2"),
    ]
    orders = [_order(f"o{i}") for i in range(4)]
    vehicles = [_vehicle(f"v{i}") for i in range(4)]
    implements = [_implement(f"i{i}") for i in range(4)]
    dispatch, _, _ = pool_solve(
        clusters, orders, vehicles, implements, [_field()], [_depot()], {},
        {f"v{i}": i for i in range(4)}, {f"i{i}": i for i in range(4)},
        now_epoch=_NOW,
    )
    assert len(dispatch) == 4  # both groups fully served
    assert not _overlap(_operator_intervals(dispatch, "op_b1"))
    assert not _overlap(_operator_intervals(dispatch, "op_b2"))

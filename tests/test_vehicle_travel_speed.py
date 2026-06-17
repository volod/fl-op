"""Per-vehicle travel speed in the no-network (haversine) fallback.

Network links carry vehicle-independent declared times, so per-vehicle speed
only differentiates the geometric fallback leg. These tests cover the matrix
mechanism (a faster mover gets proportionally shorter fallback legs, a
default-speed mover is unchanged) and the end-to-end effect under both objectives
(a genuinely faster mover completes earlier and is preferred).
"""

from datetime import datetime, timezone

from fl_op.core.constants import FALLBACK_TRAVEL_SPEED_KMH
from fl_op.solver.cluster_solver import solve_cluster
from fl_op.solver.routing_model import (
    NODE_DEPOT,
    NODE_TASK,
    RoutingNode,
    build_time_matrix,
    build_vehicle_time_matrices,
)
from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow, SiteRow, TaskRow

_NOW = int(datetime(2027, 6, 1, 6, 0, tzinfo=timezone.utc).timestamp())
# A field well away from the depot so the (no-network) travel leg dominates.
_DEPOT_LL = (48.5, 32.0)
_FIELD_LL = (49.5, 33.0)


def _nodes() -> list[RoutingNode]:
    return [
        RoutingNode(NODE_DEPOT, -1, "d0", *_DEPOT_LL),
        RoutingNode(NODE_TASK, 0, "f0", *_FIELD_LL),
    ]


def _vehicle(vid: str, travel_speed: str) -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict({
        "asset_id": vid, "asset_type": "TRACTOR", "rated_power": "150",
        "fuel_tank_volume": "400", "fuel_consumption_rate": "18",
        "lat": "48.5", "lon": "32.0", "home_depot_ref": "d0",
        "travel_speed": travel_speed,
    })


def _rv(vid: str, iid: str, travel_speed: str) -> dict:
    return {"prime": _vehicle(vid, travel_speed), "related": _implement(iid)}


def _implement(iid: str) -> RelatedRow:
    return RelatedRow.from_canonical_dict({
        "asset_id": iid, "asset_type": "SPRAYER",
        "compatible_operations": "['SPRAYING']", "required_power": "100",
        "working_width": "24", "min_speed": "5", "max_speed": "12",
        "material_capacity": "500", "home_depot_ref": "d0",
    })


def _order(oid: str) -> TaskRow:
    return TaskRow.from_canonical_dict({
        "task_id": oid, "location_ref": "f0", "operation_type": "SPRAYING",
        "area": "10", "deadline": "2027-06-10T00:00:00+00:00",
        "penalty_per_day": "100", "status": "pending", "revenue": "2000",
        "order_ref": "c0",
    })


def _field() -> SiteRow:
    return SiteRow.from_canonical_dict(
        {"location_id": "f0", "lat": str(_FIELD_LL[0]), "lon": str(_FIELD_LL[1]),
         "area": "10"})


def _depot() -> DepotRow:
    return DepotRow.from_canonical_dict(
        {"location_id": "d0", "lat": str(_DEPOT_LL[0]), "lon": str(_DEPOT_LL[1])})


# --- matrix mechanism -----------------------------------------------------


def test_faster_mover_shrinks_fallback_legs_proportionally():
    nodes = _nodes()
    matrices = build_vehicle_time_matrices(
        nodes, [_rv("v_slow", "i0", "15"), _rv("v_fast", "i1", "45")], None
    )
    slow_leg = matrices[0][0][1]
    fast_leg = matrices[1][0][1]
    assert fast_leg < slow_leg
    # Three times the speed -> ~one third the time.
    assert round(slow_leg / fast_leg, 1) == 3.0


def test_default_speed_matches_engine_fallback():
    nodes = _nodes()
    # A mover declaring the engine fallback speed must reproduce the plain
    # engine-default matrix exactly (backward compatible).
    engine = build_time_matrix(nodes, None, None)
    default_speed = build_vehicle_time_matrices(
        nodes, [_rv("v0", "i0", str(FALLBACK_TRAVEL_SPEED_KMH))], None
    )[0]
    assert default_speed == engine


# --- end-to-end completion-time effect ------------------------------------


def _cluster(vehicle_id: str, implement_id: str) -> dict:
    return {
        "cluster_id": "cl0", "depot_ref": "d0", "task_ids": ["o0"],
        "allocated_prime_related": {vehicle_id: [implement_id]},
        "total_penalty_per_day": 100.0,
    }


def _solve_single(vehicle_id: str, travel_speed: str):
    return solve_cluster(
        _cluster(vehicle_id, "i0"),
        [_order("o0")], [_vehicle(vehicle_id, travel_speed)], [_implement("i0")],
        [_field()], [_depot()], {}, {vehicle_id: 0}, {"i0": 0},
        None, None, None, _NOW, None, None, "time",
    )


def test_faster_mover_completes_earlier():
    slow, _ = _solve_single("v_slow", "10")
    fast, _ = _solve_single("v_fast", "60")
    assert slow and fast
    assert fast[0]["scheduled_end"] < slow[0]["scheduled_end"]


def test_objective_time_prefers_faster_mover():
    cd = {
        "cluster_id": "cl0", "depot_ref": "d0", "task_ids": ["o0"],
        "allocated_prime_related": {"v_slow": ["i0"], "v_fast": ["i1"]},
        "total_penalty_per_day": 100.0,
    }
    dispatch, _ = solve_cluster(
        cd, [_order("o0")],
        [_vehicle("v_slow", "10"), _vehicle("v_fast", "60")],
        [_implement("i0"), _implement("i1")],
        [_field()], [_depot()], {},
        {"v_slow": 0, "v_fast": 1}, {"i0": 0, "i1": 1},
        None, None, None, _NOW, None, None, "time",
    )
    assert dispatch
    assert dispatch[0]["prime_asset_id"] == "v_fast"

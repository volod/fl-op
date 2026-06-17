"""Resource-conflict attribution: primal utilization -> binding resource.

Pure-logic tests for ``build_resource_conflict``/``no_solution_conflict`` plus
real-solve integration tests that exercise the dimension walk in routing.py and
the propagation into the per-task attribution map and revision-diff explanation.
"""

import pytest

from fl_op.adapters.base import build_solver_attribution
from fl_op.core import constants
from fl_op.planning.revision_diff import _solver_explanation
from fl_op.solver.cluster.conflict import (
    BINDING_INFEASIBLE,
    BINDING_SOLVE_BUDGET,
    build_resource_conflict,
    no_solution_conflict,
)
from fl_op.solver.cluster_solver import solve_cluster_instrumented
from fl_op.solver.solve_telemetry import summarize_cluster_telemetry
from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow, SiteRow, TaskRow


# ---------------------------------------------------------------------------
# Pure attribution logic
# ---------------------------------------------------------------------------


def test_no_drops_is_none():
    rc = build_resource_conflict(
        n_unserved=0,
        n_vehicles=2,
        n_vehicles_used=1,
        time_utilization=0.9,
        capacity_utilization={"fert": 0.95},
    )
    assert rc["binding_resource"] == "none"
    # Utilizations are still reported for a fully-served cluster.
    assert rc["time_utilization"] == 0.9
    assert rc["capacity_utilization"] == {"fert": 0.95}


def test_capacity_outranks_saturated_fleet():
    rc = build_resource_conflict(
        n_unserved=2,
        n_vehicles=1,
        n_vehicles_used=1,
        time_utilization=0.01,
        capacity_utilization={"fert": 0.92},
    )
    # A single-vehicle cluster always has fleet=1.0; capacity must still win.
    assert rc["binding_resource"] == "capacity:fert"
    assert rc["binding_utilization"] == 0.92


def test_time_binding():
    rc = build_resource_conflict(
        n_unserved=1,
        n_vehicles=3,
        n_vehicles_used=2,
        time_utilization=0.97,
        capacity_utilization={},
    )
    assert rc["binding_resource"] == "time"


def test_fleet_binding_when_all_used_and_nothing_else_tight():
    rc = build_resource_conflict(
        n_unserved=2,
        n_vehicles=2,
        n_vehicles_used=2,
        time_utilization=0.4,
        capacity_utilization={"fert": 0.8},
    )
    assert rc["binding_resource"] == "fleet"


def test_other_when_a_vehicle_is_idle_and_nothing_tight():
    rc = build_resource_conflict(
        n_unserved=1,
        n_vehicles=3,
        n_vehicles_used=2,
        time_utilization=0.3,
        capacity_utilization={"fert": 0.4},
    )
    assert rc["binding_resource"] == "other"


def test_highest_capacity_material_is_chosen():
    rc = build_resource_conflict(
        n_unserved=1,
        n_vehicles=1,
        n_vehicles_used=1,
        time_utilization=0.0,
        capacity_utilization={"seed": 0.86, "fert": 0.99},
    )
    assert rc["binding_resource"] == "capacity:fert"


def test_no_solution_conflict_distinguishes_budget_from_infeasible():
    assert (
        no_solution_conflict(hit_time_limit=True, n_unserved=3)["binding_resource"]
        == BINDING_SOLVE_BUDGET
    )
    assert (
        no_solution_conflict(hit_time_limit=False, n_unserved=3)["binding_resource"]
        == BINDING_INFEASIBLE
    )


# ---------------------------------------------------------------------------
# Propagation into attribution and explanation
# ---------------------------------------------------------------------------


def test_build_solver_attribution_carries_binding_resource():
    dispatch = [{"task_id": "o0", "cluster_id": "c0", "estimated_margin_eur": 10.0}]
    infeasible = [
        {"task_id": "o1", "cluster_id": "c0", "reason_code": "OPTIMIZATION_TRADEOFF",
         "detail": "dropped"},
    ]
    telemetry = [
        {
            "cluster_id": "c0",
            "status": "solved",
            "resource_conflict": {
                "binding_resource": "capacity:fert",
                "binding_utilization": 0.93,
            },
        }
    ]
    assignments, unassigned = build_solver_attribution(dispatch, infeasible, telemetry)
    assert assignments["o0"]["binding_resource"] == "capacity:fert"
    assert assignments["o0"]["resource_conflict"]["binding_utilization"] == 0.93
    assert unassigned["o1"]["binding_resource"] == "capacity:fert"


def test_solver_explanation_mentions_binding_resource():
    new_attr = {
        "cluster_id": "c0",
        "binding_resource": "capacity:fert",
        "resource_conflict": {"binding_resource": "capacity:fert",
                              "binding_utilization": 0.93},
    }
    text = _solver_explanation("o1", {}, None, new_attr, {}, "observation.recorded")
    assert "binding resource capacity:fert at 93%" in text


def test_summary_tallies_binding_resources():
    records = [
        {"status": "solved", "resource_conflict": {"binding_resource": "capacity:fert"}},
        {"status": "solved", "resource_conflict": {"binding_resource": "capacity:fert"}},
        {"status": "solved", "resource_conflict": {"binding_resource": "none"}},
    ]
    summary = summarize_cluster_telemetry(records)
    # "none" clusters (nothing dropped) are not tallied as a binding resource.
    assert summary["binding_resources"] == {"capacity:fert": 2}


# ---------------------------------------------------------------------------
# Real-solve integration
# ---------------------------------------------------------------------------


def _cluster(task_ids, allocated):
    return {
        "cluster_id": "cl0", "depot_ref": "d0", "task_ids": task_ids,
        "allocated_prime_related": allocated, "total_penalty_per_day": 100.0,
    }


def _order(oid, fid, load_demand="0", material=""):
    return TaskRow.from_canonical_dict({
        "task_id": oid, "location_ref": fid, "operation_type": "SPRAYING",
        "area": "10", "deadline": "2027-12-01T00:00:00+00:00",
        "penalty_per_day": "100", "status": "pending", "revenue": "2000",
        "order_ref": "c0", "load_demand": load_demand, "load_material": material,
    })


def _vehicle(vid, load_capacity="500"):
    return PrimeMoverRow.from_canonical_dict({
        "asset_id": vid, "asset_type": "TRACTOR", "rated_power": "150",
        "fuel_tank_volume": "400", "fuel_consumption_rate": "18",
        "lat": "48.5", "lon": "32.0", "home_depot_ref": "d0",
        "travel_speed": "15", "load_capacity": load_capacity,
    })


def _implement(iid):
    return RelatedRow.from_canonical_dict({
        "asset_id": iid, "asset_type": "SPRAYER",
        "compatible_operations": "['SPRAYING']", "required_power": "100",
        "working_width": "24", "min_speed": "5", "max_speed": "12",
        "material_capacity": "500", "home_depot_ref": "d0",
    })


def _field(fid, lat=48.5):
    return SiteRow.from_canonical_dict(
        {"location_id": fid, "lat": str(lat), "lon": "32.0", "area": "10"})


def _depot():
    return DepotRow.from_canonical_dict(
        {"location_id": "d0", "lat": "48.5", "lon": "32.0"})


def test_served_cluster_reports_none_binding_with_real_utilizations():
    cd = _cluster(["o0"], {"v0": ["i0"]})
    _, infeasible, telemetry = solve_cluster_instrumented(
        cd, [_order("o0", "f0")], [_vehicle("v0")], [_implement("i0")],
        [_field("f0")], [_depot()], {}, {"v0": 0}, {"i0": 0},
    )
    rc = telemetry["resource_conflict"]
    assert infeasible == []
    assert rc["binding_resource"] == "none"
    # A real route was walked: the single vehicle is used, time is measured.
    assert rc["vehicle_utilization"] == 1.0
    assert 0.0 <= rc["time_utilization"] <= 1.0


def test_capacity_bound_cluster_attributes_to_capacity(monkeypatch):
    # No reloads + each order at 90% of capacity => only one fits per route, so
    # the load dimension is the binding constraint behind the dropped tasks.
    monkeypatch.setattr(constants, "DEPOT_RELOAD_ENABLED", False)
    cd = _cluster(["o0", "o1", "o2"], {"v0": ["i0"]})
    orders = [
        _order("o0", "f0", "450", "fert"),
        _order("o1", "f1", "450", "fert"),
        _order("o2", "f2", "450", "fert"),
    ]
    fields = [_field("f0"), _field("f1", 48.51), _field("f2", 48.52)]
    dispatch, infeasible, telemetry = solve_cluster_instrumented(
        cd, orders, [_vehicle("v0")], [_implement("i0")], fields, [_depot()],
        {}, {"v0": 0}, {"i0": 0},
    )
    rc = telemetry["resource_conflict"]
    assert len(infeasible) >= 1  # capacity forces drops
    assert rc["binding_resource"] == "capacity:fert"
    assert rc["capacity_utilization"]["fert"] >= 0.85

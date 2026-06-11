"""T16-T23: Cluster solver tests (canonical solver rows).

Tests verify the I/O contract and error-handling guarantees of solve_cluster().
OR-Tools is a required dependency so these tests assume the venv is active.
"""

import dataclasses

import pytest

from fl_op.solver.cluster_solver import solve_cluster, solve_cluster_instrumented
from fl_op.solver.solve_telemetry import (
    STATUS_NO_SOLUTION,
    STATUS_SOLVED,
    summarize_cluster_telemetry,
)
from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow, SiteRow, TaskRow


# ---------------------------------------------------------------------------
# Minimal data builders (canonical solver rows)
# ---------------------------------------------------------------------------


def _cluster(cluster_id="cl0", depot_ref="d0", task_ids=None, allocated=None):
    return {
        "cluster_id": cluster_id,
        "depot_ref": depot_ref,
        "task_ids": task_ids or [],
        "allocated_prime_related": allocated or {},
        "total_penalty_per_day": 100.0,
    }


def _order(oid, fid="f0") -> TaskRow:
    return TaskRow.from_canonical_dict({
        "task_id": oid, "location_ref": fid, "operation_type": "SPRAYING",
        "area": "10", "deadline": "2027-12-01T00:00:00+00:00",
        "penalty_per_day": "100", "status": "pending",
        "revenue": "2000", "order_ref": "c0",
    })


def _vehicle(vid, depot_ref="d0") -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict({
        "asset_id": vid, "asset_type": "TRACTOR", "rated_power": "150",
        "fuel_tank_volume": "400", "fuel_consumption_rate": "18",
        "lat": "48.5", "lon": "32.0",
        "home_depot_ref": depot_ref, "travel_speed": "15",
    })


def _implement(iid, depot_ref="d0") -> RelatedRow:
    return RelatedRow.from_canonical_dict({
        "asset_id": iid, "asset_type": "SPRAYER",
        "compatible_operations": "['SPRAYING']", "required_power": "100",
        "working_width": "24", "min_speed": "5", "max_speed": "12",
        "material_capacity": "500", "home_depot_ref": depot_ref,
    })


def _field(fid, lat=48.5, lon=32.0) -> SiteRow:
    return SiteRow.from_canonical_dict(
        {"location_id": fid, "lat": str(lat), "lon": str(lon), "area": "10"})


def _depot(did="d0", lat=48.5, lon=32.0) -> DepotRow:
    return DepotRow.from_canonical_dict(
        {"location_id": did, "lat": str(lat), "lon": str(lon)})


# ---------------------------------------------------------------------------
# Contract: return type and shape
# ---------------------------------------------------------------------------


class TestReturnTypeContract:
    def test_returns_two_tuple(self):
        result = solve_cluster(_cluster(), [], [], [], [], [], {}, {}, {})
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_both_elements_are_lists(self):
        dispatch, infeasible = solve_cluster(_cluster(), [], [], [], [], [], {}, {}, {})
        assert isinstance(dispatch, list)
        assert isinstance(infeasible, list)

    def test_empty_task_ids_returns_empty_lists(self):
        dispatch, infeasible = solve_cluster(_cluster(task_ids=[]), [], [], [], [], [], {}, {}, {})
        assert dispatch == []
        assert infeasible == []

    def test_never_raises_on_bad_data(self):
        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        try:
            result = solve_cluster(cd, [], [], [], [], [], {}, {"v0": 0}, {"i0": 0})
        except Exception as exc:
            pytest.fail(f"solve_cluster raised unexpectedly: {exc}")
        assert len(result) == 2

    def test_all_task_ids_accounted_for(self):
        cd = _cluster(task_ids=["o0", "o1"], allocated={"v0": ["i0"]})
        orders = [_order("o0", "f0"), _order("o1", "f1")]
        vehicles = [_vehicle("v0")]
        implements = [_implement("i0")]
        fields = [_field("f0"), _field("f1", 48.6, 32.1)]
        depots = [_depot()]
        dispatch, infeasible = solve_cluster(
            cd, orders, vehicles, implements, fields, depots,
            {}, {"v0": 0}, {"i0": 0},
        )
        covered = {d["task_id"] for d in dispatch} | {i["task_id"] for i in infeasible}
        assert covered == {"o0", "o1"}


# ---------------------------------------------------------------------------
# Early-exit infeasibility paths (no OR-Tools solve needed)
# ---------------------------------------------------------------------------


class TestEarlyInfeasibilityPaths:
    def test_no_allocated_resources_marks_infeasible(self):
        cd = _cluster(task_ids=["o0"], allocated={})
        orders = [_order("o0")]
        dispatch, infeasible = solve_cluster(
            cd, orders, [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [_depot()], {}, {"v0": 0}, {"i0": 0},
        )
        assert dispatch == []
        assert len(infeasible) == 1
        assert infeasible[0]["task_id"] == "o0"
        assert infeasible[0]["reason_code"] == "NO_COMPATIBLE_BUNDLE"

    def test_missing_depot_marks_infeasible(self):
        cd = _cluster(depot_ref="ghost", task_ids=["o0"], allocated={"v0": ["i0"]})
        orders = [_order("o0")]
        dispatch, infeasible = solve_cluster(
            cd, orders, [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [],  # depots list empty -> depot not found
            {}, {"v0": 0}, {"i0": 0},
        )
        assert dispatch == []
        assert any(i["reason_code"] == "LOCATION_DATA_INVALID" for i in infeasible)

    def test_order_not_in_data_marks_infeasible(self):
        cd = _cluster(task_ids=["o_missing"], allocated={"v0": ["i0"]})
        dispatch, infeasible = solve_cluster(
            cd, [],  # orders list empty -> o_missing not found
            [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert dispatch == []
        assert any(i["reason_code"] == "UNKNOWN" for i in infeasible)


# ---------------------------------------------------------------------------
# Infeasible item schema
# ---------------------------------------------------------------------------


class TestInfeasibleSchema:
    def test_infeasible_items_have_required_fields(self):
        cd = _cluster(task_ids=["o0"], allocated={})
        orders = [_order("o0")]
        _, infeasible = solve_cluster(cd, orders, [], [], [], [], {}, {}, {})
        for item in infeasible:
            assert "task_id" in item, "infeasible item missing task_id"
            assert "cluster_id" in item, "infeasible item missing cluster_id"
            assert "reason_code" in item, "infeasible item missing reason_code"
            assert "detail" in item, "infeasible item missing detail"

    def test_infeasible_cluster_id_matches(self):
        cd = _cluster(cluster_id="my_cluster", task_ids=["o0"], allocated={})
        _, infeasible = solve_cluster(cd, [_order("o0")], [], [], [], [], {}, {}, {})
        assert all(i["cluster_id"] == "my_cluster" for i in infeasible)


# ---------------------------------------------------------------------------
# Solver integration: single order, single vehicle (fast OR-Tools call)
# ---------------------------------------------------------------------------


class TestSolverIntegration:
    def test_single_order_covered(self):
        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        dispatch, infeasible = solve_cluster(
            cd, [_order("o0")], [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [_depot()], {}, {"v0": 0}, {"i0": 0},
        )
        all_ids = {d["task_id"] for d in dispatch} | {i["task_id"] for i in infeasible}
        assert "o0" in all_ids

    def test_dispatch_items_have_required_fields(self):
        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        dispatch, _ = solve_cluster(
            cd, [_order("o0")], [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [_depot()], {}, {"v0": 0}, {"i0": 0},
        )
        for pkg in dispatch:
            for field in ("dispatch_id", "cluster_id", "prime_asset_id", "related_asset_id",
                          "task_id", "depot_ref", "scheduled_start", "scheduled_end"):
                assert field in pkg, f"dispatch package missing {field}"

    def test_lns_improvement_pass_keeps_result_valid(self, monkeypatch):
        """High-value cluster with LNS enabled: every task stays accounted for."""
        from fl_op.core import constants

        monkeypatch.setattr(constants, "CLUSTER_LNS_ENABLED", True)
        monkeypatch.setattr(constants, "CLUSTER_LNS_TIME_LIMIT_S", 1)
        monkeypatch.setattr(constants, "CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY", 50.0)
        cd = _cluster(task_ids=["o0", "o1"], allocated={"v0": ["i0"]})
        orders = [_order("o0", "f0"), _order("o1", "f1")]
        dispatch, infeasible = solve_cluster(
            cd, orders, [_vehicle("v0")], [_implement("i0")],
            [_field("f0"), _field("f1", 48.6, 32.1)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        covered = {d["task_id"] for d in dispatch} | {i["task_id"] for i in infeasible}
        assert covered == {"o0", "o1"}
        assert len(dispatch) == 2

    def test_lns_skipped_below_value_threshold(self, monkeypatch):
        """A cluster under the high-value threshold solves without the LNS pass."""
        from fl_op.core import constants

        monkeypatch.setattr(constants, "CLUSTER_LNS_ENABLED", True)
        monkeypatch.setattr(constants, "CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY", 1.0e9)
        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        dispatch, _ = solve_cluster(
            cd, [_order("o0")], [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [_depot()], {}, {"v0": 0}, {"i0": 0},
        )
        assert {d["task_id"] for d in dispatch} == {"o0"}

    def test_load_capacity_bounds_route_load(self):
        """Two orders whose combined load exceeds the vehicle capacity: one drops."""
        cd = _cluster(task_ids=["o0", "o1"], allocated={"v0": ["i0"]})
        orders = [
            dataclasses.replace(_order("o0", "f0"), load_demand=80.0),
            dataclasses.replace(_order("o1", "f1"), load_demand=80.0),
        ]
        vehicle = dataclasses.replace(_vehicle("v0"), load_capacity=100.0)
        dispatch, infeasible = solve_cluster(
            cd, orders, [vehicle], [_implement("i0")],
            [_field("f0"), _field("f1", 48.6, 32.1)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert len(dispatch) == 1
        assert len(infeasible) == 1
        assert infeasible[0]["reason_code"] == "OPTIMIZATION_TRADEOFF"

    def test_zero_load_demand_leaves_capacity_unconstrained(self):
        cd = _cluster(task_ids=["o0", "o1"], allocated={"v0": ["i0"]})
        orders = [_order("o0", "f0"), _order("o1", "f1")]
        vehicle = dataclasses.replace(_vehicle("v0"), load_capacity=100.0)
        dispatch, _ = solve_cluster(
            cd, orders, [vehicle], [_implement("i0")],
            [_field("f0"), _field("f1", 48.6, 32.1)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert {d["task_id"] for d in dispatch} == {"o0", "o1"}

    def test_vehicle_without_capacity_carries_any_load(self):
        cd = _cluster(task_ids=["o0", "o1"], allocated={"v0": ["i0"]})
        orders = [
            dataclasses.replace(_order("o0", "f0"), load_demand=5000.0),
            dataclasses.replace(_order("o1", "f1"), load_demand=5000.0),
        ]
        dispatch, _ = solve_cluster(
            cd, orders, [_vehicle("v0")], [_implement("i0")],
            [_field("f0"), _field("f1", 48.6, 32.1)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert {d["task_id"] for d in dispatch} == {"o0", "o1"}

    def test_travel_lookup_drives_schedule_times(self):
        """A network link much slower than haversine delays the arrival."""
        from datetime import datetime, timezone

        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        link_seconds = 6 * 3600
        lookup = {("d0", "f0"): link_seconds, ("f0", "d0"): link_seconds}
        dispatch, _ = solve_cluster(
            cd, [_order("o0")], [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [_depot()], {}, {"v0": 0}, {"i0": 0},
            travel_lookup=lookup,
        )
        assert len(dispatch) == 1
        start = datetime.fromisoformat(dispatch[0]["scheduled_start"])
        elapsed_s = (start - datetime.now(tz=timezone.utc)).total_seconds()
        assert elapsed_s >= link_seconds - 300

    def test_infeasible_order_does_not_sink_whole_cluster(self):
        cd = _cluster(task_ids=["o_ok", "o_late"], allocated={"v0": ["i0"]})
        ok = _order("o_ok", "f0")
        # Frozen rows are immutable; build the late variant with replace().
        late = dataclasses.replace(_order("o_late", "f1"), deadline="2020-01-01T00:00:00+00:00")
        dispatch, infeasible = solve_cluster(
            cd, [ok, late], [_vehicle("v0")], [_implement("i0")],
            [_field("f0"), _field("f1", 48.6, 32.1)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert {d["task_id"] for d in dispatch} == {"o_ok"}
        assert {i["task_id"] for i in infeasible} == {"o_late"}
        assert infeasible[0]["reason_code"] == "OPTIMIZATION_TRADEOFF"


# ---------------------------------------------------------------------------
# Machine-readable solve telemetry
# ---------------------------------------------------------------------------


class TestSolveTelemetry:
    def test_solved_cluster_records_diagnostics(self):
        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        dispatch, _, telemetry = solve_cluster_instrumented(
            cd, [_order("o0")], [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [_depot()], {}, {"v0": 0}, {"i0": 0},
        )
        assert telemetry["status"] == STATUS_SOLVED
        assert telemetry["cluster_id"] == "cl0"
        assert telemetry["n_tasks"] == 1
        assert telemetry["n_routing_vehicles"] == 1
        assert telemetry["n_dispatched"] == len(dispatch) == 1
        assert telemetry["solve_wall_s"] >= 0.0
        assert telemetry["routing_status"] in {"ROUTING_SUCCESS", "ROUTING_OPTIMAL"}
        assert telemetry["hit_time_limit"] is False
        assert telemetry["objective_value"] is not None
        assert telemetry["first_solution_objective"] is not None
        assert telemetry["lns_attempted"] is False

    def test_lns_pass_records_attempt_and_delta(self, monkeypatch):
        from fl_op.core import constants

        monkeypatch.setattr(constants, "CLUSTER_LNS_ENABLED", True)
        monkeypatch.setattr(constants, "CLUSTER_LNS_TIME_LIMIT_S", 1)
        monkeypatch.setattr(constants, "CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY", 50.0)
        cd = _cluster(task_ids=["o0", "o1"], allocated={"v0": ["i0"]})
        orders = [_order("o0", "f0"), _order("o1", "f1")]
        _, _, telemetry = solve_cluster_instrumented(
            cd, orders, [_vehicle("v0")], [_implement("i0")],
            [_field("f0"), _field("f1", 48.6, 32.1)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert telemetry["lns_attempted"] is True
        # An improvement is not guaranteed, but a recorded improvement must
        # carry a strictly negative objective delta.
        if telemetry["lns_improved"]:
            assert telemetry["lns_objective_delta"] < 0
        else:
            assert telemetry["lns_objective_delta"] == 0

    def test_input_error_cluster_records_status(self):
        cd = _cluster(depot_ref="ghost", task_ids=["o0"], allocated={"v0": ["i0"]})
        _, infeasible, telemetry = solve_cluster_instrumented(
            cd, [_order("o0")], [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [], {}, {"v0": 0}, {"i0": 0},
        )
        assert telemetry["status"] == "input_error"
        assert telemetry["n_unserved"] == len(infeasible) == 1

    def test_summary_aggregates_records(self):
        records = [
            {"cluster_id": "a", "status": STATUS_SOLVED, "solve_wall_s": 1.5,
             "lns_attempted": True, "lns_improved": True, "lns_objective_delta": -20},
            {"cluster_id": "b", "status": STATUS_NO_SOLUTION, "solve_wall_s": 60.0,
             "hit_time_limit": True},
        ]
        summary = summarize_cluster_telemetry(records)
        assert summary["n_clusters"] == 2
        assert summary["statuses"] == {STATUS_SOLVED: 1, STATUS_NO_SOLUTION: 1}
        assert summary["n_hit_time_limit"] == 1
        assert summary["total_solve_wall_s"] == 61.5
        assert summary["n_lns_attempted"] == 1
        assert summary["n_lns_improved"] == 1
        assert summary["total_lns_objective_delta"] == -20

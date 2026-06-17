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

    def test_time_objective_solves_cluster(self):
        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        dispatch, infeasible, telemetry = solve_cluster_instrumented(
            cd,
            [_order("o0")],
            [_vehicle("v0")],
            [_implement("i0")],
            [_field("f0")],
            [_depot()],
            {},
            {"v0": 0},
            {"i0": 0},
            optimization_objective="time",
        )
        assert {d["task_id"] for d in dispatch} | {
            i["task_id"] for i in infeasible
        } == {"o0"}
        assert telemetry["optimization_objective"] == "time"

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

    def test_depot_reload_serves_demand_beyond_one_fill(self):
        """Two 80 kg orders on a 100 kg vehicle: a depot reload between the
        stops serves both in two trips instead of dropping one."""
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
        assert {d["task_id"] for d in dispatch} == {"o0", "o1"}, infeasible
        assert infeasible == []

    def test_multiple_reloads_serve_demand_beyond_two_fills(self):
        """Three 80 kg orders on a 100 kg vehicle need two refills (three
        trips); the extra optional reload stop lets all three be served."""
        cd = _cluster(task_ids=["o0", "o1", "o2"], allocated={"v0": ["i0"]})
        orders = [
            dataclasses.replace(_order("o0", "f0"), load_demand=80.0),
            dataclasses.replace(_order("o1", "f1"), load_demand=80.0),
            dataclasses.replace(_order("o2", "f2"), load_demand=80.0),
        ]
        vehicle = dataclasses.replace(_vehicle("v0"), load_capacity=100.0)
        dispatch, infeasible = solve_cluster(
            cd, orders, [vehicle], [_implement("i0")],
            [_field("f0"), _field("f1", 48.6, 32.1), _field("f2", 48.55, 32.05)],
            [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert {d["task_id"] for d in dispatch} == {"o0", "o1", "o2"}, infeasible
        assert infeasible == []

    def test_load_capacity_bounds_route_load_without_reloads(self, monkeypatch):
        """Single-trip semantics (reloads disabled): one of the orders drops."""
        from fl_op.core import constants

        monkeypatch.setattr(constants, "DEPOT_RELOAD_ENABLED", False)
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


# ---------------------------------------------------------------------------
# Cost-true routing: fuel-priced arcs and net dispatch margins
# ---------------------------------------------------------------------------


class TestCostTrueRouting:
    def test_fuel_efficient_vehicle_wins_time_equal_arcs(self):
        """Both vehicles start at the depot; the frugal one serves the task."""
        thirsty = dataclasses.replace(_vehicle("v_thirsty"), fuel_consumption_rate=50.0)
        frugal = dataclasses.replace(_vehicle("v_frugal"), fuel_consumption_rate=5.0)
        cd = _cluster(
            task_ids=["o0"],
            allocated={"v_thirsty": ["i0"], "v_frugal": ["i1"]},
        )
        dispatch, infeasible = solve_cluster(
            cd, [_order("o0", fid="f_far")],
            [thirsty, frugal], [_implement("i0"), _implement("i1")],
            [_field("f_far", lat=49.0)], [_depot()],
            {}, {"v_thirsty": 0, "v_frugal": 1}, {"i0": 0, "i1": 1},
        )
        assert {d["task_id"] for d in dispatch} == {"o0"}, infeasible
        assert dispatch[0]["prime_asset_id"] == "v_frugal"

    def test_objective_trades_slow_cheap_for_fast_expensive(self):
        """Slow/cheap vs fast/expensive: cost mode picks the cheap mover, time
        mode picks the fast one and lands an earlier completion without losing
        the assignment.

        The cheap pair burns little fuel (low arc cost) but works the field
        slowly (long service time); the expensive pair burns a lot of fuel
        (high arc cost) yet finishes the operation far faster. Cost mode prices
        only fuel on travel arcs, so it favors the cheap prime; time mode prices
        travel + service, so it favors the fast implement and lands an earlier
        completion -- without dropping the order.
        """
        from datetime import datetime

        slow_cheap = dataclasses.replace(
            _vehicle("v_slow_cheap"), fuel_consumption_rate=5.0
        )
        fast_expensive = dataclasses.replace(
            _vehicle("v_fast_expensive"), fuel_consumption_rate=80.0
        )
        # i0 is the slow implement (narrow + slow -> long service time);
        # i1 is the fast implement (wide + fast -> short service time).
        slow_impl = dataclasses.replace(
            _implement("i0"), working_width=4.0, max_speed=2.0
        )
        fast_impl = dataclasses.replace(
            _implement("i1"), working_width=36.0, max_speed=15.0
        )
        allocated = {"v_slow_cheap": ["i0"], "v_fast_expensive": ["i1"]}

        def _solve(objective):
            cd = _cluster(task_ids=["o0"], allocated=allocated)
            return solve_cluster(
                cd, [_order("o0", fid="f_far")],
                [slow_cheap, fast_expensive],
                [slow_impl, fast_impl],
                [_field("f_far", lat=49.0)], [_depot()],
                {}, {"v_slow_cheap": 0, "v_fast_expensive": 1}, {"i0": 0, "i1": 1},
                optimization_objective=objective,
            )

        cost_dispatch, cost_infeasible = _solve("cost")
        time_dispatch, time_infeasible = _solve("time")

        # Assignment count is preserved under both objectives.
        assert {d["task_id"] for d in cost_dispatch} == {"o0"}, cost_infeasible
        assert {d["task_id"] for d in time_dispatch} == {"o0"}, time_infeasible

        # Cost minimization picks the slow/cheap mover; time minimization the fast one.
        assert cost_dispatch[0]["prime_asset_id"] == "v_slow_cheap"
        assert time_dispatch[0]["prime_asset_id"] == "v_fast_expensive"

        # Time objective lowers the completion-time KPI (earlier makespan).
        cost_end = datetime.fromisoformat(cost_dispatch[0]["scheduled_end"]).timestamp()
        time_end = datetime.fromisoformat(time_dispatch[0]["scheduled_end"]).timestamp()
        assert time_end < cost_end

    def test_high_labor_rate_flips_cost_mode_to_fast_bundle(self):
        """Driver time changes the cost-mode choice.

        With no operating rate, cost mode prices only travel fuel and keeps the
        slow/cheap mover. Pricing driver labour per operating hour makes the
        slow pair's long on-task service time the dominant cost, so cost mode
        flips to the fast/expensive bundle that finishes sooner -- the routing
        topology is now expressive enough for the rate to change the decision.
        """
        from fl_op.solver.cost_rates import ResourcePrices

        slow_cheap = dataclasses.replace(
            _vehicle("v_slow_cheap"), fuel_consumption_rate=5.0
        )
        fast_expensive = dataclasses.replace(
            _vehicle("v_fast_expensive"), fuel_consumption_rate=80.0
        )
        slow_impl = dataclasses.replace(
            _implement("i0"), working_width=4.0, max_speed=2.0
        )
        fast_impl = dataclasses.replace(
            _implement("i1"), working_width=36.0, max_speed=15.0
        )
        allocated = {"v_slow_cheap": ["i0"], "v_fast_expensive": ["i1"]}

        def _solve(prices):
            cd = _cluster(task_ids=["o0"], allocated=allocated)
            return solve_cluster(
                cd, [_order("o0", fid="f_near")],
                [slow_cheap, fast_expensive], [slow_impl, fast_impl],
                [_field("f_near", lat=48.6)], [_depot()],
                {}, {"v_slow_cheap": 0, "v_fast_expensive": 1}, {"i0": 0, "i1": 1},
                resource_prices=prices,
            )

        no_labor, no_labor_inf = _solve(ResourcePrices())
        with_labor, with_labor_inf = _solve(ResourcePrices(labor_eur_per_h=80.0))
        assert no_labor[0]["prime_asset_id"] == "v_slow_cheap", no_labor_inf
        assert with_labor[0]["prime_asset_id"] == "v_fast_expensive", with_labor_inf

    def test_margin_is_net_of_fuel_and_material_at_resolved_prices(self):
        from fl_op.solver.cost_rates import ResourcePrices

        prices = ResourcePrices(fuel_eur_per_l=2.0, material_eur_per_kg=1.0)
        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        dispatch, _ = solve_cluster(
            cd, [_order("o0")], [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
            resource_prices=prices,
        )
        assert len(dispatch) == 1
        package = dispatch[0]
        expected = (
            2000.0
            - package["estimated_fuel_l"] * prices.fuel_eur_per_l
            - package["estimated_fertilizer_kg"] * prices.material_eur_per_kg
        )
        assert package["estimated_margin_eur"] == pytest.approx(expected, abs=0.05)

    def test_fuel_estimate_includes_inbound_travel_leg(self):
        """A far field adds travel fuel on top of the operation fuel."""
        cd_near = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        near_dispatch, _ = solve_cluster(
            cd_near, [_order("o0")], [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        cd_far = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        far_dispatch, _ = solve_cluster(
            cd_far, [_order("o0", fid="f_far")], [_vehicle("v0")],
            [_implement("i0")], [_field("f_far", lat=49.0)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert far_dispatch[0]["estimated_fuel_l"] > near_dispatch[0]["estimated_fuel_l"]


class TestOperatingCostExpansion:
    """Driver labour, machine wear, and tolls priced into dispatch margins."""

    def _far_solve(self, prices):
        from fl_op.solver.cost_rates import ResourcePrices

        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        dispatch, _ = solve_cluster(
            cd, [_order("o0", fid="f_far")], [_vehicle("v0")], [_implement("i0")],
            [_field("f_far", lat=49.0)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
            resource_prices=prices or ResourcePrices(),
        )
        return dispatch

    def test_margin_is_net_of_operating_and_toll_costs(self):
        from fl_op.solver.cost_rates import ResourcePrices

        prices = ResourcePrices(
            fuel_eur_per_l=2.0, material_eur_per_kg=1.0,
            labor_eur_per_h=30.0, machine_wear_eur_per_h=10.0, toll_eur_per_km=0.5,
        )
        dispatch = self._far_solve(prices)
        assert len(dispatch) == 1
        package = dispatch[0]
        # The new per-package cost fields are populated for a travelled leg.
        assert package["estimated_distance_km"] > 0
        assert package["estimated_labor_cost_eur"] > 0
        assert package["estimated_machine_wear_cost_eur"] > 0
        assert package["estimated_toll_cost_eur"] > 0
        expected = (
            2000.0
            - package["estimated_energy_cost_eur"]
            - package["estimated_fertilizer_kg"] * prices.material_eur_per_kg
            - package["estimated_labor_cost_eur"]
            - package["estimated_machine_wear_cost_eur"]
            - package["estimated_toll_cost_eur"]
        )
        assert package["estimated_margin_eur"] == pytest.approx(expected, abs=0.05)

    def test_operating_and_toll_reduce_margin(self):
        from fl_op.solver.cost_rates import ResourcePrices

        base = self._far_solve(
            ResourcePrices(fuel_eur_per_l=2.0, material_eur_per_kg=1.0)
        )[0]["estimated_margin_eur"]
        expanded = self._far_solve(
            ResourcePrices(
                fuel_eur_per_l=2.0, material_eur_per_kg=1.0,
                labor_eur_per_h=30.0, machine_wear_eur_per_h=10.0,
                toll_eur_per_km=0.5,
            )
        )[0]["estimated_margin_eur"]
        assert expanded < base

    def test_zero_operating_rates_leave_distance_and_costs_at_zero(self):
        from fl_op.solver.cost_rates import ResourcePrices

        package = self._far_solve(ResourcePrices())[0]
        assert package["estimated_distance_km"] == 0.0
        assert package["estimated_labor_cost_eur"] == 0.0
        assert package["estimated_machine_wear_cost_eur"] == 0.0
        assert package["estimated_toll_cost_eur"] == 0.0


# ---------------------------------------------------------------------------
# Load-dimension extensions: compartments, reloads, pickup-and-delivery
# ---------------------------------------------------------------------------


class TestLoadDimensionExtensions:
    def test_per_material_compartments_isolate_loads(self, monkeypatch):
        """Different-material loads fill separate compartments: both fit
        in one trip even though their sum exceeds either compartment."""
        from fl_op.core import constants

        monkeypatch.setattr(constants, "DEPOT_RELOAD_ENABLED", False)
        cd = _cluster(task_ids=["o0", "o1"], allocated={"v0": ["i0"]})
        orders = [
            dataclasses.replace(
                _order("o0", "f0"), load_demand=80.0, load_material="seed"
            ),
            dataclasses.replace(
                _order("o1", "f1"), load_demand=80.0, load_material="fertilizer"
            ),
        ]
        vehicle = dataclasses.replace(
            _vehicle("v0"),
            load_capacity=100.0,
            load_capacities={"seed": 100.0, "fertilizer": 100.0},
        )
        dispatch, infeasible = solve_cluster(
            cd, orders, [vehicle], [_implement("i0")],
            [_field("f0"), _field("f1", 48.6, 32.1)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert {d["task_id"] for d in dispatch} == {"o0", "o1"}, infeasible

    def test_same_compartment_loads_still_compete(self, monkeypatch):
        """Same-material loads share one compartment: one order drops."""
        from fl_op.core import constants

        monkeypatch.setattr(constants, "DEPOT_RELOAD_ENABLED", False)
        cd = _cluster(task_ids=["o0", "o1"], allocated={"v0": ["i0"]})
        orders = [
            dataclasses.replace(
                _order("o0", "f0"), load_demand=80.0, load_material="seed"
            ),
            dataclasses.replace(
                _order("o1", "f1"), load_demand=80.0, load_material="seed"
            ),
        ]
        vehicle = dataclasses.replace(
            _vehicle("v0"),
            load_capacity=500.0,
            load_capacities={"seed": 100.0},
        )
        dispatch, infeasible = solve_cluster(
            cd, orders, [vehicle], [_implement("i0")],
            [_field("f0"), _field("f1", 48.6, 32.1)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert len(dispatch) == 1
        assert len(infeasible) == 1

    def test_material_without_compartment_uses_aggregate_capacity(self, monkeypatch):
        """A material absent from the compartment map falls back to the
        aggregate load capacity."""
        from fl_op.core import constants

        monkeypatch.setattr(constants, "DEPOT_RELOAD_ENABLED", False)
        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        orders = [
            dataclasses.replace(
                _order("o0", "f0"), load_demand=80.0, load_material="lime"
            ),
        ]
        vehicle = dataclasses.replace(
            _vehicle("v0"),
            load_capacity=100.0,
            load_capacities={"seed": 1.0},
        )
        dispatch, infeasible = solve_cluster(
            cd, orders, [vehicle], [_implement("i0")],
            [_field("f0")], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert {d["task_id"] for d in dispatch} == {"o0"}, infeasible

    def test_pickup_and_delivery_detours_via_pickup_location(self):
        """A paired task's schedule includes the depot->pickup->site detour."""
        from datetime import datetime, timezone

        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        order = dataclasses.replace(
            _order("o0", "f0"),
            load_demand=50.0,
            pickup_location_ref="f_pick",
            service_duration_min=60.0,
        )
        vehicle = dataclasses.replace(_vehicle("v0"), load_capacity=100.0)
        dispatch, infeasible = solve_cluster(
            cd, [order], [vehicle], [_implement("i0")],
            [_field("f0"), _field("f_pick", 49.5, 32.0)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
            now_epoch=now_epoch,
        )
        assert {d["task_id"] for d in dispatch} == {"o0"}, infeasible
        start_epoch = int(
            datetime.fromisoformat(dispatch[0]["scheduled_start"]).timestamp()
        )
        # The pickup sits ~111 km north: two field-travel legs of ~7.4 h each
        # must precede the task start (a direct depot->site start would be
        # immediate, both share coordinates).
        assert start_epoch - now_epoch > 10 * 3600

    def test_pickup_load_beyond_capacity_drops_the_pair(self):
        """A paired load no compartment can carry drops the whole pair."""
        cd = _cluster(task_ids=["o0"], allocated={"v0": ["i0"]})
        order = dataclasses.replace(
            _order("o0", "f0"),
            load_demand=150.0,
            pickup_location_ref="f_pick",
        )
        vehicle = dataclasses.replace(_vehicle("v0"), load_capacity=100.0)
        dispatch, infeasible = solve_cluster(
            cd, [order], [vehicle], [_implement("i0")],
            [_field("f0"), _field("f_pick", 48.7, 32.2)], [_depot()],
            {}, {"v0": 0}, {"i0": 0},
        )
        assert dispatch == []
        assert [i["task_id"] for i in infeasible] == ["o0"]

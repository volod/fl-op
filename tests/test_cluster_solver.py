"""T16-T23: Cluster solver tests.

Tests verify the I/O contract and error-handling guarantees of solve_cluster().
OR-Tools is a required dependency so these tests assume the venv is active.
"""

import pytest

from fl_op.solver.cluster_solver import solve_cluster


# ---------------------------------------------------------------------------
# Minimal data builders (raw dicts, matching codec-loaded source rows)
# ---------------------------------------------------------------------------


def _cluster(cluster_id="cl0", depot_id="d0", order_ids=None, allocated=None):
    return {
        "cluster_id": cluster_id,
        "depot_id": depot_id,
        "order_ids": order_ids or [],
        "allocated_vehicle_implements": allocated or {},
        "total_penalty_per_day": 100.0,
    }


def _order(oid, fid="f0"):
    return {
        "order_id": oid, "field_id": fid, "operation_type": "SPRAYING",
        "area_ha": "10", "deadline": "2027-12-01T00:00:00+00:00",
        "penalty_per_day_eur": "100", "status": "pending",
        "estimated_revenue_eur": "2000", "contract_id": "c0",
    }


def _vehicle(vid, depot_id="d0"):
    return {
        "vehicle_id": vid, "vehicle_type": "TRACTOR", "rated_power_kw": "150",
        "fuel_tank_l": "400", "fuel_consumption_l_per_h": "18",
        "current_lat": "48.5", "current_lon": "32.0",
        "depot_id": depot_id, "travel_speed_kmh": "15",
    }


def _implement(iid, depot_id="d0"):
    return {
        "implement_id": iid, "implement_type": "SPRAYER",
        "compatible_operations": "['SPRAYING']", "required_power_kw": "100",
        "working_width_m": "24", "min_speed_kmh": "5", "max_speed_kmh": "12",
        "fertilizer_capacity_kg": "500", "depot_id": depot_id,
    }


def _field(fid, lat=48.5, lon=32.0):
    return {"field_id": fid, "centroid_lat": str(lat), "centroid_lon": str(lon), "area_ha": "10"}


def _depot(did="d0", lat=48.5, lon=32.0):
    return {"depot_id": did, "lat": str(lat), "lon": str(lon)}


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

    def test_empty_order_ids_returns_empty_lists(self):
        dispatch, infeasible = solve_cluster(_cluster(order_ids=[]), [], [], [], [], [], {}, {}, {})
        assert dispatch == []
        assert infeasible == []

    def test_never_raises_on_bad_data(self):
        cd = _cluster(order_ids=["o0"], allocated={"v0": ["i0"]})
        try:
            result = solve_cluster(cd, [], [], [], [], [], {}, {"v0": 0}, {"i0": 0})
        except Exception as exc:
            pytest.fail(f"solve_cluster raised unexpectedly: {exc}")
        assert len(result) == 2

    def test_all_order_ids_accounted_for(self):
        cd = _cluster(order_ids=["o0", "o1"], allocated={"v0": ["i0"]})
        orders = [_order("o0", "f0"), _order("o1", "f1")]
        vehicles = [_vehicle("v0")]
        implements = [_implement("i0")]
        fields = [_field("f0"), _field("f1", 48.6, 32.1)]
        depots = [_depot()]
        dispatch, infeasible = solve_cluster(
            cd, orders, vehicles, implements, fields, depots,
            {}, {"v0": 0}, {"i0": 0},
        )
        covered = {d["order_id"] for d in dispatch} | {i["order_id"] for i in infeasible}
        assert covered == {"o0", "o1"}


# ---------------------------------------------------------------------------
# Early-exit infeasibility paths (no OR-Tools solve needed)
# ---------------------------------------------------------------------------


class TestEarlyInfeasibilityPaths:
    def test_no_allocated_resources_marks_infeasible(self):
        cd = _cluster(order_ids=["o0"], allocated={})
        orders = [_order("o0")]
        dispatch, infeasible = solve_cluster(
            cd, orders, [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [_depot()], {}, {"v0": 0}, {"i0": 0},
        )
        assert dispatch == []
        assert len(infeasible) == 1
        assert infeasible[0]["order_id"] == "o0"
        assert infeasible[0]["reason_code"] == "NO_COMPATIBLE_BUNDLE"

    def test_missing_depot_marks_infeasible(self):
        cd = _cluster(depot_id="ghost", order_ids=["o0"], allocated={"v0": ["i0"]})
        orders = [_order("o0")]
        dispatch, infeasible = solve_cluster(
            cd, orders, [_vehicle("v0")], [_implement("i0")],
            [_field("f0")], [],  # depots list empty -> depot not found
            {}, {"v0": 0}, {"i0": 0},
        )
        assert dispatch == []
        assert any(i["reason_code"] == "LOCATION_DATA_INVALID" for i in infeasible)

    def test_order_not_in_data_marks_infeasible(self):
        cd = _cluster(order_ids=["o_missing"], allocated={"v0": ["i0"]})
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
        cd = _cluster(order_ids=["o0"], allocated={})
        orders = [_order("o0")]
        _, infeasible = solve_cluster(cd, orders, [], [], [], [], {}, {}, {})
        for item in infeasible:
            assert "order_id" in item, "infeasible item missing order_id"
            assert "cluster_id" in item, "infeasible item missing cluster_id"
            assert "reason_code" in item, "infeasible item missing reason_code"
            assert "detail" in item, "infeasible item missing detail"

    def test_infeasible_cluster_id_matches(self):
        cd = _cluster(cluster_id="my_cluster", order_ids=["o0"], allocated={})
        _, infeasible = solve_cluster(cd, [_order("o0")], [], [], [], [], {}, {}, {})
        assert all(i["cluster_id"] == "my_cluster" for i in infeasible)


# ---------------------------------------------------------------------------
# Solver integration: single order, single vehicle (fast OR-Tools call)
# ---------------------------------------------------------------------------


class TestSolverIntegration:
    def test_single_order_covered(self):
        cd = _cluster(order_ids=["o0"], allocated={"v0": ["i0"]})
        orders = [_order("o0")]
        vehicles = [_vehicle("v0")]
        implements = [_implement("i0")]
        fields = [_field("f0")]
        depots = [_depot()]
        dispatch, infeasible = solve_cluster(
            cd, orders, vehicles, implements, fields, depots,
            {}, {"v0": 0}, {"i0": 0},
        )
        all_ids = {d["order_id"] for d in dispatch} | {i["order_id"] for i in infeasible}
        assert "o0" in all_ids

    def test_dispatch_items_have_required_fields(self):
        cd = _cluster(order_ids=["o0"], allocated={"v0": ["i0"]})
        orders = [_order("o0")]
        vehicles = [_vehicle("v0")]
        implements = [_implement("i0")]
        fields = [_field("f0")]
        depots = [_depot()]
        dispatch, _ = solve_cluster(
            cd, orders, vehicles, implements, fields, depots,
            {}, {"v0": 0}, {"i0": 0},
        )
        for pkg in dispatch:
            for field in ("dispatch_id", "cluster_id", "vehicle_id", "implement_id",
                          "order_id", "depot_id", "scheduled_start", "scheduled_end"):
                assert field in pkg, f"dispatch package missing {field}"

    def test_infeasible_order_does_not_sink_whole_cluster(self):
        cd = _cluster(order_ids=["o_ok", "o_late"], allocated={"v0": ["i0"]})
        ok = _order("o_ok", "f0")
        late = _order("o_late", "f1")
        late["deadline"] = "2020-01-01T00:00:00+00:00"
        vehicles = [_vehicle("v0")]
        implements = [_implement("i0")]
        fields = [_field("f0"), _field("f1", 48.6, 32.1)]
        depots = [_depot()]

        dispatch, infeasible = solve_cluster(
            cd,
            [ok, late],
            vehicles,
            implements,
            fields,
            depots,
            {},
            {"v0": 0},
            {"i0": 0},
        )

        assert {d["order_id"] for d in dispatch} == {"o_ok"}
        assert {i["order_id"] for i in infeasible} == {"o_late"}
        assert infeasible[0]["reason_code"] == "OPTIMIZATION_TRADEOFF"

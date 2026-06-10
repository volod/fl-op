"""T16-T23: Cluster solver tests (canonical solver rows).

Tests verify the I/O contract and error-handling guarantees of solve_cluster().
OR-Tools is a required dependency so these tests assume the venv is active.
"""

import dataclasses

import pytest

from fl_op.solver.cluster_solver import solve_cluster
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

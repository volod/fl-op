"""T11-T13: Resource allocator tests (canonical solver rows).

T11: No related-equipment asset is assigned to more than one cluster.
T12: Higher penalty cluster wins contested equipment.
T13: Equal penalty sum -> tiebreak by cluster_id.
"""

from fl_op.solver.allocation import allocate_resources
from fl_op.solver.feasibility import build_compat_matrix
from fl_op.solver.types import ClusterSpec, OperatorRow, PrimeMoverRow, RelatedRow, TaskRow


def _veh(vid: str, power: float = 150.0) -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict(
        {"asset_id": vid, "asset_type": "TRACTOR", "rated_power": str(power),
         "fuel_tank_volume": "400", "fuel_consumption_rate": "18",
         "lat": "48.5", "lon": "32.0", "home_depot_ref": "d0", "travel_speed": "15"})


def _impl(iid: str, power: float = 100.0) -> RelatedRow:
    return RelatedRow.from_canonical_dict(
        {"asset_id": iid, "asset_type": "SPRAYER",
         "compatible_operations": "['SPRAYING']", "required_power": str(power),
         "working_width": "24", "min_speed": "5", "max_speed": "12",
         "material_capacity": "0", "home_depot_ref": "d0"})


def _order(oid: str, penalty: float = 100.0) -> TaskRow:
    return TaskRow.from_canonical_dict(
        {"task_id": oid, "operation_type": "SPRAYING", "location_ref": "f0",
         "area": "100", "deadline": "2026-06-01T00:00:00+00:00",
         "penalty_per_day": str(penalty), "status": "pending",
         "revenue": "5000", "order_ref": "c0"})


def _operator(opid: str, depot: str = "d0") -> OperatorRow:
    return OperatorRow.from_canonical_dict(
        {"asset_id": opid, "name": opid, "shift_start": "21600",
         "shift_end": "57600", "certified_operations": "['SPRAYING']", "home_depot_ref": depot})


def _build_setup(n_vehicles: int = 2, n_implements: int = 2):
    vehicles_raw = [_veh(f"v{i}") for i in range(n_vehicles)]
    implements_raw = [_impl(f"i{i}") for i in range(n_implements)]
    compat, power_margin = build_compat_matrix(vehicles_raw, implements_raw)
    vehicle_index = {v.asset_id: i for i, v in enumerate(vehicles_raw)}
    implement_index = {im.asset_id: i for i, im in enumerate(implements_raw)}
    return vehicles_raw, implements_raw, compat, power_margin, vehicle_index, implement_index


def _cluster(cid: str, task_ids: list[str], penalty: float) -> ClusterSpec:
    return {
        "cluster_id": cid, "depot_ref": "d0",
        "task_ids": task_ids, "allocated_prime_related": {},
        "total_penalty_per_day": penalty,
    }


class TestNoCrossClusterDuplicate:
    def test_implement_not_in_two_clusters(self):
        _vr, _ir, _compat, pm, v_idx, i_idx = _build_setup(2, 1)
        orders = [_order("o0", 500), _order("o1", 100)]
        operators = [_operator("op0")]
        feasible = {"o0": [(0, 0)], "o1": [(1, 0)]}  # same related i0 -> conflict
        result = allocate_resources(
            [_cluster("c0", ["o0"], 500.0), _cluster("c1", ["o1"], 100.0)],
            orders, operators, pm, v_idx, i_idx, feasible,
        )
        all_impls = []
        for c in result:
            for vid, imps in c["allocated_prime_related"].items():
                all_impls.extend(imps)
        assert len(all_impls) == len(set(all_impls))


class TestPenaltyWeightedWinner:
    def test_high_penalty_cluster_wins_implement(self):
        _vr, _ir, _compat, pm, v_idx, i_idx = _build_setup(2, 1)
        orders = [_order("o_high", 1000), _order("o_low", 10)]
        operators = [_operator("op0")]
        feasible = {"o_high": [(0, 0)], "o_low": [(0, 0)]}
        result = allocate_resources(
            [_cluster("c_low", ["o_low"], 10.0), _cluster("c_high", ["o_high"], 1000.0)],
            orders, operators, pm, v_idx, i_idx, feasible,
        )
        c_high_result = next(c for c in result if c["cluster_id"] == "c_high")
        assert c_high_result["allocated_prime_related"] != {}


class TestEqualPenaltyTiebreak:
    def test_tiebreak_by_cluster_id(self):
        _vr, _ir, _compat, pm, v_idx, i_idx = _build_setup(2, 1)
        orders = [_order("o0", 500), _order("o1", 500)]
        operators = [_operator("op0")]
        feasible = {"o0": [(0, 0)], "o1": [(0, 0)]}
        result = allocate_resources(
            [_cluster("cluster_b", ["o1"], 500.0), _cluster("cluster_a", ["o0"], 500.0)],
            orders, operators, pm, v_idx, i_idx, feasible,
        )
        ca_result = next(c for c in result if c["cluster_id"] == "cluster_a")
        cb_result = next(c for c in result if c["cluster_id"] == "cluster_b")
        ca_has = ca_result["allocated_prime_related"] != {}
        cb_has = cb_result["allocated_prime_related"] != {}
        assert ca_has or cb_has
        assert not (ca_has and cb_has)


class TestCandidateDiversity:
    def test_allocator_does_not_starve_on_implement_major_pair_ordering(self):
        _vr, _ir, _compat, pm, v_idx, i_idx = _build_setup(40, 2)
        orders = [_order("o0", 500), _order("o1", 500)]
        operators = [_operator("op0")]
        feasible = {
            "o0": [(vehicle, 0) for vehicle in range(40)] + [(1, 1)],
            "o1": [(vehicle, 0) for vehicle in range(40)] + [(2, 1)],
        }
        cluster = _cluster("cluster_a", ["o0", "o1"], 1000.0)
        result = allocate_resources(
            [cluster], orders, operators, pm, v_idx, i_idx, feasible,
        )
        allocated = result[0]["allocated_prime_related"]
        allocated_implements = {iid for iids in allocated.values() for iid in iids}
        assert len(allocated) == 2
        assert allocated_implements == {"i0", "i1"}


class TestScoredPreallocation:
    def test_allocator_uses_shared_greedy_score_when_available(self):
        _vr, _ir, _compat, pm, v_idx, i_idx = _build_setup(2, 2)
        orders = [_order("o0", 500)]
        operators = [_operator("op0")]
        feasible = {"o0": [(0, 0), (1, 1)]}
        scored = {"o0": [(1000.0, 1, 1), (100.0, 0, 0)]}
        cluster = _cluster("cluster_a", ["o0"], 500.0)
        result = allocate_resources(
            [cluster], orders, operators, pm, v_idx, i_idx, feasible, scored,
        )
        assert result[0]["allocated_prime_related"] == {"v1": ["i1"]}


def _starved_cluster_inputs():
    """Two clusters contesting i0; the high cluster has an i1 alternative.

    Greedy lets the high-penalty cluster grab its best pair (v0, i0) and
    starves the low cluster (whose only option is i0). The global model must
    route the high cluster to (v1, i1) so both clusters are served.
    """
    setup = _build_setup(2, 2)
    orders = [_order("o_high", 1000), _order("o_low", 10)]
    operators = [_operator("op0"), _operator("op1")]
    feasible = {"o_high": [(0, 0), (1, 1)], "o_low": [(0, 0)]}
    scored = {"o_high": [(100.0, 0, 0), (90.0, 1, 1)], "o_low": [(50.0, 0, 0)]}
    clusters = [
        _cluster("c_high", ["o_high"], 1000.0),
        _cluster("c_low", ["o_low"], 10.0),
    ]
    return setup, orders, operators, feasible, scored, clusters


class TestGlobalAssignmentModel:
    def test_global_model_recovers_greedy_starved_cluster(self):
        setup, orders, operators, feasible, scored, clusters = _starved_cluster_inputs()
        _vr, _ir, _compat, pm, v_idx, i_idx = setup
        result = allocate_resources(
            clusters, orders, operators, pm, v_idx, i_idx, feasible, scored,
        )
        by_id = {c["cluster_id"]: c for c in result}
        assert by_id["c_high"]["allocated_prime_related"] == {"v1": ["i1"]}
        assert by_id["c_low"]["allocated_prime_related"] == {"v0": ["i0"]}

    def test_greedy_fallback_starves_low_cluster(self, monkeypatch):
        from fl_op.core import constants

        monkeypatch.setattr(constants, "GLOBAL_ASSIGNMENT_ENABLED", False)
        setup, orders, operators, feasible, scored, clusters = _starved_cluster_inputs()
        _vr, _ir, _compat, pm, v_idx, i_idx = setup
        result = allocate_resources(
            clusters, orders, operators, pm, v_idx, i_idx, feasible, scored,
        )
        by_id = {c["cluster_id"]: c for c in result}
        assert by_id["c_high"]["allocated_prime_related"] == {"v0": ["i0"]}
        assert by_id["c_low"]["allocated_prime_related"] == {}

    def test_oversized_model_falls_back_to_greedy(self, monkeypatch):
        from fl_op.core import constants

        monkeypatch.setattr(constants, "GLOBAL_ASSIGNMENT_MAX_MODEL_CANDIDATES", 1)
        setup, orders, operators, feasible, scored, clusters = _starved_cluster_inputs()
        _vr, _ir, _compat, pm, v_idx, i_idx = setup
        result = allocate_resources(
            clusters, orders, operators, pm, v_idx, i_idx, feasible, scored,
        )
        by_id = {c["cluster_id"]: c for c in result}
        # Greedy semantics: the high-penalty cluster takes its best pair.
        assert by_id["c_high"]["allocated_prime_related"] == {"v0": ["i0"]}

    def test_global_model_is_deterministic(self):
        results = []
        for _ in range(2):
            setup, orders, operators, feasible, scored, clusters = (
                _starved_cluster_inputs()
            )
            _vr, _ir, _compat, pm, v_idx, i_idx = setup
            result = allocate_resources(
                clusters, orders, operators, pm, v_idx, i_idx, feasible, scored,
            )
            results.append(
                [
                    (c["cluster_id"], c["allocated_prime_related"], c.get("operator_ref"))
                    for c in result
                ]
            )
        assert results[0] == results[1]

    def test_global_model_assigns_qualified_operator(self):
        setup, orders, operators, feasible, scored, clusters = _starved_cluster_inputs()
        _vr, _ir, _compat, pm, v_idx, i_idx = setup
        result = allocate_resources(
            clusters, orders, operators, pm, v_idx, i_idx, feasible, scored,
        )
        assigned = [c.get("operator_ref") for c in result]
        assert sorted(a for a in assigned if a) == ["op0", "op1"]


class TestCountVsMarginObjective:
    """countPriority blends count-first allocation against pure score."""

    @staticmethod
    def _contended_inputs():
        """One pair dominates on score; serving both clusters costs margin."""
        setup, orders, operators, feasible, _scored, clusters = (
            _starved_cluster_inputs()
        )
        scored = {
            "o_high": [(1000.0, 0, 0), (90.0, 1, 1)],
            "o_low": [(50.0, 0, 0)],
        }
        return setup, orders, operators, feasible, scored, clusters

    def test_full_count_priority_allocates_both_clusters(self):
        setup, orders, operators, feasible, scored, clusters = self._contended_inputs()
        _vr, _ir, _compat, pm, v_idx, i_idx = setup
        result = allocate_resources(
            clusters, orders, operators, pm, v_idx, i_idx, feasible, scored,
            count_priority=1.0,
        )
        by_id = {c["cluster_id"]: c for c in result}
        assert by_id["c_high"]["allocated_prime_related"] == {"v1": ["i1"]}
        assert by_id["c_low"]["allocated_prime_related"] == {"v0": ["i0"]}

    def test_zero_count_priority_prefers_the_high_margin_allocation(self):
        setup, orders, operators, feasible, scored, clusters = self._contended_inputs()
        _vr, _ir, _compat, pm, v_idx, i_idx = setup
        result = allocate_resources(
            clusters, orders, operators, pm, v_idx, i_idx, feasible, scored,
            count_priority=0.0,
        )
        by_id = {c["cluster_id"]: c for c in result}
        assert by_id["c_high"]["allocated_prime_related"] == {"v0": ["i0"]}
        assert by_id["c_low"]["allocated_prime_related"] == {}


class TestHoldAwareScoring:
    def test_build_free_capacity_fraction(self):
        from fl_op.solver.allocation.scoring import build_free_capacity

        now = 1_000_000
        held = {
            "v0": [(now, now + 12 * 3600)],
            "i0": [(now - 100, now - 50)],
        }
        capacity = build_free_capacity(held, now, horizon_s=24 * 3600)
        assert capacity["v0"] == 0.5
        assert capacity["i0"] == 1.0

    def test_build_free_capacity_penalizes_fragmentation(self):
        from fl_op.solver.allocation.scoring import build_free_capacity

        now = 1_000_000
        horizon = 24 * 3600
        h = 3600
        # Both assets are busy for a total of 12h (free total 12h), but differ in
        # how that free time is laid out across the horizon.
        # Contiguous: one 12h block in the middle leaves a 6h gap on each side.
        contiguous = {"v0": [(now + 6 * h, now + 18 * h)]}
        # Fragmented: three 4h blocks split the free time into 4h gaps.
        fragmented = {
            "v1": [
                (now + 4 * h, now + 8 * h),
                (now + 12 * h, now + 16 * h),
                (now + 20 * h, now + 24 * h),
            ]
        }
        contiguous_cap = build_free_capacity(contiguous, now, horizon_s=horizon)
        fragmented_cap = build_free_capacity(fragmented, now, horizon_s=horizon)
        # Largest single gap: 6h contiguous vs 4h fragmented.
        assert contiguous_cap["v0"] == 6 * h / horizon
        assert fragmented_cap["v1"] == 4 * h / horizon
        # Equal total free time, yet fragmentation scores strictly lower.
        assert fragmented_cap["v1"] < contiguous_cap["v0"]

    def test_build_free_capacity_edge_block_uses_exact_span(self):
        from fl_op.solver.allocation.scoring import build_free_capacity

        now = 1_000_000
        horizon = 24 * 3600
        # A block flush against the start edge leaves one exact tail gap.
        held = {"v0": [(now, now + 6 * 3600)]}
        capacity = build_free_capacity(held, now, horizon_s=horizon)
        assert capacity["v0"] == 18 * 3600 / horizon

    def test_held_implement_discounted_among_equals(self):
        _vr, _ir, _compat, pm, v_idx, i_idx = _build_setup(2, 2)
        orders = [_order("o0", 500)]
        operators = [_operator("op0")]
        feasible = {"o0": [(0, 0), (1, 1)]}
        scored = {"o0": [(100.0, 0, 0), (100.0, 1, 1)]}
        cluster = _cluster("cluster_a", ["o0"], 500.0)
        result = allocate_resources(
            [cluster], orders, operators, pm, v_idx, i_idx, feasible, scored,
            free_capacity={"i0": 0.5},
        )
        assert result[0]["allocated_prime_related"] == {"v1": ["i1"]}

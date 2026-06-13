"""T14-T15: Greedy warm-start tests (canonical solver rows)."""

import pytest

from fl_op.solver.feasibility import build_compat_matrix
from fl_op.solver.greedy import greedy_assign, vectorized_score
from fl_op.solver.types import PrimeMoverRow, RelatedRow, SiteRow, TaskRow


def _v(vid: str, lat: float = 48.5, lon: float = 32.0, power: float = 150.0) -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict(
        {"asset_id": vid, "asset_type": "TRACTOR", "rated_power": str(power),
         "fuel_tank_volume": "400", "fuel_consumption_rate": "18",
         "lat": str(lat), "lon": str(lon),
         "home_depot_ref": "d0", "travel_speed": "15"})


def _i(iid: str, power: float = 100.0) -> RelatedRow:
    return RelatedRow.from_canonical_dict(
        {"asset_id": iid, "asset_type": "SPRAYER",
         "compatible_operations": "['SPRAYING']", "required_power": str(power),
         "working_width": "24", "min_speed": "5", "max_speed": "12",
         "material_capacity": "0", "home_depot_ref": "d0"})


def _o(oid: str, fid: str = "f0") -> TaskRow:
    return TaskRow.from_canonical_dict(
        {"task_id": oid, "operation_type": "SPRAYING", "location_ref": fid,
         "area": "100", "deadline": "2026-06-01T00:00:00+00:00",
         "penalty_per_day": "200", "status": "pending",
         "revenue": "5000", "order_ref": "c0"})


def _f(fid: str, lat: float, lon: float) -> SiteRow:
    return SiteRow.from_canonical_dict(
        {"location_id": fid, "lat": str(lat), "lon": str(lon),
         "area": "100", "name": fid})


class TestVectorizedScore:
    def test_returns_all_task_ids(self):
        vehicles = [_v("v0"), _v("v1")]
        implements = [_i("i0"), _i("i1")]
        orders = [_o("o0"), _o("o1")]
        fields = [_f("f0", 48.5, 32.0), _f("f1", 48.6, 32.1)]
        build_compat_matrix(vehicles, implements)
        v_idx = {v.asset_id: i for i, v in enumerate(vehicles)}
        i_idx = {im.asset_id: i for i, im in enumerate(implements)}
        feasible = {"o0": [(0, 0), (1, 1)], "o1": [(0, 0), (1, 1)]}
        scored = vectorized_score(orders, vehicles, implements, fields, feasible, v_idx, i_idx)
        assert set(scored.keys()) == {"o0", "o1"}
        for oid, pairs in scored.items():
            assert len(pairs) > 0
            # Each entry is (score, v_idx, i_idx)
            for score, vi, ii in pairs:
                assert isinstance(score, float)

    def test_closer_vehicle_scores_higher(self):
        # v_near is right at the field; v_far is 200 km away
        field_lat, field_lon = 48.5, 32.0
        vehicles = [
            _v("v_near", lat=48.5, lon=32.0),
            _v("v_far", lat=46.5, lon=32.0),  # ~200 km south
        ]
        implements = [_i("i0"), _i("i1")]
        orders = [_o("o0")]
        fields = [_f("f0", field_lat, field_lon)]
        build_compat_matrix(vehicles, implements)
        v_idx = {v.asset_id: i for i, v in enumerate(vehicles)}
        i_idx = {im.asset_id: i for i, im in enumerate(implements)}
        feasible = {"o0": [(0, 0), (1, 1)]}
        scored = vectorized_score(orders, vehicles, implements, fields, feasible, v_idx, i_idx)
        pairs = scored["o0"]
        # First pair should be v_near (idx 0) with higher score
        best_v_idx = pairs[0][1]
        assert best_v_idx == 0  # v_near has lower repositioning cost


class TestGreedyAssign:
    def test_assigns_one_pair_per_order(self):
        scored = {"o0": [(100.0, 0, 0), (90.0, 1, 1)], "o1": [(80.0, 0, 0), (70.0, 1, 1)]}
        v_idx = {"v0": 0, "v1": 1}
        i_idx = {"i0": 0, "i1": 1}
        result = greedy_assign(scored, v_idx, i_idx)
        assert "o0" in result
        assert "o1" in result
        # o0 takes (0,0); o1 must take (1,1) since implement 0 is claimed
        assert result["o0"] == (0, 0)
        assert result["o1"] == (1, 1)

    def test_no_implement_reuse(self):
        scored = {
            "o0": [(100.0, 0, 0)],
            "o1": [(95.0, 1, 0), (80.0, 1, 1)],  # both orders want implement 0
        }
        v_idx = {"v0": 0, "v1": 1}
        i_idx = {"i0": 0, "i1": 1}
        result = greedy_assign(scored, v_idx, i_idx)
        all_pairs = list(result.values())
        impl_ids = [p[1] for p in all_pairs]
        assert len(impl_ids) == len(set(impl_ids))

    def test_scarce_order_gets_implement_before_flexible_order(self):
        scored = {
            "flexible": [(100.0, 0, 0), (99.0, 0, 1)],
            "scarce": [(95.0, 1, 0)],
        }
        v_idx = {"v0": 0, "v1": 1}
        i_idx = {"i0": 0, "i1": 1}

        result = greedy_assign(scored, v_idx, i_idx)

        assert result["scarce"] == (1, 0)
        assert result["flexible"] == (0, 1)

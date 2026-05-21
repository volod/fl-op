"""T14-T15: Greedy warm-start tests."""

import numpy as np
import pytest

from fl_op.models.compat_matrix import build_compat_matrix
from fl_op.models.enums import ImplementType, VehicleType
from fl_op.models.implement import Implement
from fl_op.models.vehicle import Vehicle
from fl_op.solver.greedy import greedy_assign, vectorized_score


def _v(vid: str, lat: float = 48.5, lon: float = 32.0, power: float = 150.0) -> dict:
    return {"vehicle_id": vid, "vehicle_type": "TRACTOR", "rated_power_kw": str(power),
            "fuel_tank_l": "400", "fuel_consumption_l_per_h": "18",
            "current_lat": str(lat), "current_lon": str(lon),
            "depot_id": "d0", "travel_speed_kmh": "15"}


def _i(iid: str, power: float = 100.0) -> dict:
    return {"implement_id": iid, "implement_type": "SPRAYER",
            "compatible_operations": "['SPRAYING']", "required_power_kw": str(power),
            "working_width_m": "24", "min_speed_kmh": "5", "max_speed_kmh": "12",
            "fertilizer_capacity_kg": "0", "depot_id": "d0"}


def _o(oid: str, fid: str = "f0") -> dict:
    return {"order_id": oid, "operation_type": "SPRAYING", "field_id": fid,
            "area_ha": "100", "deadline": "2026-06-01T00:00:00+00:00",
            "penalty_per_day_eur": "200", "status": "pending",
            "estimated_revenue_eur": "5000", "contract_id": "c0"}


def _f(fid: str, lat: float, lon: float) -> dict:
    return {"field_id": fid, "centroid_lat": str(lat), "centroid_lon": str(lon),
            "area_ha": "100", "name": fid}


class TestVectorizedScore:
    def test_returns_all_order_ids(self):
        vehicles = [_v("v0"), _v("v1")]
        implements = [_i("i0"), _i("i1")]
        orders = [_o("o0"), _o("o1")]
        fields = [_f("f0", 48.5, 32.0), _f("f1", 48.6, 32.1)]
        vp = [Vehicle.model_validate(v) for v in vehicles]
        ip = [Implement.model_validate(im) for im in implements]
        compat, _ = build_compat_matrix(vp, ip)
        v_idx = {v["vehicle_id"]: i for i, v in enumerate(vehicles)}
        i_idx = {im["implement_id"]: i for i, im in enumerate(implements)}
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
        vp = [Vehicle.model_validate(v) for v in vehicles]
        ip = [Implement.model_validate(im) for im in implements]
        compat, _ = build_compat_matrix(vp, ip)
        v_idx = {v["vehicle_id"]: i for i, v in enumerate(vehicles)}
        i_idx = {im["implement_id"]: i for i, im in enumerate(implements)}
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
        assigned_implements = {vi[1] for vi in result.values()}
        # implement 0 should only appear once
        all_pairs = list(result.values())
        impl_ids = [p[1] for p in all_pairs]
        assert len(impl_ids) == len(set(impl_ids))

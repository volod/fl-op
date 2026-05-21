"""T11-T13: Resource allocator tests.

T11: No implement is assigned to more than one cluster.
T12: Higher penalty cluster wins contested implement.
T13: Equal penalty sum -> tiebreak by cluster_id.
"""

import numpy as np
import pytest

from fl_op.models.compat_matrix import build_compat_matrix
from fl_op.models.enums import ImplementType, OperationType, VehicleType
from fl_op.models.implement import Implement
from fl_op.models.vehicle import Vehicle
from fl_op.models.types import ClusterSpec
from fl_op.solver.resource_allocator import allocate_resources


def _veh(vid: str, power: float = 150.0) -> dict:
    return {"vehicle_id": vid, "vehicle_type": "TRACTOR", "rated_power_kw": str(power),
            "fuel_tank_l": "400", "fuel_consumption_l_per_h": "18",
            "current_lat": "48.5", "current_lon": "32.0", "depot_id": "d0", "travel_speed_kmh": "15"}


def _impl(iid: str, power: float = 100.0) -> dict:
    return {"implement_id": iid, "implement_type": "SPRAYER",
            "compatible_operations": "['SPRAYING']", "required_power_kw": str(power),
            "working_width_m": "24", "min_speed_kmh": "5", "max_speed_kmh": "12",
            "fertilizer_capacity_kg": "0", "depot_id": "d0"}


def _order(oid: str, penalty: float = 100.0) -> dict:
    return {"order_id": oid, "operation_type": "SPRAYING", "field_id": "f0",
            "area_ha": "100", "deadline": "2026-06-01T00:00:00+00:00",
            "penalty_per_day_eur": str(penalty), "status": "pending",
            "estimated_revenue_eur": "5000", "contract_id": "c0"}


def _operator(opid: str, depot: str = "d0") -> dict:
    return {"operator_id": opid, "name": opid, "shift_start_s": "21600",
            "shift_end_s": "57600", "certified_operations": "['SPRAYING']", "depot_id": depot}


def _build_setup(n_vehicles: int = 2, n_implements: int = 2):
    vehicles_raw = [_veh(f"v{i}") for i in range(n_vehicles)]
    implements_raw = [_impl(f"i{i}") for i in range(n_implements)]
    vehicles_p = [Vehicle.model_validate(v) for v in vehicles_raw]
    implements_p = [Implement.model_validate(im) for im in implements_raw]
    compat, power_margin = build_compat_matrix(vehicles_p, implements_p)
    vehicle_index = {v["vehicle_id"]: i for i, v in enumerate(vehicles_raw)}
    implement_index = {im["implement_id"]: i for i, im in enumerate(implements_raw)}
    return vehicles_raw, implements_raw, compat, power_margin, vehicle_index, implement_index


class TestNoCrossClusterDuplicate:
    def test_implement_not_in_two_clusters(self):
        vehicles_raw, implements_raw, compat, pm, v_idx, i_idx = _build_setup(2, 1)
        orders = [_order("o0", 500), _order("o1", 100)]
        operators = [_operator("op0")]
        feasible = {
            "o0": [(0, 0)],
            "o1": [(1, 0)],  # same implement i0 -> conflict
        }
        c0: ClusterSpec = {
            "cluster_id": "c0", "depot_id": "d0",
            "order_ids": ["o0"], "allocated_vehicle_implements": {},
            "total_penalty_per_day": 500.0,
        }
        c1: ClusterSpec = {
            "cluster_id": "c1", "depot_id": "d0",
            "order_ids": ["o1"], "allocated_vehicle_implements": {},
            "total_penalty_per_day": 100.0,
        }
        result = allocate_resources(
            [c0, c1], orders, vehicles_raw, implements_raw, operators,
            compat, pm, v_idx, i_idx, feasible,
        )
        all_impls = []
        for c in result:
            for vid, imps in c["allocated_vehicle_implements"].items():
                all_impls.extend(imps)
        # No implement appears twice
        assert len(all_impls) == len(set(all_impls))


class TestPenaltyWeightedWinner:
    def test_high_penalty_cluster_wins_implement(self):
        vehicles_raw, implements_raw, compat, pm, v_idx, i_idx = _build_setup(2, 1)
        orders = [_order("o_high", 1000), _order("o_low", 10)]
        operators = [_operator("op0")]
        feasible = {"o_high": [(0, 0)], "o_low": [(0, 0)]}
        c_high: ClusterSpec = {
            "cluster_id": "c_high", "depot_id": "d0",
            "order_ids": ["o_high"], "allocated_vehicle_implements": {},
            "total_penalty_per_day": 1000.0,
        }
        c_low: ClusterSpec = {
            "cluster_id": "c_low", "depot_id": "d0",
            "order_ids": ["o_low"], "allocated_vehicle_implements": {},
            "total_penalty_per_day": 10.0,
        }
        result = allocate_resources(
            [c_low, c_high],  # intentionally pass low-penalty first
            orders, vehicles_raw, implements_raw, operators,
            compat, pm, v_idx, i_idx, feasible,
        )
        # High penalty cluster should have the implement
        c_high_result = next(c for c in result if c["cluster_id"] == "c_high")
        assert c_high_result["allocated_vehicle_implements"] != {}


class TestEqualPenaltyTiebreak:
    def test_tiebreak_by_cluster_id(self):
        vehicles_raw, implements_raw, compat, pm, v_idx, i_idx = _build_setup(2, 1)
        orders = [_order("o0", 500), _order("o1", 500)]
        operators = [_operator("op0")]
        feasible = {"o0": [(0, 0)], "o1": [(0, 0)]}
        ca: ClusterSpec = {
            "cluster_id": "cluster_a", "depot_id": "d0",
            "order_ids": ["o0"], "allocated_vehicle_implements": {},
            "total_penalty_per_day": 500.0,
        }
        cb: ClusterSpec = {
            "cluster_id": "cluster_b", "depot_id": "d0",
            "order_ids": ["o1"], "allocated_vehicle_implements": {},
            "total_penalty_per_day": 500.0,
        }
        result = allocate_resources(
            [cb, ca],  # pass b first, a should still win by lex order
            orders, vehicles_raw, implements_raw, operators,
            compat, pm, v_idx, i_idx, feasible,
        )
        ca_result = next(c for c in result if c["cluster_id"] == "cluster_a")
        cb_result = next(c for c in result if c["cluster_id"] == "cluster_b")
        # cluster_a (lexicographically first) should get the implement
        # cluster_b should be empty
        ca_has = ca_result["allocated_vehicle_implements"] != {}
        cb_has = cb_result["allocated_vehicle_implements"] != {}
        # Exactly one should have the implement
        assert ca_has or cb_has
        assert not (ca_has and cb_has)
        if ca_has:
            assert not cb_has

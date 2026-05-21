"""T06-T10: Pre-filter + clustering tests."""

import numpy as np
import pytest

from fl_op.models.compat_matrix import build_compat_matrix
from fl_op.models.enums import ImplementType, OperationType, VehicleType
from fl_op.models.implement import Implement
from fl_op.models.vehicle import Vehicle
from fl_op.solver.preprocessing import (
    build_cluster_specs,
    cluster_orders_by_depot,
    filter_feasible_vehicle_implement_pairs,
)


def _vehicle(vid: str, power_kw: float = 150.0, depot: str = "d0") -> dict:
    return {
        "vehicle_id": vid,
        "vehicle_type": VehicleType.TRACTOR.value,
        "rated_power_kw": str(power_kw),
        "fuel_tank_l": "400",
        "fuel_consumption_l_per_h": "18",
        "current_lat": "48.5",
        "current_lon": "32.0",
        "depot_id": depot,
        "travel_speed_kmh": "15",
    }


def _implement(iid: str, power_kw: float = 120.0, op: str = "SPRAYING", depot: str = "d0") -> dict:
    return {
        "implement_id": iid,
        "implement_type": ImplementType.SPRAYER.value,
        "compatible_operations": f"['{op}']",
        "required_power_kw": str(power_kw),
        "working_width_m": "24",
        "min_speed_kmh": "5",
        "max_speed_kmh": "12",
        "fertilizer_capacity_kg": "0",
        "depot_id": depot,
    }


def _order(oid: str, op: str = "SPRAYING", fid: str = "f0") -> dict:
    return {
        "order_id": oid,
        "operation_type": op,
        "field_id": fid,
        "area_ha": "100",
        "deadline": "2026-06-01T00:00:00+00:00",
        "penalty_per_day_eur": "200",
        "status": "pending",
        "estimated_revenue_eur": "5000",
        "contract_id": "c0",
    }


def _field(fid: str, lat: float, lon: float) -> dict:
    return {
        "field_id": fid,
        "centroid_lat": str(lat),
        "centroid_lon": str(lon),
        "area_ha": "100",
        "name": fid,
    }


def _depot(did: str, lat: float, lon: float) -> dict:
    return {"depot_id": did, "lat": str(lat), "lon": str(lon), "name": did,
            "fuel_available_l": "5000", "fertilizer_available_kg": "0"}


class TestPowerFilter:
    def _setup(self, v_power: float, i_power: float):
        vraw = [_vehicle("v0", v_power)]
        iraw = [_implement("i0", i_power)]
        vehicles_p = [Vehicle.model_validate(v) for v in vraw]
        implements_p = [Implement.model_validate(im) for im in iraw]
        compat, _ = build_compat_matrix(vehicles_p, implements_p)
        v_idx = {"v0": 0}
        i_idx = {"i0": 0}
        return vraw, iraw, compat, v_idx, i_idx

    def test_compatible_pair_in_feasible(self):
        vraw, iraw, compat, v_idx, i_idx = self._setup(150, 120)
        orders = [_order("o0")]
        result = filter_feasible_vehicle_implement_pairs(orders, vraw, iraw, compat, v_idx, i_idx)
        assert len(result["o0"]) == 1
        assert result["o0"][0] == (0, 0)

    def test_overloaded_pair_excluded(self):
        vraw, iraw, compat, v_idx, i_idx = self._setup(80, 120)
        orders = [_order("o0")]
        result = filter_feasible_vehicle_implement_pairs(orders, vraw, iraw, compat, v_idx, i_idx)
        assert result["o0"] == []

    def test_operation_type_mismatch_excluded(self):
        vraw = [_vehicle("v0", 200)]
        iraw = [_implement("i0", 100, op="TILLAGE")]
        vehicles_p = [Vehicle.model_validate(v) for v in vraw]
        implements_p = [Implement.model_validate(im) for im in iraw]
        compat, _ = build_compat_matrix(vehicles_p, implements_p)
        orders = [_order("o0", op="SPRAYING")]  # wants SPRAYING, implement is TILLAGE
        result = filter_feasible_vehicle_implement_pairs(orders, vraw, iraw, compat, {"v0": 0}, {"i0": 0})
        assert result["o0"] == []


class TestDepotAffinityCluster:
    def test_nearest_depot_assignment(self):
        # Two depots at opposite corners; two fields each near one depot
        depots = [_depot("north", 50.0, 30.0), _depot("south", 46.0, 34.0)]
        fields = [
            _field("f_north", 50.1, 30.1),
            _field("f_south", 46.1, 34.1),
        ]
        orders = [_order("o_north", fid="f_north"), _order("o_south", fid="f_south")]
        assignment = cluster_orders_by_depot(orders, fields, depots)
        assert "o_north" in assignment["north"]
        assert "o_south" in assignment["south"]

    def test_all_orders_assigned(self):
        depots = [_depot("d0", 48.5, 32.0)]
        fields = [_field(f"f{i}", 48.5 + i * 0.1, 32.0) for i in range(5)]
        orders = [_order(f"o{i}", fid=f"f{i}") for i in range(5)]
        assignment = cluster_orders_by_depot(orders, fields, depots)
        assert sum(len(v) for v in assignment.values()) == 5

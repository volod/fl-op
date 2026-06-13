"""T06-T10: Pre-filter + clustering tests (canonical solver rows)."""

from fl_op.solver.feasibility import build_compat_matrix
from fl_op.solver.preprocessing import (
    build_cluster_specs,
    cluster_orders_by_depot,
    filter_feasible_vehicle_implement_pairs,
)
from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow, SiteRow, TaskRow


def _vehicle(vid: str, power_kw: float = 150.0, depot: str = "d0") -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict({
        "asset_id": vid,
        "asset_type": "TRACTOR",
        "rated_power": str(power_kw),
        "fuel_tank_volume": "400",
        "fuel_consumption_rate": "18",
        "lat": "48.5",
        "lon": "32.0",
        "home_depot_ref": depot,
        "travel_speed": "15",
    })


def _implement(iid: str, power_kw: float = 120.0, op: str = "SPRAYING", depot: str = "d0") -> RelatedRow:
    return RelatedRow.from_canonical_dict({
        "asset_id": iid,
        "asset_type": "SPRAYER",
        "compatible_operations": f"['{op}']",
        "required_power": str(power_kw),
        "working_width": "24",
        "min_speed": "5",
        "max_speed": "12",
        "material_capacity": "0",
        "home_depot_ref": depot,
    })


def _order(oid: str, op: str = "SPRAYING", fid: str = "f0") -> TaskRow:
    return TaskRow.from_canonical_dict({
        "task_id": oid,
        "operation_type": op,
        "location_ref": fid,
        "area": "100",
        "deadline": "2026-06-01T00:00:00+00:00",
        "penalty_per_day": "200",
        "status": "pending",
        "revenue": "5000",
        "order_ref": "c0",
    })


def _field(fid: str, lat: float, lon: float) -> SiteRow:
    return SiteRow.from_canonical_dict({
        "location_id": fid,
        "lat": str(lat),
        "lon": str(lon),
        "area": "100",
        "name": fid,
    })


def _depot(did: str, lat: float, lon: float) -> DepotRow:
    return DepotRow.from_canonical_dict(
        {"location_id": did, "lat": str(lat), "lon": str(lon), "name": did,
         "inventory_fuel": "5000", "inventory_material": "0"})


class TestPowerFilter:
    def _setup(self, v_power: float, i_power: float):
        vraw = [_vehicle("v0", v_power)]
        iraw = [_implement("i0", i_power)]
        compat, _ = build_compat_matrix(vraw, iraw)
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
        compat, _ = build_compat_matrix(vraw, iraw)
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

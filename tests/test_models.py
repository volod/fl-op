"""T01-T05: Data model tests."""

import numpy as np
import pytest
from pydantic import ValidationError

from fl_op.models.compat_matrix import build_compat_matrix
from fl_op.models.enums import ImplementType, OperationType, VehicleType
from fl_op.models.implement import Implement
from fl_op.models.vehicle import Vehicle


def _make_vehicle(power_kw: float = 150.0, vid: str = "v1") -> Vehicle:
    return Vehicle(
        vehicle_id=vid,
        vehicle_type=VehicleType.TRACTOR,
        rated_power_kw=power_kw,
        fuel_tank_l=400,
        fuel_consumption_l_per_h=20,
        current_lat=48.5,
        current_lon=32.0,
        depot_id="d1",
    )


def _make_implement(power_kw: float = 120.0, iid: str = "i1") -> Implement:
    return Implement(
        implement_id=iid,
        implement_type=ImplementType.SPRAYER,
        compatible_operations=[OperationType.SPRAYING],
        required_power_kw=power_kw,
        working_width_m=24,
        min_speed_kmh=5,
        max_speed_kmh=12,
        depot_id="d1",
    )


class TestEnums:
    def test_operation_type_values(self):
        assert OperationType.SPRAYING.value == "SPRAYING"
        assert OperationType.HARVESTING.value == "HARVESTING"

    def test_enum_comparison(self):
        # Enum comparison must not silently pass string mismatch
        assert OperationType.SPRAYING != OperationType.TILLAGE
        assert OperationType.SPRAYING == OperationType("SPRAYING")

    def test_enum_invalid_raises(self):
        with pytest.raises(ValueError):
            OperationType("spraying")  # lowercase invalid


class TestCompatMatrix:
    def test_shape(self):
        v = [_make_vehicle(150), _make_vehicle(200, "v2")]
        im = [_make_implement(120), _make_implement(180, "i2"), _make_implement(90, "i3")]
        compat, pm = build_compat_matrix(v, im)
        assert compat.shape == (2, 3)
        assert pm.shape == (2, 3)

    def test_dtype(self):
        v = [_make_vehicle(150)]
        im = [_make_implement(120)]
        compat, pm = build_compat_matrix(v, im)
        assert compat.dtype == bool
        assert pm.dtype == np.float32

    def test_compatible_pair(self):
        # 150 kW vehicle, 120 kW implement -> 20% headroom -> compatible
        v = [_make_vehicle(150)]
        im = [_make_implement(120)]
        compat, pm = build_compat_matrix(v, im)
        assert compat[0, 0] is np.bool_(True)
        assert pm[0, 0] == pytest.approx(20.0, abs=0.1)

    def test_overloaded_pair(self):
        # 100 kW vehicle, 120 kW implement -> -20% -> incompatible (beyond 10% margin)
        v = [_make_vehicle(100)]
        im = [_make_implement(120)]
        compat, pm = build_compat_matrix(v, im)
        assert compat[0, 0] is np.bool_(False)
        assert pm[0, 0] < 0

    def test_within_margin_pair(self):
        # 100 kW vehicle, 108 kW implement -> -8% -> within POWER_MARGIN_PCT=10% margin
        v = [_make_vehicle(100)]
        im = [_make_implement(108)]
        compat, pm = build_compat_matrix(v, im)
        assert compat[0, 0] is np.bool_(True)


class TestPydanticModels:
    def test_vehicle_invalid_power(self):
        with pytest.raises(ValidationError):
            Vehicle(
                vehicle_id="x",
                vehicle_type=VehicleType.TRACTOR,
                rated_power_kw=-1,
                fuel_tank_l=400,
                fuel_consumption_l_per_h=20,
                current_lat=48.5,
                current_lon=32.0,
                depot_id="d1",
            )

    def test_vehicle_json_roundtrip(self):
        v = _make_vehicle()
        v2 = Vehicle.model_validate_json(v.model_dump_json())
        assert v2.vehicle_id == v.vehicle_id
        assert v2.rated_power_kw == v.rated_power_kw

    def test_model_rebuild_no_error(self):
        # models/__init__.py calls model_rebuild(); verify no ImportError
        from fl_op.models import Contract, Order  # noqa: F401

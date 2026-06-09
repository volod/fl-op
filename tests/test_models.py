"""T01-T05: Domain enum + canonical compatibility-matrix tests.

The former Pydantic domain models (Vehicle/Implement/...) were removed when the
solver became domain-agnostic. Compatibility is now computed directly from
canonical solver rows by fl_op.solver.feasibility; operation/implement/vehicle
type vocabularies live with the data generator.
"""

import numpy as np
import pytest

from fl_op.data.agri_enums import ImplementType, OperationType, VehicleType
from fl_op.solver.feasibility import build_compat_matrix


def _make_vehicle(power_kw: float = 150.0, vid: str = "v1") -> dict:
    return {"asset_id": vid, "rated_power": power_kw}


def _make_implement(power_kw: float = 120.0, iid: str = "i1") -> dict:
    return {"asset_id": iid, "required_power": power_kw}


class TestEnums:
    def test_operation_type_values(self):
        assert OperationType.SPRAYING.value == "SPRAYING"
        assert OperationType.HARVESTING.value == "HARVESTING"

    def test_enum_comparison(self):
        assert OperationType.SPRAYING != OperationType.TILLAGE
        assert OperationType.SPRAYING == OperationType("SPRAYING")

    def test_enum_invalid_raises(self):
        with pytest.raises(ValueError):
            OperationType("spraying")  # lowercase invalid

    def test_vehicle_and_implement_type_vocab(self):
        assert VehicleType.TRACTOR.value == "TRACTOR"
        assert ImplementType.SPRAYER.value == "SPRAYER"


class TestCompatMatrix:
    def test_shape(self):
        v = [_make_vehicle(150), _make_vehicle(200, "v2")]
        im = [_make_implement(120), _make_implement(180, "i2"), _make_implement(90, "i3")]
        compat, pm = build_compat_matrix(v, im)
        assert compat.shape == (2, 3)
        assert pm.shape == (2, 3)

    def test_dtype(self):
        compat, pm = build_compat_matrix([_make_vehicle(150)], [_make_implement(120)])
        assert compat.dtype == bool
        assert pm.dtype == np.float32

    def test_compatible_pair(self):
        # 150 kW prime mover, 120 kW related -> 20% headroom -> compatible
        compat, pm = build_compat_matrix([_make_vehicle(150)], [_make_implement(120)])
        assert compat[0, 0] is np.bool_(True)
        assert pm[0, 0] == pytest.approx(20.0, abs=0.1)

    def test_overloaded_pair(self):
        # 100 kW prime mover, 120 kW related -> -20% -> incompatible (beyond 10% margin)
        compat, pm = build_compat_matrix([_make_vehicle(100)], [_make_implement(120)])
        assert compat[0, 0] is np.bool_(False)
        assert pm[0, 0] < 0

    def test_within_margin_pair(self):
        # 100 kW prime mover, 108 kW related -> -8% -> within POWER_MARGIN_PCT=10% margin
        compat, pm = build_compat_matrix([_make_vehicle(100)], [_make_implement(108)])
        assert compat[0, 0] is np.bool_(True)

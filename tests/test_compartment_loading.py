"""Multi-compartment loadCapacities: generation, mapping, and projection.

The drone-logistics domain already declares per-material load demand
(`parcel`, `meal`) on delivery orders; these tests cover the matching vehicle
side — per-material compartment capacities flowing from the generator through
the domain contract/mapping into the canonical asset and the solver row.
"""

import numpy as np

from fl_op.data.drone_logistics_entities import (
    _compartment_capacities,
    _generate_uavs,
    _generate_ugvs,
)
from fl_op.mapping.engine import MappingEngine

_LOAD_CAPACITIES_TERM = "urn:xopt:capability:load-capacities"


def _complete_uav(**overrides):
    row = {
        "uav_id": "UAV_X", "name": "courier", "vehicle_class": "UAV",
        "rated_power_kw": 12.0,
        "energy_capacity_l_equiv": 0.6, "energy_use_l_per_h": 0.1,
        "energy_resource_type": "electricity", "energy_unit": "kWh",
        "battery_capacity_kwh": 18.0, "energy_use_kwh_per_h": 2.0,
        "current_lat": 50.0, "current_lon": 30.0, "hub_id": "HUB_0",
        "travel_speed_kmh": 80.0, "payload_capacity_kg": 14.0,
        "load_capacities_kg": {"parcel": 14.0, "meal": 5.0},
        "compatible_operations": ["UAV_DELIVERY"],
    }
    row.update(overrides)
    return row


class TestCompartmentPartition:
    def test_parcel_compartment_equals_payload_meal_is_smaller(self):
        comp = _compartment_capacities(240.0)
        assert comp["parcel"] == 240.0
        assert comp["meal"] == 72.0  # 0.3 * payload

    def test_meal_compartment_never_exceeds_payload(self):
        # Tiny vehicles: the meal box is capped at the payload, never above it.
        comp = _compartment_capacities(5.0)
        assert comp["parcel"] == 5.0
        assert comp["meal"] <= comp["parcel"]
        assert comp["meal"] >= 3.0  # floor covers a single meal order


class TestCompartmentGeneration:
    def _tuning(self):
        return {
            "payloadCapacityClassesKg": {
                "UAV": {"light": 5.0, "heavy": 14.0},
                "UGV": {"small": 80.0, "large": 240.0},
            },
            "ugvRoadSpeedBucketsKmh": {"arterial": 26.0},
        }

    def _hubs(self):
        return [{"hub_id": "HUB_0", "lat": 50.0, "lon": 30.0}]

    def test_uav_generator_emits_compartment_map(self):
        rows = _generate_uavs(np.random.default_rng(0), 3, self._hubs(), self._tuning())
        for row in rows:
            comp = row["load_capacities_kg"]
            assert set(comp) == {"parcel", "meal"}
            assert comp["parcel"] == row["payload_capacity_kg"]
            assert comp["meal"] <= comp["parcel"]

    def test_ugv_generator_emits_compartment_map(self):
        rows = _generate_ugvs(np.random.default_rng(0), 3, self._hubs(), self._tuning())
        for row in rows:
            comp = row["load_capacities_kg"]
            assert set(comp) == {"parcel", "meal"}
            assert comp["parcel"] == row["payload_capacity_kg"]


class TestCompartmentMapping:
    def test_uav_mapping_carries_load_capacities(self):
        res = MappingEngine().map_dataset("uavs", [_complete_uav()])
        assert not res.excluded.get("uavs"), res.findings
        asset = res.assets[0]
        assert asset.capability_value(_LOAD_CAPACITIES_TERM) == {
            "parcel": 14.0, "meal": 5.0,
        }

    def test_stringified_compartment_map_parses(self):
        # CSV round-trips the object column as a stringified dict.
        row = _complete_uav(load_capacities_kg="{'parcel': 14.0, 'meal': 5.0}")
        res = MappingEngine().map_dataset("uavs", [row])
        assert res.assets[0].capability_value(_LOAD_CAPACITIES_TERM) == {
            "parcel": 14.0, "meal": 5.0,
        }

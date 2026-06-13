"""Contract tests for the frozen canonical solver-row dataclasses.

These guard the typed boundary introduced in solver/types.py: construction via
from_canonical_dict (unknown-key filtering, constant-backed defaults), frozen
immutability, the slots memory contract, and pickle safety across the
ProcessPoolExecutor(spawn) boundary in cluster_pool.
"""

import dataclasses
import pickle

import pytest

from fl_op.core.constants import (
    FUEL_CONSUMPTION_DEFAULT_L_PER_H,
    RELATED_OPERATING_SPEED_DEFAULT,
    RELATED_WORKING_WIDTH_DEFAULT,
    TRAVEL_SPEED_DEFAULT_KMH,
)
from fl_op.solver.types import (
    DepotRow,
    OperatorRow,
    PrimeMoverRow,
    RelatedRow,
    SiteRow,
    TaskRow,
    TravelLinkRow,
)

_ALL_ROWS = (PrimeMoverRow, RelatedRow, OperatorRow, SiteRow, DepotRow, TaskRow)

_REQUIRED_ID = {
    PrimeMoverRow: "asset_id",
    RelatedRow: "asset_id",
    OperatorRow: "asset_id",
    SiteRow: "location_id",
    DepotRow: "location_id",
    TaskRow: "task_id",
}


def _minimal(cls):
    """Smallest valid payload for a row: just its required id field."""
    return cls.from_canonical_dict({_REQUIRED_ID[cls]: "x1"})


class TestConstruction:
    @pytest.mark.parametrize("cls", _ALL_ROWS)
    def test_builds_from_id_only(self, cls):
        row = _minimal(cls)
        assert getattr(row, _REQUIRED_ID[cls]) == "x1"

    @pytest.mark.parametrize("cls", _ALL_ROWS)
    def test_unknown_keys_are_dropped(self, cls):
        row = cls.from_canonical_dict(
            {_REQUIRED_ID[cls]: "x1", "not_a_field": "junk", "another": 99}
        )
        assert not hasattr(row, "not_a_field")
        assert not hasattr(row, "another")

    def test_present_value_overrides_default(self):
        row = PrimeMoverRow.from_canonical_dict({"asset_id": "v1", "travel_speed": 7.5})
        assert row.travel_speed == 7.5

    def test_task_alternative_group_projects(self):
        row = TaskRow.from_canonical_dict({
            "task_id": "delivery_1-UAV",
            "alternative_group_ref": "delivery_1",
        })
        assert row.alternative_group_ref == "delivery_1"

    def test_travel_link_mode_projects(self):
        row = TravelLinkRow.from_canonical_dict({
            "link_id": "l1",
            "network_mode": "air",
        })
        assert row.network_mode == "air"


class TestDefaultsComeFromConstants:
    def test_prime_mover_defaults(self):
        row = PrimeMoverRow.from_canonical_dict({"asset_id": "v1"})
        assert row.travel_speed == TRAVEL_SPEED_DEFAULT_KMH
        assert row.fuel_consumption_rate == FUEL_CONSUMPTION_DEFAULT_L_PER_H

    def test_related_defaults(self):
        row = RelatedRow.from_canonical_dict({"asset_id": "i1"})
        assert row.working_width == RELATED_WORKING_WIDTH_DEFAULT
        assert row.max_speed == RELATED_OPERATING_SPEED_DEFAULT

    def test_task_defaults(self):
        row = TaskRow.from_canonical_dict({"task_id": "t1"})
        assert row.area == 0.0
        assert row.revenue == 0.0
        assert row.deadline is None


class TestFrozen:
    @pytest.mark.parametrize("cls", _ALL_ROWS)
    def test_mutation_raises(self, cls):
        row = _minimal(cls)
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(row, _REQUIRED_ID[cls], "mutated")


class TestSlots:
    @pytest.mark.parametrize("cls", _ALL_ROWS)
    def test_no_instance_dict(self, cls):
        # slots=True: rows carry no per-instance __dict__ (memory/pickle win).
        assert not hasattr(_minimal(cls), "__dict__")

    @pytest.mark.parametrize("cls", _ALL_ROWS)
    def test_declares_slots(self, cls):
        # Every declared field lives in __slots__; nothing else can be stored.
        slot_names = set()
        for klass in cls.__mro__:
            slot_names.update(getattr(klass, "__slots__", ()))
        for f in dataclasses.fields(cls):
            assert f.name in slot_names


class TestPickleRoundTrip:
    @pytest.mark.parametrize("cls", _ALL_ROWS)
    def test_pickles_unchanged(self, cls):
        # cluster_pool ships rows to spawn workers; pickling must round-trip.
        row = _minimal(cls)
        assert pickle.loads(pickle.dumps(row)) == row


class TestFactoryDefaultsAreIndependent:
    def test_compatible_operations_not_shared(self):
        a = RelatedRow.from_canonical_dict({"asset_id": "i1"})
        b = RelatedRow.from_canonical_dict({"asset_id": "i2"})
        assert a.compatible_operations == [] and b.compatible_operations == []
        assert a.compatible_operations is not b.compatible_operations

    def test_prime_mover_compatible_operations_not_shared(self):
        a = PrimeMoverRow.from_canonical_dict({"asset_id": "v1"})
        b = PrimeMoverRow.from_canonical_dict({"asset_id": "v2"})
        assert a.compatible_operations == [] and b.compatible_operations == []
        assert a.compatible_operations is not b.compatible_operations

    def test_certified_operations_not_shared(self):
        a = OperatorRow.from_canonical_dict({"asset_id": "op1"})
        b = OperatorRow.from_canonical_dict({"asset_id": "op2"})
        assert a.certified_operations is not b.certified_operations

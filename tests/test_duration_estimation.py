"""Quantity-driven service-duration estimation with per-unit work rates.

The estimate's precedence: explicit service duration, then a declared work
rate for the quantity's unit (any unit kind), then the width-times-speed
coverage model for area-like quantities, then the nominal effort.
"""

from fl_op.mapping.records import parse_rate_map
from fl_op.solver.travel_time import (
    _OP_HOURS_FALLBACK,
    _OP_HOURS_MAX,
    _OP_HOURS_MIN,
    _estimate_operation_seconds,
)
from fl_op.solver.types import RelatedRow, TaskRow


def _task(**overrides) -> TaskRow:
    data = {"task_id": "task_1"}
    data.update(overrides)
    return TaskRow.from_canonical_dict(data)


def _implement(**overrides) -> RelatedRow:
    data = {"asset_id": "impl_1"}
    data.update(overrides)
    return RelatedRow.from_canonical_dict(data)


class TestWorkRateDriven:
    def test_volume_quantity_uses_declared_rate(self):
        order = _task(work_quantity=300.0, work_quantity_unit="m3")
        implement = _implement(work_rates={"m3": 100.0})
        assert _estimate_operation_seconds(order, implement) == 3 * 3600

    def test_item_quantity_uses_declared_rate(self):
        order = _task(work_quantity=24.0, work_quantity_unit="items")
        implement = _implement(work_rates={"items": 6.0})
        assert _estimate_operation_seconds(order, implement) == 4 * 3600

    def test_area_rate_overrides_coverage_model(self):
        order = _task(work_quantity=20.0, work_quantity_unit="ha")
        implement = _implement(
            work_rates={"ha": 10.0}, working_width=6000.0, max_speed=10.0
        )
        # The coverage model would clamp to the minimum; the rate gives 2 h.
        assert _estimate_operation_seconds(order, implement) == 2 * 3600

    def test_legacy_area_alias_matches_ha_rate(self):
        order = _task(area=20.0)
        implement = _implement(work_rates={"ha": 10.0})
        assert _estimate_operation_seconds(order, implement) == 2 * 3600

    def test_rate_estimate_is_clamped(self):
        small = _task(work_quantity=1.0, work_quantity_unit="m3")
        fast = _implement(work_rates={"m3": 1000.0})
        assert _estimate_operation_seconds(small, fast) == int(_OP_HOURS_MIN * 3600)
        big = _task(work_quantity=100000.0, work_quantity_unit="m3")
        slow = _implement(work_rates={"m3": 1.0})
        assert _estimate_operation_seconds(big, slow) == int(_OP_HOURS_MAX * 3600)


class TestFallbacks:
    def test_non_area_unit_without_rate_falls_back_to_nominal(self):
        order = _task(work_quantity=300.0, work_quantity_unit="m3")
        implement = _implement(work_rates={})
        assert _estimate_operation_seconds(order, implement) == int(
            _OP_HOURS_FALLBACK * 3600
        )

    def test_area_without_rate_keeps_coverage_model(self):
        order = _task(work_quantity=12.0, work_quantity_unit="ha")
        implement = _implement(working_width=12.0, max_speed=10.0)
        expected_hours = 12.0 / (12.0 / 1000 * 10.0 * 10)
        assert _estimate_operation_seconds(order, implement) == int(
            expected_hours * 3600
        )

    def test_explicit_duration_wins_over_rate(self):
        order = _task(
            work_quantity=300.0, work_quantity_unit="m3", service_duration_min=90.0
        )
        implement = _implement(work_rates={"m3": 100.0})
        assert _estimate_operation_seconds(order, implement) == 90 * 60


class TestRateMapParsing:
    def test_parses_stringified_map(self):
        assert parse_rate_map('{"m3": 90.0}') == {"m3": 90.0}

    def test_drops_non_positive_and_non_numeric_rates(self):
        assert parse_rate_map({"m3": 0.0, "items": "fast", "ha": 5}) == {"ha": 5.0}

    def test_garbage_yields_empty_map(self):
        assert parse_rate_map("not a map") == {}
        assert parse_rate_map(None) == {}
        assert parse_rate_map(3.5) == {}

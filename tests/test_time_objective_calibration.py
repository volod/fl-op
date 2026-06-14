"""Unit tests for time-objective urgency calibration helpers.

These cover the deterministic per-task weight scaling used when
TIME_OBJECTIVE_URGENCY_CALIBRATION is enabled. The helpers are pure functions
that read calibration knobs from fl_op.core.constants at call time, so tests
monkeypatch those module attributes to exercise each branch in isolation.
"""

from types import SimpleNamespace

import pytest

from fl_op.core import constants
from fl_op.solver.cluster import routing


def _order(deadline="", priority_class=""):
    return SimpleNamespace(deadline=deadline, priority_class=priority_class)


class TestCoercePriorityClass:
    def test_numeric_string_parses(self):
        assert routing._coerce_priority_class(_order(priority_class="3")) == 3

    def test_numeric_string_with_whitespace_parses(self):
        assert routing._coerce_priority_class(_order(priority_class="  7 ")) == 7

    def test_blank_returns_none(self):
        assert routing._coerce_priority_class(_order(priority_class="")) is None

    def test_non_numeric_returns_none(self):
        assert routing._coerce_priority_class(_order(priority_class="gold")) is None

    def test_missing_attribute_returns_none(self):
        assert routing._coerce_priority_class(SimpleNamespace()) is None


class TestCompletionWeightForOrder:
    BASE_WEIGHT = 10

    @pytest.fixture(autouse=True)
    def _enable_calibration(self, monkeypatch):
        monkeypatch.setattr(constants, "TIME_OBJECTIVE_URGENCY_CALIBRATION", True)
        monkeypatch.setattr(constants, "TIME_OBJECTIVE_BASELINE_PRIORITY_CLASS", 5)
        monkeypatch.setattr(constants, "TIME_OBJECTIVE_CLASS_WEIGHT_STEP", 1)
        monkeypatch.setattr(constants, "TIME_OBJECTIVE_SLACK_REFERENCE_S", 7 * 24 * 3600)
        monkeypatch.setattr(constants, "TIME_OBJECTIVE_SLACK_WEIGHT_BONUS", 4)

    def test_calibration_disabled_returns_base_weight(self, monkeypatch):
        monkeypatch.setattr(constants, "TIME_OBJECTIVE_URGENCY_CALIBRATION", False)
        order = _order(priority_class="1", deadline="")
        assert (
            routing._completion_weight_for_order(order, 0, self.BASE_WEIGHT)
            == self.BASE_WEIGHT
        )

    def test_baseline_class_no_boost(self):
        order = _order(priority_class="5", deadline="")
        assert (
            routing._completion_weight_for_order(order, 0, self.BASE_WEIGHT)
            == self.BASE_WEIGHT
        )

    def test_lower_class_than_baseline_no_boost(self):
        order = _order(priority_class="9", deadline="")
        assert (
            routing._completion_weight_for_order(order, 0, self.BASE_WEIGHT)
            == self.BASE_WEIGHT
        )

    def test_higher_priority_class_adds_steps(self):
        # class 1 vs baseline 5 -> deficit 4 -> 4 urgency steps
        order = _order(priority_class="1", deadline="")
        expected = self.BASE_WEIGHT * (1 + 4)
        assert (
            routing._completion_weight_for_order(order, 0, self.BASE_WEIGHT) == expected
        )

    def test_class_step_scales_boost(self, monkeypatch):
        monkeypatch.setattr(constants, "TIME_OBJECTIVE_CLASS_WEIGHT_STEP", 3)
        # class 4 vs baseline 5 -> deficit 1 -> 1 * 3 = 3 steps
        order = _order(priority_class="4", deadline="")
        expected = self.BASE_WEIGHT * (1 + 3)
        assert (
            routing._completion_weight_for_order(order, 0, self.BASE_WEIGHT) == expected
        )

    def test_tight_deadline_adds_full_bonus(self):
        # Deadline at "now" -> zero slack -> full slack bonus (4 steps)
        now_epoch = 1_000_000
        from datetime import datetime, timezone

        deadline = datetime.fromtimestamp(now_epoch, tz=timezone.utc).isoformat()
        order = _order(priority_class="", deadline=deadline)
        expected = self.BASE_WEIGHT * (1 + 4)
        assert (
            routing._completion_weight_for_order(order, now_epoch, self.BASE_WEIGHT)
            == expected
        )

    def test_far_deadline_no_slack_bonus(self):
        # Deadline beyond reference window -> no slack bonus
        now_epoch = 1_000_000
        from datetime import datetime, timezone

        far = now_epoch + 30 * 24 * 3600
        deadline = datetime.fromtimestamp(far, tz=timezone.utc).isoformat()
        order = _order(priority_class="", deadline=deadline)
        assert (
            routing._completion_weight_for_order(order, now_epoch, self.BASE_WEIGHT)
            == self.BASE_WEIGHT
        )

    def test_class_and_slack_stack(self):
        # class 3 (deficit 2 -> 2 steps) + zero-slack deadline (4 steps) = 6 steps
        now_epoch = 1_000_000
        from datetime import datetime, timezone

        deadline = datetime.fromtimestamp(now_epoch, tz=timezone.utc).isoformat()
        order = _order(priority_class="3", deadline=deadline)
        expected = self.BASE_WEIGHT * (1 + 2 + 4)
        assert (
            routing._completion_weight_for_order(order, now_epoch, self.BASE_WEIGHT)
            == expected
        )

    def test_invalid_deadline_treated_as_full_horizon(self):
        # Unparseable deadline -> _deadline_from_now_s returns the routing horizon,
        # which is >= reference, so no slack bonus is applied.
        order = _order(priority_class="", deadline="not-a-date")
        assert (
            routing._completion_weight_for_order(order, 0, self.BASE_WEIGHT)
            == self.BASE_WEIGHT
        )

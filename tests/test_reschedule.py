"""T28-T29: Reschedule pipeline tests.

Covers the pure helper functions (_apply_events, _build_plan_diff,
_write_plan_diff_txt) and the status-validation logic, without invoking
the full reschedule pipeline (which spawns a process pool).
"""

import pytest

from fl_op.solver.reschedule import (
    _apply_events,
    _build_plan_diff,
    _write_plan_diff_txt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _order(oid, status="pending"):
    # Raw physical order row (events are keyed by physical order_id).
    return {"order_id": oid, "status": status}


def _dp(oid, vid="v0", start="2026-06-01T06:00:00+00:00"):
    # Dispatch package uses canonical keys.
    return {
        "task_id": oid, "prime_asset_id": vid, "scheduled_start": start,
        "cluster_id": "cl0", "depot_ref": "d0", "related_asset_id": "i0",
        "operator_asset_id": "op0", "scheduled_end": "2026-06-01T10:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# _apply_events  (operates on raw physical order rows)
# ---------------------------------------------------------------------------


class TestApplyEvents:
    def test_mark_started_updates_status(self):
        orders = [_order("o0")]
        result = _apply_events(orders, [{"type": "mark_started", "order_id": "o0"}])
        assert result[0]["status"] == "started"

    def test_unknown_event_type_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown event type"):
            _apply_events([_order("o0")], [{"type": "teleport", "order_id": "o0"}])

    def test_mark_started_unknown_order_id_logs_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="fl_op.solver.reschedule"):
            result = _apply_events([_order("o0")], [{"type": "mark_started", "order_id": "ghost"}])
        assert result[0]["status"] == "pending"
        assert any("ghost" in r.message for r in caplog.records)

    def test_empty_events_returns_orders_unchanged(self):
        orders = [_order("o0", "pending"), _order("o1", "started")]
        result = _apply_events(orders, [])
        assert result[0]["status"] == "pending"
        assert result[1]["status"] == "started"

    def test_multiple_mark_started_in_sequence(self):
        orders = [_order("o0"), _order("o1")]
        events = [
            {"type": "mark_started", "order_id": "o0"},
            {"type": "mark_started", "order_id": "o1"},
        ]
        result = _apply_events(orders, events)
        assert result[0]["status"] == "started"
        assert result[1]["status"] == "started"


# ---------------------------------------------------------------------------
# _build_plan_diff  (operates on canonical dispatch schedules)
# ---------------------------------------------------------------------------


class TestBuildPlanDiff:
    def test_required_schema_keys_present(self):
        diff = _build_plan_diff([], [], set(), set())
        for key in ("schema_version", "frozen_orders", "added", "removed",
                    "rescheduled", "newly_infeasible"):
            assert key in diff, f"plan_diff missing key: {key}"

    def test_new_order_classified_as_added(self):
        diff = _build_plan_diff(old_schedule=[], new_schedule=[_dp("o_new")],
                                frozen_task_ids=set(), infeasible_task_ids=set())
        assert any(d["task_id"] == "o_new" for d in diff["added"])
        assert diff["removed"] == []

    def test_disappeared_order_classified_as_removed(self):
        diff = _build_plan_diff(old_schedule=[_dp("o_gone")], new_schedule=[],
                                frozen_task_ids=set(), infeasible_task_ids=set())
        assert any(d["task_id"] == "o_gone" for d in diff["removed"])

    def test_frozen_order_excluded_from_removed(self):
        diff = _build_plan_diff(
            old_schedule=[_dp("o_frozen")], new_schedule=[],
            frozen_task_ids={"o_frozen"}, infeasible_task_ids=set(),
        )
        assert not any(d["task_id"] == "o_frozen" for d in diff["removed"])
        assert "o_frozen" in diff["frozen_orders"]

    def test_vehicle_change_classified_as_rescheduled(self):
        diff = _build_plan_diff(
            old_schedule=[_dp("o0", vid="v_old")],
            new_schedule=[_dp("o0", vid="v_new")],
            frozen_task_ids=set(), infeasible_task_ids=set(),
        )
        assert any(r["task_id"] == "o0" for r in diff["rescheduled"])

    def test_time_change_classified_as_rescheduled(self):
        diff = _build_plan_diff(
            old_schedule=[_dp("o0", start="2026-06-01T06:00:00+00:00")],
            new_schedule=[_dp("o0", start="2026-06-01T09:00:00+00:00")],
            frozen_task_ids=set(), infeasible_task_ids=set(),
        )
        assert any(r["task_id"] == "o0" for r in diff["rescheduled"])

    def test_unchanged_order_not_rescheduled(self):
        dp = _dp("o0")
        diff = _build_plan_diff([dp], [dp], set(), set())
        assert not any(r["task_id"] == "o0" for r in diff["rescheduled"])

    def test_newly_infeasible_recorded(self):
        diff = _build_plan_diff([], [], set(), infeasible_task_ids={"o_inf"})
        assert "o_inf" in diff["newly_infeasible"]

    def test_rescheduled_item_has_from_and_to(self):
        old = _dp("o0", vid="v_old")
        new = _dp("o0", vid="v_new")
        diff = _build_plan_diff([old], [new], set(), set())
        reschedule = next(r for r in diff["rescheduled"] if r["task_id"] == "o0")
        assert "from" in reschedule
        assert "to" in reschedule
        assert reschedule["from"]["prime_asset_id"] == "v_old"
        assert reschedule["to"]["prime_asset_id"] == "v_new"


# ---------------------------------------------------------------------------
# _write_plan_diff_txt — output format
# ---------------------------------------------------------------------------


class TestWritePlanDiffTxt:
    def _diff(self, frozen=0, added=0, removed=0, rescheduled=None, infeasible=0):
        return {
            "frozen_orders": ["f"] * frozen,
            "added": ["a"] * added,
            "removed": ["r"] * removed,
            "rescheduled": rescheduled or [],
            "newly_infeasible": ["i"] * infeasible,
        }

    def test_summary_counts_in_output(self, tmp_path):
        diff = self._diff(frozen=2, added=1, removed=3, infeasible=4)
        path = tmp_path / "diff.txt"
        _write_plan_diff_txt(diff, path)
        text = path.read_text()
        assert "Frozen (started):   2" in text
        assert "Newly added:        1" in text
        assert "Removed:            3" in text
        assert "Newly infeasible:   4" in text

    def test_rescheduled_section_included(self, tmp_path):
        diff = self._diff(rescheduled=[{
            "task_id": "o42",
            "from": {"prime_asset_id": "v_old"},
            "to": {"prime_asset_id": "v_new"},
        }])
        path = tmp_path / "diff.txt"
        _write_plan_diff_txt(diff, path)
        text = path.read_text()
        assert "o42" in text
        assert "v_old" in text
        assert "v_new" in text

    def test_file_created_and_non_empty(self, tmp_path):
        path = tmp_path / "diff.txt"
        _write_plan_diff_txt(self._diff(), path)
        assert path.exists()
        assert path.stat().st_size > 0


# ---------------------------------------------------------------------------
# Status validation (canonical TaskStatus)
# ---------------------------------------------------------------------------


class TestOrderStatusValidation:
    def test_unknown_status_raises_value_error(self):
        from fl_op.canonical.enums import TaskStatus
        with pytest.raises(ValueError):
            TaskStatus("flying")

    def test_started_status_is_valid(self):
        from fl_op.canonical.enums import TaskStatus
        assert TaskStatus("started") == TaskStatus.STARTED

    def test_pending_status_is_valid(self):
        from fl_op.canonical.enums import TaskStatus
        assert TaskStatus("pending") == TaskStatus.PENDING

    def test_completed_status_is_valid(self):
        from fl_op.canonical.enums import TaskStatus
        assert TaskStatus("completed") == TaskStatus.COMPLETED

    def test_infeasible_status_is_valid(self):
        from fl_op.canonical.enums import TaskStatus
        assert TaskStatus("infeasible") == TaskStatus.INFEASIBLE

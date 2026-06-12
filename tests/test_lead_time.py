"""Completion lead-time measurement: records and distribution stats."""

from datetime import datetime, timezone

import pytest

from fl_op.canonical.common import VersionDimensions
from fl_op.canonical.enums import PlanningMode
from fl_op.canonical.plan import Assignment, Plan
from fl_op.stream.lead_time import lead_time_stats, record_completions

_TS = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)


def _previous_plan() -> Plan:
    return Plan(
        plan_id="plan-1",
        revision_id="rev-1",
        origin_plan_id="plan-1",
        planning_mode=PlanningMode.ROLLING,
        snapshot_id="snap-0",
        version_dimensions=VersionDimensions(),
        adapter_id="ortools-rolling",
        adapter_version="0.1.0",
        generated_at=_TS,
        effective_from=_TS,
        assignments=[
            Assignment(
                assignment_id="a-1",
                task_id="order_1",
                bundle_id="b-1",
                planned_start=_TS,
                planned_finish=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
            )
        ],
    )


def test_records_measure_deadline_lead_and_schedule_error(tmp_path) -> None:
    log = tmp_path / "lead.jsonl"
    completions = [
        {
            "task_id": "order_1",
            "completed_at": "2026-06-05T15:00:00+00:00",
            "deadline": "2026-06-07T00:00:00+00:00",
            "via": "event",
        }
    ]
    records = record_completions(completions, _previous_plan(), log)
    assert len(records) == 1
    record = records[0]
    # Finished 33 h before the deadline, one hour behind the planned finish.
    assert record["lead_time_s"] == pytest.approx(33 * 3600)
    assert record["schedule_error_s"] == pytest.approx(3600)
    assert record["is_service"] is False
    assert log.exists()


def test_service_completions_split_into_forecast_lead_time(tmp_path) -> None:
    log = tmp_path / "lead.jsonl"
    record_completions(
        [
            {
                "task_id": "service-sensor_1",
                "completed_at": "2026-06-05T15:00:00+00:00",
                "deadline": "2026-06-05T12:00:00+00:00",  # 3 h late
                "via": "event",
            },
            {
                "task_id": "order_9",
                "completed_at": "2026-06-05T15:00:00+00:00",
                "deadline": "2026-06-05T16:00:00+00:00",  # 1 h lead
                "via": "telemetry",
            },
        ],
        None,
        log,
    )
    stats = lead_time_stats(log)
    assert stats["n_completions"] == 2
    assert stats["n_with_lead"] == 2
    assert stats["late_share"] == pytest.approx(0.5)
    assert stats["n_service_completions"] == 1
    assert stats["mean_service_lead_s"] == pytest.approx(-3 * 3600)


def test_completions_without_deadline_still_count(tmp_path) -> None:
    log = tmp_path / "lead.jsonl"
    record_completions(
        [{"task_id": "order_2", "completed_at": "2026-06-05T15:00:00+00:00",
          "deadline": None, "via": "progress"}],
        None,
        log,
    )
    stats = lead_time_stats(log)
    assert stats == {"n_completions": 1, "n_with_lead": 0}


def test_no_log_yields_empty_stats(tmp_path) -> None:
    assert lead_time_stats(tmp_path / "absent.jsonl") == {}

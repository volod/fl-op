"""Service-prognosis accuracy feedback."""

import pathlib
from datetime import datetime, timezone

import pytest

from fl_op.canonical.common import VersionDimensions
from fl_op.canonical.enums import CorrectiveActionType, PlanningMode
from fl_op.canonical.plan import Assignment, CorrectiveAction, Plan
from fl_op.stream.prognosis import prognosis_accuracy, record_prognosis_outcomes

_TS = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _plan(revision: str, service_assignments: int, withdrawn: int, escalated: int) -> Plan:
    assignments = [
        Assignment(
            assignment_id=f"as-{i}",
            task_id=f"service-sensor_{i}",
            bundle_id=f"bundle-{i}",
            planned_start=_TS,
            planned_finish=_TS,
        )
        for i in range(service_assignments)
    ]
    actions = [
        CorrectiveAction(
            action=CorrectiveActionType.SERVICE_WITHDRAWN,
            task_id=f"service-withdrawn_{i}",
        )
        for i in range(withdrawn)
    ] + [
        CorrectiveAction(
            action=CorrectiveActionType.SERVICE_ESCALATED,
            task_id=f"service-escalated_{i}",
        )
        for i in range(escalated)
    ]
    return Plan(
        plan_id="plan-1",
        revision_id=revision,
        origin_plan_id="plan-1",
        planning_mode=PlanningMode.ROLLING,
        snapshot_id="snap-1",
        version_dimensions=VersionDimensions(),
        adapter_id="ortools-rolling",
        adapter_version="0.1.0",
        generated_at=_TS,
        effective_from=_TS,
        assignments=assignments,
        corrective_actions=actions,
    )


def test_outcome_records_and_rates(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "prognosis.jsonl"
    record = record_prognosis_outcomes(_plan("rev-1", service_assignments=3, withdrawn=1, escalated=1), log)
    assert record["n_service_active"] == 3
    assert record["n_service_withdrawn"] == 1
    record_prognosis_outcomes(_plan("rev-2", service_assignments=2, withdrawn=1, escalated=0), log)

    accuracy = prognosis_accuracy(log)
    # 5 active + 2 withdrawn observed; 2 withdrawn, 1 escalated.
    assert accuracy["n_observed"] == 7.0
    assert accuracy["false_positive_rate"] == pytest.approx(2 / 7)
    assert accuracy["false_negative_rate"] == pytest.approx(1 / 7)


def test_no_history_yields_empty_accuracy(tmp_path: pathlib.Path) -> None:
    assert prognosis_accuracy(tmp_path / "missing.jsonl") == {}

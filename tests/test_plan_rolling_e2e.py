"""End-to-end rolling dispatch: event stream -> immutable revisions with frozen tasks."""

import pathlib
from datetime import datetime, timezone

import pytest

from fl_op.contracts.registry import FileRegistry
from fl_op.snapshot.builder import SnapshotBuilder
from fl_op.stream.driver import StreamDriver
from fl_op.stream.source import ExecutionEvent

_NOW = datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def stream_run(dataset_dir: pathlib.Path):
    registry = FileRegistry()
    sources = SnapshotBuilder(registry).load_sources(dataset_dir)

    # Baseline assignment to pick a real assigned order to mark started.
    driver = StreamDriver(registry)
    baseline = driver.initial_revision(
        __import__("copy").deepcopy(sources), effective_at=_NOW
    )
    assert baseline.plan.assignments, "baseline must assign at least one task"
    started_oid = baseline.plan.assignments[0].task_id
    a_vehicle = sources["vehicles"][-1]["vehicle_id"]

    events = [
        ExecutionEvent("e1", "task.started", _NOW.isoformat(), started_oid, {}),
        ExecutionEvent("e2", "asset.unavailable", _NOW.isoformat(), a_vehicle, {}),
    ]
    result = driver.run(sources, events, effective_at=_NOW)
    return started_oid, result


def test_stream_produces_revision_per_event(stream_run) -> None:
    _, result = stream_run
    # baseline + 2 events
    assert len(result.revisions) == 3
    # Each revision is linked to its parent (after the baseline).
    for rev in result.revisions[1:]:
        assert rev.plan.parent_revision_id is not None


def test_started_task_is_frozen_and_byte_identical(stream_run) -> None:
    started_oid, result = stream_run
    baseline_plan = result.revisions[0].plan
    base_assignment = next(
        a for a in baseline_plan.assignments if a.task_id == started_oid
    )
    # In every later revision the started task keeps its exact assignment.
    for rev in result.revisions[1:]:
        match = [a for a in rev.plan.assignments if a.task_id == started_oid]
        assert match, f"started task missing in revision {rev.plan.revision_id}"
        frozen = match[0]
        assert frozen.is_frozen is True
        assert frozen.bundle_id == base_assignment.bundle_id
        assert frozen.planned_start == base_assignment.planned_start
        assert frozen.asset_ids == base_assignment.asset_ids


def test_revisions_are_immutable(stream_run) -> None:
    from pydantic import ValidationError

    _, result = stream_run
    with pytest.raises(ValidationError):
        result.revisions[-1].plan.revision_id = "tampered"


def test_score_reports_freeze_and_instability(stream_run) -> None:
    _, result = stream_run
    last = result.revisions[-1].plan
    assert "n_frozen" in last.score
    assert "plan_instability_penalty" in last.score
    assert last.score["n_frozen"] >= 1

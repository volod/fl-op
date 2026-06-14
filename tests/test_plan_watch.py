"""Serving-side watcher: a single session producing a durable revision chain.

Where rolling drains the backlog once, ``run_plan_watch`` keeps one session
alive across bounded drain cycles. These tests pin the externally observable
contract: a baseline revision followed by one revision per event, an unbroken
parent->child continuity chain, and started tasks frozen byte-for-byte in
every later revision.
"""

import json
import pathlib
from datetime import datetime, timezone

import pytest

from fl_op.contracts.registry import FileRegistry
from fl_op.snapshot.builder import SnapshotBuilder
from fl_op.stream.driver import StreamDriver

_NOW = datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc)


def _baseline_refs(dataset_dir: pathlib.Path) -> tuple[str, str]:
    """A real assignable task id and a real vehicle id from a baseline plan."""
    registry = FileRegistry()
    sources = SnapshotBuilder(registry).load_sources(dataset_dir)
    baseline = StreamDriver(registry).initial_revision(sources, effective_at=_NOW)
    assert baseline.plan.assignments, "baseline must assign at least one task"
    return baseline.plan.assignments[0].task_id, sources["vehicles"][-1]["vehicle_id"]


@pytest.fixture
def watch_run(dataset_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch):
    """Run one bounded watch cycle over a two-event JSONL backlog."""
    from fl_op.planning import plans

    started_oid, vehicle_id = _baseline_refs(dataset_dir)
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        "\n".join(
            json.dumps(rec)
            for rec in (
                {
                    "event_id": "e1",
                    "event_type": "task.started",
                    "observed_at": _NOW.isoformat(),
                    "entity_ref": started_oid,
                    "payload": {},
                },
                {
                    "event_id": "e2",
                    "event_type": "asset.unavailable",
                    "observed_at": _NOW.isoformat(),
                    "entity_ref": vehicle_id,
                    "payload": {},
                },
            )
        )
        + "\n"
    )

    monkeypatch.setattr(plans, "DATA_ROOT", tmp_path / ".data")
    out_dir = plans.run_plan_watch(
        data_dir=str(dataset_dir),
        events_path=str(events_file),
        effective_at=_NOW.isoformat(),
        max_cycles=1,
    )
    return started_oid, out_dir


def _summary(out_dir: pathlib.Path) -> list[dict]:
    payload = json.loads((out_dir / "revisions_summary.json").read_text())
    return payload["revisions"]


def _revision_plan(out_dir: pathlib.Path, n: int) -> dict:
    return json.loads((out_dir / "revisions" / f"{n:03d}" / "plan.json").read_text())


def test_watch_publishes_baseline_then_one_revision_per_event(watch_run) -> None:
    _, out_dir = watch_run
    summary = _summary(out_dir)
    # baseline + 2 events, in arrival order.
    assert [r["trigger"] for r in summary] == [
        "baseline",
        "task.started",
        "asset.unavailable",
    ]
    assert [r["trigger_event_id"] for r in summary] == ["", "e1", "e2"]


def test_watch_revision_chain_is_continuous(watch_run) -> None:
    _, out_dir = watch_run
    summary = _summary(out_dir)
    assert summary[0]["parent_revision_id"] is None
    # Every later revision descends from the previous one: one unbroken chain.
    for parent, child in zip(summary, summary[1:]):
        assert child["parent_revision_id"] == parent["revision_id"]


def test_watch_persists_a_plan_file_per_revision(watch_run) -> None:
    _, out_dir = watch_run
    summary = _summary(out_dir)
    for n, row in enumerate(summary):
        plan = _revision_plan(out_dir, n)
        assert plan["revision_id"] == row["revision_id"]
        assert "schema_version" in plan


def test_watch_freezes_started_task_across_revisions(watch_run) -> None:
    started_oid, out_dir = watch_run
    baseline = _revision_plan(out_dir, 0)
    base = next(a for a in baseline["assignments"] if a["task_id"] == started_oid)
    # After task.started, the task is frozen and byte-identical in later revisions.
    for n in (1, 2):
        plan = _revision_plan(out_dir, n)
        match = [a for a in plan["assignments"] if a["task_id"] == started_oid]
        assert match, f"started task missing in revision {n}"
        frozen = match[0]
        assert frozen["is_frozen"] is True
        assert frozen["bundle_id"] == base["bundle_id"]
        assert frozen["planned_start"] == base["planned_start"]
        assert frozen["asset_ids"] == base["asset_ids"]

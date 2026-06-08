"""Periodic and rolling planning command implementations."""

import logging
import pathlib
from datetime import datetime, timezone
from typing import Optional

from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
from fl_op.canonical.enums import PlanningMode
from fl_op.canonical.snapshot import PlanningSnapshot
from fl_op.contracts.registry import FileRegistry
from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.planning.artifacts import model_json, run_timestamp, write_json
from fl_op.snapshot.builder import SnapshotBuilder

logger = logging.getLogger(__name__)


def run_plan_periodic(
    data_dir: str,
    snapshot: Optional[PlanningSnapshot] = None,
) -> pathlib.Path:
    """Solve a periodic plan. Builds a snapshot from data_dir unless one is provided."""
    registry = FileRegistry()
    if snapshot is None:
        snapshot = SnapshotBuilder(registry).build(data_dir, PlanningMode.PERIODIC)
    profile = registry.get_profile("agricultural-custom-services")
    plan = OrToolsPeriodicAdapter().plan(snapshot, profile)

    out_dir = DATA_ROOT / "plan-periodic" / run_timestamp()
    write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, **model_json(plan)},
        out_dir / "plan.json",
    )
    write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, **model_json(snapshot)},
        out_dir / "snapshot.json",
    )
    logger.info(
        "Periodic plan %s: %d assigned, %d unassigned -> %s",
        plan.plan_id,
        len(plan.assignments),
        len(plan.unassigned_tasks),
        out_dir,
    )
    return out_dir


def run_plan_rolling(
    data_dir: str,
    events_path: Optional[str] = None,
    effective_at: Optional[str] = None,
) -> pathlib.Path:
    """Drive rolling dispatch from an event stream, writing one revision per event."""
    from fl_op.stream.driver import StreamDriver
    from fl_op.stream.source import JsonlEventSource

    registry = FileRegistry()
    builder = SnapshotBuilder(registry)
    sources = builder.load_sources(data_dir)
    eff = datetime.fromisoformat(effective_at) if effective_at else datetime.now(tz=timezone.utc)

    events = list(JsonlEventSource(events_path)) if events_path else []

    driver = StreamDriver(registry)
    result = driver.run(sources, events, effective_at=eff)

    out_dir = DATA_ROOT / "plan-rolling" / run_timestamp()
    summary = []
    for n, rev in enumerate(result.revisions):
        rev_dir = out_dir / "revisions" / f"{n:03d}"
        write_json(
            {"schema_version": ARTIFACT_SCHEMA_VERSION, **model_json(rev.plan)},
            rev_dir / "plan.json",
        )
        summary.append(
            {
                "revision": n,
                "trigger": rev.event.event_type if rev.event else "baseline",
                "revision_id": rev.plan.revision_id,
                "parent_revision_id": rev.plan.parent_revision_id,
                "n_assignments": len(rev.plan.assignments),
                "n_frozen": rev.plan.score.get("n_frozen", 0),
                "n_carried_forward": rev.plan.score.get("n_carried_forward", 0),
                "n_changed_after_freeze": rev.plan.score.get("n_changed_after_freeze", 0),
                "plan_instability_penalty": rev.plan.score.get("plan_instability_penalty", 0),
                "n_unassigned": len(rev.plan.unassigned_tasks),
            }
        )
    write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, "revisions": summary},
        out_dir / "revisions_summary.json",
    )
    logger.info("Rolling dispatch: %d revisions -> %s", len(result.revisions), out_dir)
    return out_dir

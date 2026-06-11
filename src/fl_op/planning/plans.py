"""Periodic and rolling planning command implementations."""

import logging
import pathlib
from datetime import datetime, timezone
from typing import Optional

from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
from fl_op.canonical.enums import PlanningMode
from fl_op.canonical.snapshot import PlanningSnapshot
from fl_op.contracts.plan_contract import assert_plan_conforms
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
    profile_id = registry.active_profile_id
    if profile_id is None:
        raise ValueError("Registry declares no active domain profile")
    profile = registry.get_profile(profile_id)
    plan = OrToolsPeriodicAdapter().plan(snapshot, profile)
    assert_plan_conforms(plan)
    _log_plan_to_mlflow(plan)

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


def _log_plan_to_mlflow(plan, extra_tags: Optional[dict[str, str]] = None) -> None:
    """Opt-in experiment tracking: KPIs, version dimensions, solve telemetry."""
    from fl_op.tuning.mlflow_logger import log_solver_run

    solve_summary = plan.score.get("solve_telemetry") or {}
    version = plan.version_dimensions
    log_solver_run(
        run_name=f"{plan.plan_id}/{plan.revision_id}",
        params={
            "adapter_id": plan.adapter_id,
            "adapter_version": plan.adapter_version,
            "solver_version": plan.solver_version,
            "optimization_profile_version": version.optimization_profile_version,
            "adapter_compatibility_version": version.adapter_compatibility_version,
        },
        metrics={
            **{k: v for k, v in plan.score.items() if not isinstance(v, dict)},
            "n_clusters_hit_time_limit": solve_summary.get("n_hit_time_limit", 0),
            "total_solve_wall_s": solve_summary.get("total_solve_wall_s", 0.0),
            "n_lns_improved": solve_summary.get("n_lns_improved", 0),
            "total_lns_objective_delta": solve_summary.get(
                "total_lns_objective_delta", 0
            ),
        },
        tags={
            "planning_mode": plan.planning_mode.value,
            "snapshot_hash": plan.snapshot_hash,
            "snapshot_id": plan.snapshot_id,
            **(extra_tags or {}),
        },
    )


def run_plan_rolling(
    data_dir: str,
    events_path: Optional[str] = None,
    effective_at: Optional[str] = None,
) -> pathlib.Path:
    """Drive rolling dispatch from an event stream, writing one revision per event."""
    from fl_op.stream.broker import open_event_source
    from fl_op.stream.driver import StreamDriver

    registry = FileRegistry()
    builder = SnapshotBuilder(registry)
    sources = builder.load_sources(data_dir)
    eff = datetime.fromisoformat(effective_at) if effective_at else datetime.now(tz=timezone.utc)

    # EVENT_SOURCE_KIND selects JSONL (development default) or broker-backed
    # ingestion; both yield the same validated ExecutionEvents.
    events = list(open_event_source(events_path))

    driver = StreamDriver(registry)
    result = driver.run(sources, events, effective_at=eff)

    out_dir = DATA_ROOT / "plan-rolling" / run_timestamp()
    summary = []
    for n, rev in enumerate(result.revisions):
        assert_plan_conforms(rev.plan)
        rev_dir = out_dir / "revisions" / f"{n:03d}"
        write_json(
            {"schema_version": ARTIFACT_SCHEMA_VERSION, **model_json(rev.plan)},
            rev_dir / "plan.json",
        )
        summary.append(
            {
                "revision": n,
                "trigger": rev.event.event_type if rev.event else "baseline",
                "trigger_entity_ref": rev.event.entity_ref if rev.event else "",
                "trigger_event_id": rev.event.event_id if rev.event else "",
                "n_coalesced_events": rev.n_coalesced_events,
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
    # One experiment-tracking run per rolling invocation: the final revision
    # carries the converged KPIs, version dimensions, and solve telemetry.
    if result.revisions:
        _log_plan_to_mlflow(
            result.revisions[-1].plan,
            extra_tags={"n_revisions": str(len(result.revisions))},
        )
    logger.info("Rolling dispatch: %d revisions -> %s", len(result.revisions), out_dir)
    return out_dir

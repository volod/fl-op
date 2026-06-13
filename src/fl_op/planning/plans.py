"""Periodic and rolling planning command implementations."""

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

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
    from fl_op.adapters.rolling.corrective import service_task_reasons
    from fl_op.stream.prognosis import record_prognosis_outcomes

    registry = FileRegistry()
    if snapshot is None:
        snapshot = SnapshotBuilder(registry).build(data_dir, PlanningMode.PERIODIC)
    profile_id = registry.active_profile_id
    if profile_id is None:
        raise ValueError("Registry declares no active domain profile")
    profile = registry.get_profile(profile_id)
    # Reconcile against the previous periodic plan: withdrawn and escalated
    # service prognoses are recorded as corrective actions, the same
    # record-keeping rolling revisions get.
    previous_plan, previous_reasons = _previous_periodic_plan()
    plan = OrToolsPeriodicAdapter().plan(
        snapshot,
        profile,
        {
            "previous_plan": previous_plan,
            "previous_service_reasons": previous_reasons,
        },
    )
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
    # Why each service task was derived, for the next run's reconciliation.
    write_json(
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "service_reasons": service_task_reasons(snapshot),
        },
        out_dir / "service_reasons.json",
    )
    if previous_plan is not None:
        record_prognosis_outcomes(plan)
    logger.info(
        "Periodic plan %s: %d assigned, %d unassigned, %d corrective actions -> %s",
        plan.plan_id,
        len(plan.assignments),
        len(plan.unassigned_tasks),
        len(plan.corrective_actions),
        out_dir,
    )
    return out_dir


def _previous_periodic_plan() -> tuple[Optional[Any], dict[str, str]]:
    """The newest published periodic plan and its service reasons, if any."""
    base = DATA_ROOT / "plan-periodic"
    runs = sorted(d for d in base.glob("*") if (d / "plan.json").exists())
    if not runs:
        return None, {}
    previous_dir = runs[-1]
    try:
        plan = _load_published_plan(previous_dir)
    except (OSError, ValueError) as exc:
        logger.warning("Could not load previous periodic plan %s: %s", previous_dir, exc)
        return None, {}
    reasons: dict[str, str] = {}
    reasons_path = previous_dir / "service_reasons.json"
    if reasons_path.exists():
        try:
            reasons = json.loads(reasons_path.read_text()).get("service_reasons", {})
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load %s: %s", reasons_path, exc)
    return plan, reasons


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
    from fl_op.stream.broker import open_dedup_store, open_event_source
    from fl_op.stream.driver import StreamDriver

    registry = FileRegistry()
    builder = SnapshotBuilder(registry)
    sources = builder.load_sources(data_dir)
    eff = datetime.fromisoformat(effective_at) if effective_at else datetime.now(tz=timezone.utc)

    # EVENT_SOURCE_KIND selects JSONL (development default) or broker-backed
    # ingestion; both yield the same validated ExecutionEvents. Broker runs
    # additionally carry the durable event-id dedup store, so redeliveries
    # across process restarts never produce duplicate revisions.
    event_source = open_event_source(events_path)
    events = list(event_source)
    dedup_store = open_dedup_store()

    driver = StreamDriver(registry, dedup_store=dedup_store)
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
                "drone_logistics_kpis": rev.plan.score.get("drone_logistics_kpis", {}),
            }
        )
    write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, "revisions": summary},
        out_dir / "revisions_summary.json",
    )
    # Publication is durable at this point: first record the published event
    # ids in the dedup store, then commit the broker offsets - in that order.
    # A crash before this block replays the backlog (nothing lost); a crash
    # between record and commit redelivers events the store now suppresses
    # (nothing duplicated). Effectively-once, end to end.
    if dedup_store is not None:
        dedup_store.record_published(
            event_id
            for rev in result.revisions
            for event_id in rev.applied_event_ids
        )
    commit = getattr(event_source, "commit", None)
    if callable(commit):
        commit()
    # One experiment-tracking run per rolling invocation: the final revision
    # carries the converged KPIs, version dimensions, and solve telemetry.
    if result.revisions:
        _log_plan_to_mlflow(
            result.revisions[-1].plan,
            extra_tags={"n_revisions": str(len(result.revisions))},
        )
    logger.info("Rolling dispatch: %d revisions -> %s", len(result.revisions), out_dir)
    return out_dir


def _load_published_plan(plan_dir: pathlib.Path):
    """Load the published plan of one run dir (rolling runs: last revision)."""
    from fl_op.canonical.plan import Plan

    path = plan_dir / "plan.json"
    if not path.exists():
        revisions = sorted((plan_dir / "revisions").glob("*/plan.json"))
        if not revisions:
            raise FileNotFoundError(f"No plan.json under {plan_dir}")
        path = revisions[-1]
    payload = json.loads(path.read_text())
    payload.pop("schema_version", None)
    return Plan.model_validate(payload)


def _latest_plan_dir() -> pathlib.Path:
    """Newest published plan run dir across periodic and rolling outputs."""
    runs = [
        run_dir
        for base in ("plan-periodic", "plan-rolling")
        for run_dir in (DATA_ROOT / base).glob("*")
        if run_dir.is_dir()
    ]
    if not runs:
        raise FileNotFoundError(
            f"No published plans under {DATA_ROOT}/plan-periodic or plan-rolling"
        )
    return max(runs, key=lambda run_dir: run_dir.name)


def run_plan_freshness(
    data_dir: str,
    plan_dir: Optional[str] = None,
    replan: bool = False,
    events_path: Optional[str] = None,
) -> dict[str, Any]:
    """Watermark freshness check of a published plan against the data now.

    Builds a snapshot from ``data_dir`` and compares its source watermarks
    with the plan's recorded visibility horizon. With ``replan=True`` a stale
    plan automatically triggers a rolling replan, closing the loop the
    watermarks were recorded for; without it the check only reports.
    """
    from fl_op.stream.freshness import newly_visible_sources

    target = (
        pathlib.Path(plan_dir)
        if plan_dir and plan_dir.lower() != "latest"
        else _latest_plan_dir()
    )
    plan = _load_published_plan(target)
    snapshot = SnapshotBuilder(FileRegistry()).build(data_dir, PlanningMode.ROLLING)
    newly = newly_visible_sources(plan, snapshot)

    report: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "plan_id": plan.plan_id,
        "revision_id": plan.revision_id,
        "plan_dir": str(target),
        "data_dir": str(data_dir),
        "stale": bool(newly),
        "newly_visible_sources": newly,
        "replan_triggered": False,
    }
    if newly and replan:
        replan_dir = run_plan_rolling(data_dir, events_path=events_path)
        report["replan_triggered"] = True
        report["replan_dir"] = str(replan_dir)

    out_dir = DATA_ROOT / "freshness" / run_timestamp()
    write_json(report, out_dir / "freshness.json")
    logger.info(
        "Freshness check of %s: %s%s",
        plan.revision_id,
        "STALE" if newly else "fresh",
        " (replan triggered)" if report["replan_triggered"] else "",
    )
    return report

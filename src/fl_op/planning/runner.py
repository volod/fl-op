"""Orchestration for the contract -> snapshot -> plan CLI commands.

Keeps main.py thin: each function performs one stage end to end and writes
artifacts under .data/<method>/<run_timestamp>/ per the repository convention.
"""

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
from fl_op.canonical.enums import PlanningMode
from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.validate import validate_suite
from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.snapshot.builder import SnapshotBuilder

logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")


def _write_json(obj: Any, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _model_json(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


# ---------------------------------------------------------------------------
# contracts validate
# ---------------------------------------------------------------------------


def run_contracts_validate(persist: bool = False) -> bool:
    """Validate the contract suite; optionally persist fingerprints. Returns ok."""
    registry = FileRegistry()
    report = validate_suite(registry)

    logger.info("Contract validation: %s", "OK" if report.ok else "FAILED")
    logger.info(
        "%-18s %8s  rt  odcs  parsingFP        metaHash", "contract", "bindings"
    )
    for c in report.contracts:
        logger.info(
            "%-18s %8d  %s   %s   %s  %s",
            c.contract_id,
            c.n_bindings,
            "ok" if c.roundtrip_preserved else "NO",
            "ok" if c.odcs_matches_avro else "NO",
            c.avro_parsing_fingerprint[:12],
            c.optimization_metadata_hash[:12],
        )
        for err in c.errors:
            logger.error("  %s: %s", c.contract_id, err)
    if report.profile_errors:
        for err in report.profile_errors:
            logger.error("  profile: %s", err)

    if persist and report.ok:
        fps = {
            c.contract_id: {
                "avroParsingFingerprint": c.avro_parsing_fingerprint,
                "optimizationMetadataHash": c.optimization_metadata_hash,
            }
            for c in report.contracts
        }
        registry.persist_fingerprints(fps)
    return report.ok


# ---------------------------------------------------------------------------
# snapshot build
# ---------------------------------------------------------------------------


def run_snapshot_build(
    data_dir: str,
    mode: str = "periodic",
    effective_at: Optional[str] = None,
) -> pathlib.Path:
    """Build an immutable planning snapshot and write it under .data/snapshot/."""
    planning_mode = PlanningMode(mode)
    eff = datetime.fromisoformat(effective_at) if effective_at else None
    snapshot = SnapshotBuilder().build(data_dir, planning_mode, eff)

    out_dir = DATA_ROOT / "snapshot" / _ts()
    _write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, **_model_json(snapshot)},
        out_dir / "snapshot.json",
    )
    logger.info(
        "Snapshot %s (hash %s) -> %s",
        snapshot.snapshot_id,
        snapshot.snapshot_hash[:12],
        out_dir,
    )
    return out_dir


# ---------------------------------------------------------------------------
# plan periodic
# ---------------------------------------------------------------------------


def run_plan_periodic(data_dir: str) -> pathlib.Path:
    """Build a periodic snapshot, solve via the periodic adapter, write the Plan."""
    registry = FileRegistry()
    snapshot = SnapshotBuilder(registry).build(data_dir, PlanningMode.PERIODIC)
    profile = registry.get_profile("agricultural-custom-services")
    plan = OrToolsPeriodicAdapter().plan(snapshot, profile)

    out_dir = DATA_ROOT / "plan-periodic" / _ts()
    _write_json({"schema_version": ARTIFACT_SCHEMA_VERSION, **_model_json(plan)}, out_dir / "plan.json")
    _write_json({"schema_version": ARTIFACT_SCHEMA_VERSION, **_model_json(snapshot)}, out_dir / "snapshot.json")
    logger.info(
        "Periodic plan %s: %d assigned, %d unassigned -> %s",
        plan.plan_id,
        len(plan.assignments),
        len(plan.unassigned_tasks),
        out_dir,
    )
    return out_dir


# ---------------------------------------------------------------------------
# plan rolling (stream)
# ---------------------------------------------------------------------------


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

    out_dir = DATA_ROOT / "plan-rolling" / _ts()
    summary = []
    for n, rev in enumerate(result.revisions):
        rev_dir = out_dir / "revisions" / f"{n:03d}"
        _write_json({"schema_version": ARTIFACT_SCHEMA_VERSION, **_model_json(rev.plan)}, rev_dir / "plan.json")
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
    _write_json({"schema_version": ARTIFACT_SCHEMA_VERSION, "revisions": summary}, out_dir / "revisions_summary.json")
    logger.info("Rolling dispatch: %d revisions -> %s", len(result.revisions), out_dir)
    return out_dir


# ---------------------------------------------------------------------------
# demo: full batch + stream story
# ---------------------------------------------------------------------------


def generate_demo_events(data_dir: str, plan_dir: pathlib.Path) -> pathlib.Path:
    """Synthesize a small events.jsonl from a periodic plan for the rolling demo."""
    plan = json.loads((plan_dir / "plan.json").read_text())
    assignments = plan.get("assignments", [])
    now = datetime.now(tz=timezone.utc).isoformat()

    events: list[dict[str, Any]] = []
    if assignments:
        events.append({
            "event_id": "evt-001", "event_type": "task.started",
            "observed_at": now, "entity_ref": assignments[0]["task_id"], "payload_json": "{}",
        })
    if len(assignments) > 1:
        events.append({
            "event_id": "evt-002", "event_type": "asset.unavailable",
            "observed_at": now, "entity_ref": assignments[-1]["asset_ids"][0], "payload_json": "{}",
        })

    out_dir = DATA_ROOT / "demo" / _ts()
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    with events_path.open("w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    logger.info("Generated %d demo events -> %s", len(events), events_path)
    return events_path


def run_demo(data_dir: str) -> None:
    """Run the full pipeline: contracts -> snapshot -> periodic -> events -> rolling."""
    logger.info("=== fl-op demo: declarative contract -> snapshot -> batch + stream ===")
    logger.info("[1/5] Validating data contracts (Avro + ODCS + dual fingerprints)")
    if not run_contracts_validate():
        raise SystemExit("Contract validation failed; aborting demo.")

    logger.info("[2/5] Building immutable periodic planning snapshot")
    run_snapshot_build(data_dir, "periodic")

    logger.info("[3/5] Periodic (batch) optimization via OR-Tools adapter")
    periodic_dir = run_plan_periodic(data_dir)

    logger.info("[4/5] Synthesizing an execution-event stream")
    events_path = generate_demo_events(data_dir, periodic_dir)

    logger.info("[5/5] Rolling (stream) dispatch with freeze window and revisions")
    rolling_dir = run_plan_rolling(data_dir, str(events_path))

    _print_demo_summary(periodic_dir, rolling_dir)

    logger.info("Artifacts:")
    logger.info("  periodic plan:    %s", periodic_dir)
    logger.info("  rolling revisions: %s", rolling_dir)


def _print_demo_summary(periodic_dir: pathlib.Path, rolling_dir: pathlib.Path) -> None:
    """Log statistics and result analysis for the periodic and rolling runs."""
    snapshot = json.loads((periodic_dir / "snapshot.json").read_text())
    plan = json.loads((periodic_dir / "plan.json").read_text())
    revisions = json.loads((rolling_dir / "revisions_summary.json").read_text())["revisions"]

    score = plan.get("score", {})
    assigned = plan.get("assignments", [])
    unassigned = plan.get("unassigned_tasks", [])
    n_tasks = len(assigned) + len(unassigned)
    fill_rate = (len(assigned) / n_tasks * 100.0) if n_tasks else 0.0

    bar = "=" * 64
    logger.info(bar)
    logger.info("DEMO SUMMARY")
    logger.info(bar)

    # -- snapshot / governance ---------------------------------------------------
    qs = snapshot.get("quality_summary", {})
    logger.info("Snapshot   : %s", snapshot.get("snapshot_id", "?"))
    logger.info("  hash            : %s", snapshot.get("snapshot_hash", "")[:16])
    logger.info(
        "  canonical       : %d assets, %d locations, %d tasks, %d bundles",
        len(snapshot.get("assets", [])),
        len(snapshot.get("locations", [])),
        len(snapshot.get("tasks", [])),
        len(snapshot.get("bundles", [])),
    )
    logger.info(
        "  quality         : %d findings, %d entities excluded",
        qs.get("n_findings", 0),
        qs.get("n_entities_excluded", 0),
    )

    # -- periodic (batch) --------------------------------------------------------
    logger.info("-" * 64)
    logger.info("Periodic (batch) plan: %s", plan.get("plan_id", "?"))
    logger.info(
        "  assigned        : %d / %d tasks (%.1f%% fill rate)",
        len(assigned), n_tasks, fill_rate,
    )
    logger.info("  unassigned      : %d", len(unassigned))
    logger.info(
        "  margin (est.)   : %.2f EUR  (greedy baseline %.2f, solver %+.2f)",
        score.get("total_estimated_margin_eur", 0.0),
        score.get("greedy_baseline_margin_eur", 0.0),
        score.get("solver_improvement_eur", 0.0),
    )
    logger.info(
        "  resources       : %.1f L fuel, %.1f kg fertilizer",
        score.get("total_fuel_l", 0.0),
        score.get("total_fertilizer_kg", 0.0),
    )
    if unassigned:
        reasons: dict[str, int] = {}
        for u in unassigned:
            reasons[u.get("reason_code", "UNKNOWN")] = reasons.get(u.get("reason_code", "UNKNOWN"), 0) + 1
        for code, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            logger.info("    unassigned reason %-26s %d", code + ":", count)

    # -- rolling (stream) --------------------------------------------------------
    logger.info("-" * 64)
    logger.info("Rolling (stream) dispatch: %d revisions", len(revisions))
    logger.info(
        "  %-3s %-18s %6s %7s %8s %8s %6s",
        "rev", "trigger", "assign", "frozen", "carried", "changed", "unasn",
    )
    for r in revisions:
        logger.info(
            "  %-3d %-18s %6d %7d %8d %8d %6d",
            r["revision"], r["trigger"], r["n_assignments"],
            r["n_frozen"], r.get("n_carried_forward", 0),
            r["n_changed_after_freeze"], r["n_unassigned"],
        )
    total_instability = sum(r.get("plan_instability_penalty", 0) for r in revisions)
    total_changed = sum(r["n_changed_after_freeze"] for r in revisions[1:])
    logger.info(
        "  incremental replanning changed %d assignment(s) across %d event(s); "
        "total plan-instability penalty %d",
        total_changed, max(0, len(revisions) - 1), total_instability,
    )
    logger.info(bar)

"""Full planning demo orchestration."""

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any

from fl_op.canonical.enums import PlanningMode
from fl_op.contracts.registry import FileRegistry
from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION, OBJECTIVE_MODE_COST
from fl_op.core.paths import DATA_ROOT
from fl_op.planning.artifacts import model_json, run_timestamp, write_json
from fl_op.planning.contracts import run_contracts_validate
from fl_op.planning.demo_summary import print_demo_summary
from fl_op.planning.plans import run_plan_periodic, run_plan_rolling
from fl_op.snapshot.builder import SnapshotBuilder

logger = logging.getLogger(__name__)


def generate_demo_events(data_dir: str, plan_dir: pathlib.Path) -> pathlib.Path:
    """Synthesize a small events.jsonl from a periodic plan for the rolling demo."""
    plan = json.loads((plan_dir / "plan.json").read_text())
    assignments = plan.get("assignments", [])
    now = datetime.now(tz=timezone.utc).isoformat()

    events: list[dict[str, Any]] = []
    if assignments:
        events.append(
            {
                "event_id": "evt-001",
                "event_type": "task.started",
                "observed_at": now,
                "entity_ref": assignments[0]["task_id"],
                "payload_json": "{}",
            }
        )
    if len(assignments) > 1:
        events.append(
            {
                "event_id": "evt-002",
                "event_type": "asset.unavailable",
                "observed_at": now,
                "entity_ref": assignments[-1]["asset_ids"][0],
                "payload_json": "{}",
            }
        )

    out_dir = DATA_ROOT / "demo" / run_timestamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    with events_path.open("w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    logger.info("Generated %d demo events -> %s", len(events), events_path)
    return events_path


def run_demo(data_dir: str, objective: str = OBJECTIVE_MODE_COST) -> None:
    """Run the full pipeline: contracts -> snapshot -> periodic -> events -> rolling."""
    logger.info(
        "=== fl-op demo: declarative contract -> snapshot -> batch + stream (%s objective) ===",
        objective,
    )
    logger.info("[1/5] Validating data contracts (Avro + ODCS + dual fingerprints)")
    if not run_contracts_validate():
        raise SystemExit("Contract validation failed; aborting demo.")

    logger.info("[2/5] Building immutable periodic planning snapshot")
    registry = FileRegistry()
    effective_at = datetime.now(tz=timezone.utc)
    snapshot = SnapshotBuilder(registry).build(data_dir, PlanningMode.PERIODIC, effective_at)
    snap_out = DATA_ROOT / "snapshot" / run_timestamp()
    write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, **model_json(snapshot)},
        snap_out / "snapshot.json",
    )
    logger.info(
        "Snapshot %s (hash %s) -> %s",
        snapshot.snapshot_id,
        snapshot.snapshot_hash[:12],
        snap_out,
    )

    logger.info("[3/5] Periodic (batch) optimization via OR-Tools adapter")
    periodic_dir = run_plan_periodic(data_dir, snapshot=snapshot, objective=objective)

    logger.info("[4/5] Synthesizing an execution-event stream")
    events_path = generate_demo_events(data_dir, periodic_dir)

    logger.info("[5/5] Rolling (stream) dispatch with freeze window and revisions")
    rolling_dir = run_plan_rolling(
        data_dir,
        str(events_path),
        effective_at=effective_at.isoformat(),
        objective=objective,
    )

    logger.info("Artifacts:")
    logger.info("  periodic plan:    %s", periodic_dir)
    logger.info("  rolling revisions: %s", rolling_dir)
    print_demo_summary(periodic_dir, rolling_dir)

"""Reschedule pipeline: re-run solver after in-progress order updates."""

import logging
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.canonical.enums import TaskStatus
from fl_op.io import detect_format, get_codec, locate_source
from fl_op.solver.aggregator import _write_json, _write_report
from fl_op.solver.reschedule import _apply_events, _build_plan_diff, _load_json, _write_plan_diff_txt

logger = logging.getLogger(__name__)


def run_reschedule(data_dir: str, schedule_dir: str, events_path: str | None) -> None:
    """Re-run solver after in-progress updates; write plan_diff and new schedule."""
    from fl_op.solver.chain import run_solver_chain

    data_path = pathlib.Path(data_dir)
    codec = get_codec(detect_format(data_path))
    sched_path = pathlib.Path(schedule_dir)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = DATA_ROOT / "reschedule" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    schedule_file = sched_path / "schedule.json"
    if not schedule_file.exists():
        logger.error("schedule.json not found in %s.", sched_path)
        sys.exit(1)

    old_schedule: list[dict[str, Any]] = _load_json(schedule_file).get("schedule", [])

    orders_raw = codec.read(locate_source(data_path, "orders.csv", codec))
    vehicles_raw = codec.read(locate_source(data_path, "vehicles.csv", codec))
    implements_raw = codec.read(locate_source(data_path, "implements.csv", codec))
    depots_raw = codec.read(locate_source(data_path, "depots.csv", codec))
    fields_raw = codec.read(locate_source(data_path, "fields.csv", codec))
    operators_raw = codec.read(locate_source(data_path, "operators.csv", codec))

    if events_path:
        import json
        with open(events_path) as fh:
            events = json.load(fh)
        orders_raw = _apply_events(orders_raw, events)

    # orders_raw are raw physical rows (order_id/status columns); the canonical
    # task id equals the physical order_id via the identity binding.
    frozen_task_ids: set[str] = set()
    remaining_orders: list[dict[str, Any]] = []
    for order in orders_raw:
        status = order.get("status", "")
        try:
            task_status = TaskStatus(status)
        except ValueError:
            raise ValueError(
                f"Unknown task status '{status}' for task {order.get('order_id')}. "
                f"Valid values: {[s.value for s in TaskStatus]}"
            )
        if task_status == TaskStatus.STARTED:
            frozen_task_ids.add(order["order_id"])
        elif task_status == TaskStatus.PENDING:
            remaining_orders.append(order)

    logger.info(
        "Reschedule: %d frozen (started), %d remaining",
        len(frozen_task_ids), len(remaining_orders),
    )

    if not remaining_orders:
        logger.info("All orders are started/completed; nothing to reschedule.")
        empty_diff = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "reason": "no_unstarted_orders",
            "frozen_orders": list(frozen_task_ids),
            "added": [], "removed": [], "rescheduled": [], "newly_infeasible": [],
        }
        _write_json({"schema_version": ARTIFACT_SCHEMA_VERSION, "schedule": []}, out_dir / "schedule.json")
        _write_json(empty_diff, out_dir / "plan_diff.json")
        _write_plan_diff_txt(empty_diff, out_dir / "plan_diff.txt")
        sys.exit(0)

    # Project the (event-mutated) physical sources through the canonical snapshot
    # so the solver consumes canonical rows, never raw source data.
    from fl_op.snapshot.builder import SnapshotBuilder
    from fl_op.solver.inputs import build_solver_inputs

    sources = {
        "vehicles": vehicles_raw,
        "implements": implements_raw,
        "operators": operators_raw,
        "depots": depots_raw,
        "fields": fields_raw,
        "orders": remaining_orders,
        "weather": [],
    }
    snapshot = SnapshotBuilder().build_from_sources(sources)
    rows = build_solver_inputs(snapshot)
    result = run_solver_chain(rows, matrix_out_dir=out_dir)
    all_dispatch = result.dispatch
    all_infeasible = result.infeasible
    kpis = result.kpis

    run_metadata = {
        "timestamp": ts,
        "data_dir": str(data_path),
        "schedule_dir": str(sched_path),
        "n_frozen": len(frozen_task_ids),
        "n_remaining": len(remaining_orders),
    }

    new_infeasible_ids = {inf["task_id"] for inf in all_infeasible}
    plan_diff = _build_plan_diff(old_schedule, all_dispatch, frozen_task_ids, new_infeasible_ids)

    _write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, "run_metadata": run_metadata, "schedule": all_dispatch},
        out_dir / "schedule.json",
    )
    _write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, "run_metadata": run_metadata, "infeasible_orders": all_infeasible},
        out_dir / "infeasible_orders.json",
    )
    _write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, "run_metadata": run_metadata, **kpis},
        out_dir / "schedule_kpis.json",
    )
    _write_report(all_dispatch, all_infeasible, kpis, out_dir / "schedule_report.txt")
    _write_json(plan_diff, out_dir / "plan_diff.json")
    _write_plan_diff_txt(plan_diff, out_dir / "plan_diff.txt")

    logger.info(
        "Reschedule complete: %d dispatched, %d infeasible -> %s",
        kpis["n_dispatched"], kpis["n_infeasible"], out_dir,
    )

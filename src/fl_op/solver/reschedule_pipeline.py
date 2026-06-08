"""Reschedule pipeline: re-run solver after in-progress order updates."""

import logging
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.io import detect_format, get_codec, locate_source
from fl_op.models.enums import OrderStatus
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

    frozen_order_ids: set[str] = set()
    remaining_orders: list[dict[str, Any]] = []
    for order in orders_raw:
        status = order.get("status", "")
        try:
            order_status = OrderStatus(status)
        except ValueError:
            raise ValueError(
                f"Unknown order status '{status}' for order {order.get('order_id')}. "
                f"Valid values: {[s.value for s in OrderStatus]}"
            )
        if order_status == OrderStatus.STARTED:
            frozen_order_ids.add(order["order_id"])
        elif order_status == OrderStatus.PENDING:
            remaining_orders.append(order)

    logger.info(
        "Reschedule: %d frozen (started), %d remaining",
        len(frozen_order_ids), len(remaining_orders),
    )

    if not remaining_orders:
        logger.info("All orders are started/completed; nothing to reschedule.")
        empty_diff = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "reason": "no_unstarted_orders",
            "frozen_orders": list(frozen_order_ids),
            "added": [], "removed": [], "rescheduled": [], "newly_infeasible": [],
        }
        _write_json({"schema_version": ARTIFACT_SCHEMA_VERSION, "schedule": []}, out_dir / "schedule.json")
        _write_json(empty_diff, out_dir / "plan_diff.json")
        _write_plan_diff_txt(empty_diff, out_dir / "plan_diff.txt")
        sys.exit(0)

    rows = {
        "vehicles": vehicles_raw,
        "implements": implements_raw,
        "orders": remaining_orders,
        "depots": depots_raw,
        "fields": fields_raw,
        "operators": operators_raw,
    }
    result = run_solver_chain(rows, matrix_out_dir=out_dir)
    all_dispatch = result.dispatch
    all_infeasible = result.infeasible
    kpis = result.kpis

    run_metadata = {
        "timestamp": ts,
        "data_dir": str(data_path),
        "schedule_dir": str(sched_path),
        "n_frozen": len(frozen_order_ids),
        "n_remaining": len(remaining_orders),
    }

    new_infeasible_ids = {inf["order_id"] for inf in all_infeasible}
    plan_diff = _build_plan_diff(old_schedule, all_dispatch, frozen_order_ids, new_infeasible_ids)

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

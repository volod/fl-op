"""Reschedule pipeline: re-run solver after in-progress order updates."""

import csv
import logging
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.models.enums import OrderStatus
from fl_op.solver.aggregator import _compute_kpis, _write_json, _write_report
from fl_op.solver.cluster_pool import pool_solve
from fl_op.solver.reschedule import _apply_events, _build_plan_diff, _load_json, _write_plan_diff_txt

logger = logging.getLogger(__name__)


def _load_csv(data_path: pathlib.Path, name: str) -> list[dict[str, Any]]:
    p = data_path / name
    if not p.exists():
        return []
    with p.open() as fh:
        return list(csv.DictReader(fh))


def run_reschedule(data_dir: str, schedule_dir: str, events_path: str | None) -> None:
    """Re-run solver after in-progress updates; write plan_diff and new schedule."""
    from fl_op.models.compat_matrix import build_compat_matrix, save_compat_matrix
    from fl_op.models.implement import Implement
    from fl_op.models.vehicle import Vehicle
    from fl_op.solver.greedy import greedy_assign, vectorized_score
    from fl_op.solver.preprocessing import build_cluster_specs, filter_feasible_vehicle_implement_pairs
    from fl_op.solver.resource_allocator import allocate_resources

    data_path = pathlib.Path(data_dir)
    sched_path = pathlib.Path(schedule_dir)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = DATA_ROOT / "reschedule" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    schedule_file = sched_path / "schedule.json"
    if not schedule_file.exists():
        logger.error("schedule.json not found in %s.", sched_path)
        sys.exit(1)

    old_schedule: list[dict[str, Any]] = _load_json(schedule_file).get("schedule", [])

    orders_raw = _load_csv(data_path, "orders.csv")
    vehicles_raw = _load_csv(data_path, "vehicles.csv")
    implements_raw = _load_csv(data_path, "implements.csv")
    depots_raw = _load_csv(data_path, "depots.csv")
    fields_raw = _load_csv(data_path, "fields.csv")
    operators_raw = _load_csv(data_path, "operators.csv")

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

    vehicle_index = {v["vehicle_id"]: i for i, v in enumerate(vehicles_raw)}
    implement_index = {im["implement_id"]: i for i, im in enumerate(implements_raw)}
    order_index = {o["order_id"]: o for o in remaining_orders}

    vehicles_parsed = [Vehicle.model_validate(v) for v in vehicles_raw]
    implements_parsed = [Implement.model_validate(im) for im in implements_raw]
    compat, power_margin = build_compat_matrix(vehicles_parsed, implements_parsed)
    save_compat_matrix(compat, power_margin, out_dir / "matrix")

    feasible_pairs = filter_feasible_vehicle_implement_pairs(
        remaining_orders, vehicles_raw, implements_raw, compat, vehicle_index, implement_index
    )
    clusters = build_cluster_specs(
        remaining_orders, fields_raw, depots_raw, vehicles_raw, implements_raw,
        compat, vehicle_index, implement_index, order_index,
    )
    clusters = allocate_resources(
        clusters, remaining_orders, vehicles_raw, implements_raw, operators_raw,
        compat, power_margin, vehicle_index, implement_index, feasible_pairs,
    )
    scored = vectorized_score(
        remaining_orders, vehicles_raw, implements_raw, fields_raw,
        feasible_pairs, vehicle_index, implement_index,
    )
    greedy_assignment = greedy_assign(scored, vehicle_index, implement_index)

    all_dispatch, all_infeasible = pool_solve(
        clusters, remaining_orders, vehicles_raw, implements_raw, fields_raw, depots_raw,
        greedy_assignment, vehicle_index, implement_index,
    )
    kpis = _compute_kpis(all_dispatch, all_infeasible, remaining_orders, greedy_assignment)

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

"""Reschedule pipeline.

Loads an existing schedule, freezes 'started' orders, re-runs pre-allocation
+ solver on remaining orders, and writes plan_diff.json + plan_diff.txt.

Events file supports the 'mark_started' type to transition order status.
"""

import csv
import json
import logging
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.models.enums import OrderStatus

logger = logging.getLogger(__name__)


def _write_json(obj: Any, path: pathlib.Path) -> None:
    with path.open("w") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _load_json(path: pathlib.Path) -> Any:
    with path.open() as fh:
        return json.load(fh)


def _apply_events(
    orders: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mutate order statuses based on events; raise on unknown event types."""
    order_map = {o["order_id"]: o for o in orders}
    for event in events:
        event_type = event.get("type")
        if event_type == "mark_started":
            oid = event.get("order_id")
            if oid and oid in order_map:
                order_map[oid]["status"] = OrderStatus.STARTED.value
            else:
                logger.warning("mark_started: order_id %s not found", oid)
        else:
            raise ValueError(
                f"Unknown event type '{event_type}'. "
                f"Supported types: mark_started"
            )
    return orders


def _build_plan_diff(
    old_schedule: list[dict[str, Any]],
    new_schedule: list[dict[str, Any]],
    frozen_order_ids: set[str],
    infeasible_order_ids: set[str],
) -> dict[str, Any]:
    old_order_ids = {d["order_id"] for d in old_schedule}
    new_order_ids = {d["order_id"] for d in new_schedule}

    added = [d for d in new_schedule if d["order_id"] not in old_order_ids]
    removed = [
        d for d in old_schedule
        if d["order_id"] not in new_order_ids and d["order_id"] not in frozen_order_ids
    ]

    rescheduled = []
    old_map = {d["order_id"]: d for d in old_schedule}
    for dp in new_schedule:
        oid = dp["order_id"]
        if oid in old_map:
            old = old_map[oid]
            if (
                old.get("vehicle_id") != dp.get("vehicle_id")
                or old.get("scheduled_start") != dp.get("scheduled_start")
            ):
                rescheduled.append({"order_id": oid, "from": old, "to": dp})

    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "frozen_orders": list(frozen_order_ids),
        "added": added,
        "removed": removed,
        "rescheduled": rescheduled,
        "newly_infeasible": list(infeasible_order_ids),
    }


def _write_plan_diff_txt(diff: dict[str, Any], path: pathlib.Path) -> None:
    lines = [
        "Plan Diff Summary",
        "=" * 40,
        f"Frozen (started):   {len(diff['frozen_orders'])}",
        f"Newly added:        {len(diff['added'])}",
        f"Removed:            {len(diff['removed'])}",
        f"Rescheduled:        {len(diff['rescheduled'])}",
        f"Newly infeasible:   {len(diff['newly_infeasible'])}",
    ]
    if diff["rescheduled"]:
        lines.append("")
        lines.append("Rescheduled orders (first 10):")
        for r in diff["rescheduled"][:10]:
            lines.append(
                f"  {r['order_id']}: "
                f"{r['from'].get('vehicle_id')} -> {r['to'].get('vehicle_id')}"
            )
    path.write_text("\n".join(lines) + "\n")


def run_reschedule(
    data_dir: str,
    schedule_dir: str,
    events_path: str | None,
) -> None:
    from fl_op.models.compat_matrix import build_compat_matrix, save_compat_matrix
    from fl_op.models.implement import Implement
    from fl_op.models.vehicle import Vehicle
    from fl_op.solver.aggregator import pool_solve, _compute_kpis, _write_json, _write_report
    from fl_op.solver.greedy import vectorized_score, greedy_assign
    from fl_op.solver.preprocessing import build_cluster_specs, filter_feasible_vehicle_implement_pairs
    from fl_op.solver.resource_allocator import allocate_resources

    data_path = pathlib.Path(data_dir)
    sched_path = pathlib.Path(schedule_dir)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = pathlib.Path(".data") / "reschedule" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load existing schedule
    schedule_file = sched_path / "schedule.json"
    if not schedule_file.exists():
        logger.error("[error] schedule.json not found in %s.", sched_path)
        sys.exit(1)

    schedule_data = _load_json(schedule_file)
    old_schedule: list[dict[str, Any]] = schedule_data.get("schedule", [])

    # Load data
    def load_csv(name: str) -> list[dict[str, Any]]:
        p = data_path / name
        if not p.exists():
            return []
        with p.open() as fh:
            return list(csv.DictReader(fh))

    orders_raw = load_csv("orders.csv")
    vehicles_raw = load_csv("vehicles.csv")
    implements_raw = load_csv("implements.csv")
    depots_raw = load_csv("depots.csv")
    fields_raw = load_csv("fields.csv")
    operators_raw = load_csv("operators.csv")

    # Apply events (e.g., mark_started)
    if events_path:
        with open(events_path) as fh:
            events = json.load(fh)
        orders_raw = _apply_events(orders_raw, events)

    # Separate frozen (started) from remaining
    frozen_order_ids: set[str] = set()
    remaining_orders: list[dict[str, Any]] = []
    for order in orders_raw:
        status = order.get("status", "")
        # Validate status strictly — raise on unknown
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
        # COMPLETED and INFEASIBLE orders are silently dropped from remaining

    logger.info(
        "Reschedule: %d frozen (started), %d remaining",
        len(frozen_order_ids),
        len(remaining_orders),
    )

    # Guard: all orders frozen -> exit 0 without building BallTree
    if not remaining_orders:
        logger.info("All orders are started/completed; nothing to reschedule.")
        empty_diff = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "reason": "no_unstarted_orders",
            "frozen_orders": list(frozen_order_ids),
            "added": [],
            "removed": [],
            "rescheduled": [],
            "newly_infeasible": [],
        }
        _write_json({"schema_version": ARTIFACT_SCHEMA_VERSION, "schedule": []}, out_dir / "schedule.json")
        _write_json(empty_diff, out_dir / "plan_diff.json")
        _write_plan_diff_txt(empty_diff, out_dir / "plan_diff.txt")
        sys.exit(0)

    # Re-run pipeline on remaining orders
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

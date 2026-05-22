"""Reschedule helpers: event application, plan diff computation, and diff reporting."""

import json
import logging
import pathlib
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
    """Mutate order statuses based on events; raise ValueError on unknown event types."""
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
                f"Unknown event type '{event_type}'. Supported types: mark_started"
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

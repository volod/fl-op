"""Infeasibility record helpers for cluster solving."""

from typing import Any

from fl_op.canonical.enums import ReasonCode


def mark_all_infeasible(
    cluster_dict: dict[str, Any],
    reason_code: ReasonCode,
    detail: str,
) -> tuple[list[dict], list[dict]]:
    """Return every order in a cluster as infeasible with one shared reason."""
    infeasible = [
        {
            "task_id": oid,
            "cluster_id": cluster_dict.get("cluster_id", ""),
            "reason_code": reason_code.value,
            "detail": detail,
        }
        for oid in cluster_dict.get("task_ids", [])
    ]
    return [], infeasible


def unserved_orders(
    task_ids: list[str],
    cluster_id: str,
    served_task_ids: set[str],
    orders: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Build infeasibility records for orders dropped by prize collection."""
    if orders:
        order_map = {o.task_id: o for o in orders}
        group_members: dict[str, list[str]] = {}
        group_labels: dict[str, str] = {}
        ungrouped: list[str] = []
        for oid in task_ids:
            order = order_map.get(oid)
            group = str(getattr(order, "alternative_group_ref", "") or "")
            if group:
                group_members.setdefault(group, []).append(oid)
                group_labels[group] = group
            else:
                ungrouped.append(oid)

        records: list[dict[str, Any]] = []
        for oid in ungrouped:
            if oid not in served_task_ids:
                records.append(_unserved_record(oid, cluster_id))
        for group, members in sorted(group_members.items()):
            if served_task_ids.intersection(members):
                continue
            label = group_labels.get(group, group)
            records.append(
                {
                    "task_id": label,
                    "cluster_id": cluster_id,
                    "reason_code": ReasonCode.OPTIMIZATION_TRADEOFF.value,
                    "detail": (
                        "OR-Tools routing did not assign any alternative for "
                        f"{label}: {', '.join(members)}"
                    ),
                }
            )
        return records

    return [
        _unserved_record(oid, cluster_id)
        for oid in task_ids
        if oid not in served_task_ids
    ]


def _unserved_record(task_id: str, cluster_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "cluster_id": cluster_id,
        "reason_code": ReasonCode.OPTIMIZATION_TRADEOFF.value,
        "detail": "OR-Tools routing did not assign this order to any vehicle",
    }

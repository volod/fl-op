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
            "order_id": oid,
            "cluster_id": cluster_dict.get("cluster_id", ""),
            "reason_code": reason_code.value,
            "detail": detail,
        }
        for oid in cluster_dict.get("order_ids", [])
    ]
    return [], infeasible


def unserved_orders(
    order_ids: list[str],
    cluster_id: str,
    served_order_ids: set[str],
) -> list[dict[str, Any]]:
    """Build infeasibility records for orders dropped by prize collection."""
    return [
        {
            "order_id": oid,
            "cluster_id": cluster_id,
            "reason_code": ReasonCode.OPTIMIZATION_TRADEOFF.value,
            "detail": "OR-Tools routing did not assign this order to any vehicle",
        }
        for oid in order_ids
        if oid not in served_order_ids
    ]

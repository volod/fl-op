"""Operator assignment helpers for resource pre-allocation."""

from typing import Any

from fl_op.solver.enforcement import ops_set
from fl_op.solver.types import ClusterSpec
from fl_op.solver.allocation.state import AllocationState


def index_operators_by_depot(
    operators: list[Any],
) -> dict[str, list[Any]]:
    """Return operators grouped by depot in input order."""
    depot_operators: dict[str, list[Any]] = {}
    for operator in operators:
        depot_operators.setdefault(operator.home_depot_ref, []).append(operator)
    return depot_operators


def assign_operator(
    cluster: ClusterSpec,
    operators: list[Any],
    depot_operators: dict[str, list[Any]],
    state: AllocationState,
    cluster_operations: set[str],
) -> None:
    """Assign the unclaimed operator covering the most cluster operations.

    Depot operators are preferred; the global pool is the fallback. Among
    candidates the one certified for the most of the cluster's operation types
    wins (ties keep input order), so qualification enforcement loses as few
    tasks as possible.
    """
    depot_id = cluster["depot_ref"]
    available_ops = [
        op
        for op in depot_operators.get(depot_id, [])
        if op.asset_id not in state.claimed_operators
    ]
    if not available_ops:
        available_ops = [
            op for op in operators if op.asset_id not in state.claimed_operators
        ]
    if not available_ops:
        return

    operator = max(
        available_ops,
        key=lambda op: len(cluster_operations & ops_set(op.certified_operations)),
    )
    state.claimed_operators.add(operator.asset_id)
    cluster["operator_ref"] = operator.asset_id  # type: ignore[typeddict-unknown-key]

"""Operator assignment helpers for resource pre-allocation."""

from typing import Any

from fl_op.solver.types import ClusterSpec
from fl_op.solver.allocation.state import AllocationState


def index_operators_by_depot(
    operators: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Return operators grouped by depot in input order."""
    depot_operators: dict[str, list[dict[str, Any]]] = {}
    for operator in operators:
        depot_operators.setdefault(operator["home_depot_ref"], []).append(operator)
    return depot_operators


def assign_operator(
    cluster: ClusterSpec,
    operators: list[dict[str, Any]],
    depot_operators: dict[str, list[dict[str, Any]]],
    state: AllocationState,
) -> None:
    """Assign the first unclaimed depot operator, falling back globally."""
    depot_id = cluster["depot_ref"]
    available_ops = [
        op
        for op in depot_operators.get(depot_id, [])
        if op["asset_id"] not in state.claimed_operators
    ]
    if not available_ops:
        available_ops = [
            op for op in operators if op["asset_id"] not in state.claimed_operators
        ]
    if not available_ops:
        return

    operator = available_ops[0]
    state.claimed_operators.add(operator["asset_id"])
    cluster["operator_ref"] = operator["asset_id"]  # type: ignore[typeddict-unknown-key]

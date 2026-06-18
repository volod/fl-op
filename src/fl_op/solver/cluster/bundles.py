"""Shared routing-bundle capability predicates."""

from typing import Any

from fl_op.solver.travel_time import operation_set


def bundle_supports_operation(
    routing_vehicle: dict[str, Any], operation: str
) -> bool:
    """Whether both members of a routed asset bundle admit an operation."""
    prime_ops = operation_set(
        getattr(routing_vehicle.get("prime"), "compatible_operations", [])
    )
    related_ops = operation_set(
        getattr(routing_vehicle.get("related"), "compatible_operations", [])
    )
    return (not prime_ops or operation in prime_ops) and (
        not related_ops or operation in related_ops
    )

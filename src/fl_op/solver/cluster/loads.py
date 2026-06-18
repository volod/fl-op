"""Shared route-load parsing and compartment capacity helpers."""

from typing import Any

from fl_op.core import constants


def load_kg(value: Any) -> float:
    """Coerce a load value to nonnegative kilograms."""
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def compartment_capacity_kg(prime: Any, material: str) -> float:
    """Vehicle capacity for one material: compartment, aggregate, unlimited."""
    compartments = (
        prime.load_capacities if isinstance(prime.load_capacities, dict) else {}
    )
    value = compartments.get(material) if material else None
    if value is None:
        value = prime.load_capacity
    capacity_kg = load_kg(value)
    return capacity_kg if capacity_kg > 0 else constants.VEHICLE_LOAD_UNLIMITED_KG

"""Mutable allocation state shared across clusters."""

from dataclasses import dataclass, field

MAX_VEHICLE_ASSIGNMENTS = 2


@dataclass
class AllocationState:
    """Global resource claims accumulated during pre-allocation."""

    claimed_implements: set[str] = field(default_factory=set)
    claimed_operators: set[str] = field(default_factory=set)
    vehicle_assignment_count: dict[str, int] = field(default_factory=dict)

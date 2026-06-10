"""Typed contracts for the solver pipeline.

Two families live here, both domain-agnostic (assets and tasks are identified by
canonical ids, never by domain-specific column names):

1. Frozen INPUT-row dataclasses (PrimeMoverRow, RelatedRow, OperatorRow, SiteRow,
   DepotRow, TaskRow). build_solver_inputs projects the canonical snapshot into
   these; every solver stage reads `row.field`, never `row["field"]`. They are
   frozen + slotted: immutable, low-memory, and safe to pickle across the
   ProcessPoolExecutor(spawn) boundary in cluster_pool.

2. OUTPUT/working TypedDicts (ClusterSpec, FeasibleAssignment, DispatchPackage,
   InfeasibleOrder). These stay plain dicts: ClusterSpec is mutated during
   allocation, and the dispatch/infeasible packages serialise straight to JSON.

Plain JSON-serialisable primitives only -- no Pydantic models, no OR-Tools
objects -- so the types stay easy to log, assert, and serialise.
"""

import dataclasses
import logging
from collections.abc import Mapping
from typing import Any, Optional

from typing_extensions import Required, Self, TypedDict

from fl_op.core.constants import (
    FUEL_CONSUMPTION_DEFAULT_L_PER_H,
    RELATED_OPERATING_SPEED_DEFAULT,
    RELATED_WORKING_WIDTH_DEFAULT,
    TRAVEL_SPEED_DEFAULT_KMH,
)

logger = logging.getLogger(__name__)


class ClusterSpec(TypedDict, total=False):
    cluster_id: Required[str]
    depot_ref: Required[str]
    task_ids: Required[list[str]]
    # prime-mover asset_id -> list of related-equipment asset_ids pre-allocated
    allocated_prime_related: Required[dict[str, list[str]]]
    # operator asset_id assigned to the cluster (filled by allocation)
    operator_ref: str
    # sum of task penalty_per_day for priority-ordering
    total_penalty_per_day: Required[float]


class FeasibleAssignment(TypedDict):
    prime_asset_id: str
    related_asset_id: str
    task_id: str
    # gross_margin_estimate - repositioning_cost
    greedy_score: float
    # estimated gross revenue for this assignment
    gross_margin_estimate_eur: float
    # estimated repositioning fuel cost
    repositioning_cost_eur: float


class DispatchPackage(TypedDict):
    dispatch_id: str
    cluster_id: str
    prime_asset_id: str
    related_asset_id: str
    operator_asset_id: str
    task_id: str
    depot_ref: str
    # ISO-8601 strings
    scheduled_start: str
    scheduled_end: str
    route_waypoints: list[dict[str, Any]]
    estimated_fuel_l: float
    estimated_fertilizer_kg: float
    estimated_margin_eur: float


class InfeasibleOrder(TypedDict):
    task_id: str
    cluster_id: str
    # canonical ReasonCode value, e.g. "NO_COMPATIBLE_BUNDLE"
    reason_code: str
    # human-readable description
    detail: str


# ---------------------------------------------------------------------------
# Frozen INPUT-row dataclasses
# ---------------------------------------------------------------------------
#
#   build_solver_inputs(snapshot)                to_canonical_row(raw, contract)
#            |                                              |
#            +---- _project() -> canonical dict ----+ -----+
#                                                   |
#                                   Row.from_canonical_dict(dict)
#                                                   |
#                                                   v
#                          frozen + slotted row (PrimeMoverRow, ...)
#                                                   |
#                  read as row.field across solver/, never row["field"]
#
# from_canonical_dict keeps only declared fields (drops non-canonical extras) and
# leaves absent optional fields at their constant-backed defaults. It is the one
# construction path for both the snapshot projection and partial query orders, and
# doubles as the typed test-fixture builder.


class _SolverRow:
    """Mixin giving every solver row a single, defaulting construction path.

    __slots__ = () so subclasses declared with @dataclass(slots=True) keep no
    per-instance __dict__ (the memory/pickle win relied on at fleet scale).
    """

    __slots__ = ()

    @classmethod
    def from_canonical_dict(cls, data: Mapping[str, Any]) -> Self:
        """Build a row from a canonical-keyed dict, ignoring non-declared keys."""
        field_names = {f.name for f in dataclasses.fields(cls)}
        dropped = [k for k in data if k not in field_names]
        if dropped:
            logger.debug(
                "%s.from_canonical_dict dropping non-canonical keys: %s",
                cls.__name__,
                sorted(dropped),
            )
        kwargs = {k: v for k, v in data.items() if k in field_names}
        return cls(**kwargs)


@dataclasses.dataclass(frozen=True, slots=True)
class PrimeMoverRow(_SolverRow):
    """A mobile prime mover (vehicle role) the solver can assign to tasks."""

    asset_id: str
    asset_type: str = ""
    name: str = ""
    home_depot_ref: str = ""
    lat: float = 0.0
    lon: float = 0.0
    rated_power: float = 0.0
    fuel_tank_volume: float = 0.0
    fuel_consumption_rate: float = FUEL_CONSUMPTION_DEFAULT_L_PER_H
    travel_speed: float = TRAVEL_SPEED_DEFAULT_KMH


@dataclasses.dataclass(frozen=True, slots=True)
class RelatedRow(_SolverRow):
    """Related equipment (implement role) powered by a prime mover."""

    asset_id: str
    asset_type: str = ""
    name: str = ""
    home_depot_ref: str = ""
    required_power: float = 0.0
    working_width: float = RELATED_WORKING_WIDTH_DEFAULT
    min_speed: float = 0.0
    max_speed: float = RELATED_OPERATING_SPEED_DEFAULT
    material_capacity: float = 0.0
    compatible_operations: Any = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class OperatorRow(_SolverRow):
    """An operator (labour role) assignable to a cluster."""

    asset_id: str
    name: str = ""
    home_depot_ref: str = ""
    shift_start: str = ""
    shift_end: str = ""
    certified_operations: Any = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class SiteRow(_SolverRow):
    """A work site (field) where tasks are executed."""

    location_id: str
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    area: float = 0.0
    soil_type: str = ""
    polygon: Optional[Any] = None


@dataclasses.dataclass(frozen=True, slots=True)
class DepotRow(_SolverRow):
    """A depot: route origin and inventory source."""

    location_id: str
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    inventory_fuel: float = 0.0
    inventory_material: float = 0.0


@dataclasses.dataclass(frozen=True, slots=True)
class ForecastRow(_SolverRow):
    """An environmental forecast window used for weather-window enforcement."""

    forecast_id: str
    lat: float = 0.0
    lon: float = 0.0
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    wind_speed: Optional[float] = None
    precipitation_rate: Optional[float] = None
    soil_moisture: Optional[float] = None


@dataclasses.dataclass(frozen=True, slots=True)
class TaskRow(_SolverRow):
    """A unit of work to schedule (order)."""

    task_id: str
    order_ref: str = ""
    location_ref: str = ""
    operation_type: str = ""
    area: float = 0.0
    deadline: Optional[str] = None
    penalty_per_day: float = 0.0
    priority_class: str = ""
    status: str = ""
    revenue: float = 0.0

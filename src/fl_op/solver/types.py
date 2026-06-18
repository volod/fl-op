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
    # task_id -> backup operator asset_id for tasks the cluster operator is
    # not certified for (filled by qualification enforcement)
    task_operators: dict[str, str]
    # backup operator ids this cluster shares with another cluster over an
    # overlapping window (OPERATOR_SHARING_SEQUENTIAL only); the pool serializes
    # clusters that share one and feeds forward the committed operator intervals.
    shared_backup_operators: list[str]
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
    # estimated repositioning energy cost
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
    energy_resource_type: str
    estimated_energy_quantity: float
    estimated_energy_unit: str
    estimated_energy_cost_eur: float
    estimated_fertilizer_kg: float
    estimated_distance_km: float
    estimated_labor_cost_eur: float
    estimated_machine_wear_cost_eur: float
    estimated_toll_cost_eur: float
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
    energy_resource_type: str = "fuel"
    energy_unit: str = "L"
    energy_capacity: float = 0.0
    energy_consumption_rate: float = 0.0
    # Per-vehicle machine-wear/operating rate (EUR per operating hour); 0 falls
    # back to the fleet machine-wear rate.
    machine_wear_eur_per_h: float = 0.0
    travel_speed: float = TRAVEL_SPEED_DEFAULT_KMH
    # Total mass carried on one route; 0 means the load is unconstrained.
    load_capacity: float = 0.0
    # Per-material compartment capacities (material code -> kg); materials
    # absent from the map fall back to load_capacity.
    load_capacities: Any = dataclasses.field(default_factory=dict)
    # Optional prime-mover operation compatibility. Empty means unconstrained
    # for legacy domains.
    compatible_operations: Any = dataclasses.field(default_factory=list)


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
    # Work quantity processed per hour, keyed by work-quantity unit ("m3", ...).
    work_rates: Any = dataclasses.field(default_factory=dict)
    compatible_operations: Any = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class OperatorRow(_SolverRow):
    """An operator (labour role) assignable to a cluster."""

    asset_id: str
    name: str = ""
    home_depot_ref: str = ""
    shift_start: str = ""
    shift_end: str = ""
    # Per-operator wage band (EUR per operating hour); 0 falls back to the fleet
    # labour rate.
    wage_eur_per_h: float = 0.0
    certified_operations: Any = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class SiteRow(_SolverRow):
    """A work site (field) where tasks are executed."""

    location_id: str
    location_type: str = "field"
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    area: float = 0.0
    soil_type: str = ""
    polygon: Optional[Any] = None
    # Operation types prohibited at this site (restricted zone).
    restricted_operations: Any = dataclasses.field(default_factory=list)
    # ISO-8601 "from/to" intervals when no execution may start here.
    restriction_windows: Any = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class DepotRow(_SolverRow):
    """A depot: route origin and inventory source."""

    location_id: str
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    inventory_fuel: float = 0.0
    inventory_material: float = 0.0
    inventory_energy: float = 0.0
    energy_resource_type: str = ""
    energy_unit: str = ""


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
class TravelLinkRow(_SolverRow):
    """One directed travel-network edge between two locations."""

    link_id: str
    from_location_ref: str = ""
    to_location_ref: str = ""
    travel_time_s: float = 0.0
    distance_km: float = 0.0
    network_mode: str = "any"
    route_geometry: Any = dataclasses.field(default_factory=list)
    # Directed toll charged to traverse this link (EUR); 0 means untolled. Only
    # genuinely tolled segments carry a positive value.
    toll_eur: float = 0.0


@dataclasses.dataclass(frozen=True, slots=True)
class CostRateRow(_SolverRow):
    """A priced resource rate (fuel, material) with an optional validity window."""

    rate_id: str
    rate_type: str = ""
    unit_price: float = 0.0
    per_unit: str = ""
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None


@dataclasses.dataclass(frozen=True, slots=True)
class TaskRow(_SolverRow):
    """A unit of work to schedule (order)."""

    task_id: str
    order_ref: str = ""
    alternative_group_ref: str = ""
    location_ref: str = ""
    operation_type: str = ""
    area: float = 0.0
    # Work-area polygon ([lat, lon] vertices) of the specific region to work; a
    # sub-region of the site. Empty falls back to the whole site polygon.
    work_area_geometry: Any = dataclasses.field(default_factory=list)
    # Union of completed coverage passes ([lat, lon] vertices), subtracted from
    # the work area (with restricted areas) to leave the uncovered remainder.
    covered_geometry: Any = dataclasses.field(default_factory=list)
    # Generic work demand; preferred over area for duration estimation.
    work_quantity: float = 0.0
    work_quantity_unit: str = ""
    # Explicit effort override in minutes; wins over any quantity estimate.
    service_duration_min: float = 0.0
    # Workable ISO-8601 "from/to" interval strings the execution must start in.
    time_windows: Any = dataclasses.field(default_factory=list)
    # Predecessor task id that must be served before this task.
    depends_on_task_ref: str = ""
    # Mass the bundle must carry to the task; 0 means no load demand.
    load_demand: float = 0.0
    # Material code of the load demand ("" = unspecified aggregate material).
    load_material: str = ""
    # Pickup location of a paired pickup-and-delivery task; "" means the
    # load is carried from the depot.
    pickup_location_ref: str = ""
    deadline: Optional[str] = None
    penalty_per_day: float = 0.0
    priority_class: str = ""
    status: str = ""
    revenue: float = 0.0

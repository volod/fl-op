"""TypedDict definitions for cross-process pipeline contracts.

These types cross the multiprocessing.Pool boundary as plain Python dicts.
No Pydantic, no OR-Tools objects — only JSON-serialisable primitives.
"""

from typing import Any

from typing_extensions import Required, TypedDict


class ClusterSpec(TypedDict, total=False):
    cluster_id: Required[str]
    depot_id: Required[str]
    order_ids: Required[list[str]]
    # vehicle_id -> list of implement_ids pre-allocated to this cluster
    allocated_vehicle_implements: Required[dict[str, list[str]]]
    # sum of order.penalty_per_day for priority-ordering
    total_penalty_per_day: Required[float]


class FeasibleAssignment(TypedDict):
    vehicle_id: str
    implement_id: str
    order_id: str
    # gross_margin_estimate - repositioning_cost
    greedy_score: float
    # estimated gross revenue for this assignment
    gross_margin_estimate_eur: float
    # estimated repositioning fuel cost
    repositioning_cost_eur: float


class DispatchPackage(TypedDict):
    dispatch_id: str
    cluster_id: str
    vehicle_id: str
    implement_id: str
    operator_id: str
    order_id: str
    depot_id: str
    # ISO-8601 strings
    scheduled_start: str
    scheduled_end: str
    route_waypoints: list[dict[str, Any]]
    estimated_fuel_l: float
    estimated_fertilizer_kg: float
    estimated_margin_eur: float


class InfeasibleOrder(TypedDict):
    order_id: str
    cluster_id: str
    # machine-readable reason tag, e.g. "no_compatible_vehicle", "solver_timeout"
    reason: str
    # human-readable description
    detail: str

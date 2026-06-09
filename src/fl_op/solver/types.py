"""TypedDict definitions for the solver pipeline contracts.

Plain JSON-serialisable primitives only -- no Pydantic models, no OR-Tools
objects. Kept simple so the types remain easy to log, assert, and serialise.

These describe the solver's internal, domain-agnostic working rows: assets and
tasks are identified by their canonical ids, never by domain-specific column
names.
"""

from typing import Any

from typing_extensions import Required, TypedDict


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

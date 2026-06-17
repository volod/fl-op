"""Machine-readable per-cluster solve diagnostics.

Every cluster solve yields one plain JSON-serializable telemetry record:
model size, wall time, OR-Tools search status, objective values, the LNS
improvement delta, and an explicit time-limit flag. The pool aggregates the
records, the chain returns them on its result, the batch pipeline writes them
as ``solve_telemetry.json``, and plan scores carry the summary -- so solve
quality and timeouts are inspectable per run without log scraping.
"""

import logging
from typing import Any, Optional

from typing_extensions import Required, TypedDict

logger = logging.getLogger(__name__)

# Telemetry record statuses.
STATUS_SOLVED = "solved"                 # OR-Tools returned a solution
STATUS_NO_SOLUTION = "no_solution"       # no feasible solution within the limit
STATUS_EMPTY = "empty"                   # cluster carried no tasks
STATUS_INPUT_ERROR = "input_error"       # context preparation failed (data)
STATUS_WORKER_ERROR = "worker_error"     # worker raised or crashed
STATUS_POOL_TIMEOUT = "pool_timeout"     # worker exceeded the pool ceiling


class ClusterSolveTelemetry(TypedDict, total=False):
    """One cluster's machine-readable solve record."""

    cluster_id: Required[str]
    status: Required[str]
    n_tasks: int
    n_routing_vehicles: int
    solve_wall_s: float
    time_limit_s: int
    # OR-Tools routing search status name (ROUTING_SUCCESS, ...).
    routing_status: str
    hit_time_limit: bool
    objective_value: Optional[int]
    first_solution_objective: Optional[int]
    lns_attempted: bool
    lns_improved: bool
    lns_objective_delta: int
    lns_time_limit_s: int
    worker_max_rss_mb: float
    n_dispatched: int
    n_unserved: int
    # Primal resource-conflict attribution: the routing dimension running
    # tightest behind any dropped tasks (see solver/cluster/conflict.py).
    resource_conflict: dict[str, Any]
    detail: str


def routing_status_name(routing: Any) -> str:
    """Symbolic name of the model's search status, robust across versions."""
    try:
        code = routing.status()
    except Exception:  # noqa: BLE001 - diagnostics must never fail a solve
        return "unknown"
    try:
        from ortools.constraint_solver import routing_enums_pb2

        return routing_enums_pb2.RoutingSearchStatus.Value.Name(code)
    except Exception:  # noqa: BLE001 - older bindings expose model attributes
        for name in dir(routing):
            if name.startswith("ROUTING_"):
                try:
                    if getattr(routing, name) == code:
                        return name
                except Exception:  # noqa: BLE001
                    continue
        return str(code)


def summarize_cluster_telemetry(
    records: list[ClusterSolveTelemetry],
) -> dict[str, Any]:
    """Aggregate per-cluster records into a plan-score-sized summary."""
    statuses: dict[str, int] = {}
    binding_resources: dict[str, int] = {}
    for record in records:
        status = record.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        binding = (record.get("resource_conflict") or {}).get("binding_resource")
        # Only count clusters that actually dropped tasks, so the tally surfaces
        # where capacity was the constraint, not how often nothing bound.
        if binding and binding != "none":
            binding_resources[binding] = binding_resources.get(binding, 0) + 1
    rss_values = [
        float(r.get("worker_max_rss_mb", 0.0))
        for r in records
        if r.get("worker_max_rss_mb") is not None
    ]
    return {
        "n_clusters": len(records),
        "statuses": statuses,
        "binding_resources": binding_resources,
        "n_hit_time_limit": sum(1 for r in records if r.get("hit_time_limit")),
        "total_solve_wall_s": round(
            sum(float(r.get("solve_wall_s", 0.0)) for r in records), 3
        ),
        "n_lns_attempted": sum(1 for r in records if r.get("lns_attempted")),
        "n_lns_improved": sum(1 for r in records if r.get("lns_improved")),
        "total_lns_objective_delta": sum(
            int(r.get("lns_objective_delta", 0)) for r in records
        ),
        "max_worker_rss_mb": round(max(rss_values), 2) if rss_values else 0.0,
        "mean_worker_rss_mb": round(sum(rss_values) / len(rss_values), 2)
        if rss_values
        else 0.0,
    }

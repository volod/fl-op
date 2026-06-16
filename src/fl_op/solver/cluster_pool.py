"""Parallel cluster execution via ProcessPoolExecutor with spawn context.

Why processes, not threads
--------------------------
OR-Tools routing solver (pywrapcp) is SWIG-wrapped C++ without explicit
%nogil annotations, so SolveWithParameters holds the GIL for the entire
solve duration. Threads run sequentially during the solve phase and provide
no CPU parallelism where it matters most.

Why spawn, not fork
-------------------
OR-Tools internally spawns C++ threads (SAT sub-solver, search workers).
fork()ing a process that already owns live C++ threads is undefined
behaviour and causes deadlocks on macOS and random crashes on Linux.
spawn is the only safe start method for OR-Tools.

Amortising spawn cost with initializer
---------------------------------------
ProcessPoolExecutor(initializer=_pool_initializer) pays spawn cost exactly
N_WORKERS times at pool creation (all concurrent) rather than N_CLUSTERS
times. All cluster tasks dispatched to a warm worker pay zero import cost.

as_completed vs sequential ar.get(timeout=...)
-----------------------------------------------
as_completed() yields futures in completion order, so fast clusters are
collected immediately and the overall timeout is a ceiling on wall-clock
for the whole pool, not per-cluster multiplied by count.
"""

import concurrent.futures
import dataclasses
import logging
import multiprocessing
import os
import pathlib
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from typing import Any, Optional

from fl_op.canonical.enums import ReasonCode
from fl_op.core import constants
from fl_op.core.constants import CLUSTER_SOLVE_TIME_LIMIT_S, SOLVER_WORKERS
from fl_op.solver.cluster.routing import HeldWindows
from fl_op.solver.cost_rates import ResourcePrices
from fl_op.solver.enforcement import BlockedWindows
from fl_op.solver.travel_time import TravelLookup
from fl_op.solver.types import ClusterSpec

logger = logging.getLogger(__name__)

_SOLVER_GRACE_S = 30

_MEMINFO_PATH = pathlib.Path("/proc/meminfo")
_BYTES_PER_MB = 1024 * 1024


@dataclasses.dataclass(frozen=True)
class PoolSizing:
    """How the worker count was derived (recorded for diagnostics)."""

    n_workers: int
    cpu_cap: int
    memory_cap: Optional[int]
    available_memory_mb: Optional[float]
    estimated_worker_memory_mb: float
    explicit_override: bool


def available_memory_mb() -> Optional[float]:
    """Public accessor for currently available physical memory (MB), or None."""
    return _available_memory_mb()


def _available_memory_mb() -> Optional[float]:
    """Currently available physical memory; None when not measurable."""
    try:
        for line in _MEMINFO_PATH.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return float(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / _BYTES_PER_MB
    except (ValueError, OSError, AttributeError):
        return None


def _estimate_worker_memory_mb(clusters: list[ClusterSpec]) -> float:
    """Estimated peak memory of one worker solving the largest cluster.

    The routing model holds an n_nodes^2 time matrix plus one transit callback
    per routing vehicle, so the model footprint scales with
    n_nodes^2 x (n_vehicles + 1) cells on top of the worker's base footprint.
    """
    max_nodes = 1
    max_vehicles = 1
    for cluster in clusters:
        max_nodes = max(max_nodes, len(cluster.get("task_ids", [])) + 1)
        max_vehicles = max(
            max_vehicles, len(cluster.get("allocated_prime_related", {})) or 1
        )
    cells = max_nodes * max_nodes * (max_vehicles + 1)
    from fl_op.solver.performance_feedback import (
        calibrated_memory_model,
        calibrated_worker_memory_mb,
    )

    model = calibrated_memory_model()
    if model is not None:
        # Data-driven fit (base MB plus MB per model cell) from retained worker
        # RSS feedback supersedes the hardcoded base/per-cell constants.
        base_mb, mb_per_cell = model
        estimated_mb = base_mb + mb_per_cell * cells
    else:
        estimated_mb = constants.SOLVER_WORKER_BASE_MEMORY_MB + (
            cells * constants.SOLVER_MODEL_BYTES_PER_CELL / _BYTES_PER_MB
        )
    return calibrated_worker_memory_mb(estimated_mb)


def compute_pool_sizing(clusters: list[ClusterSpec]) -> PoolSizing:
    """Derive the worker count from cluster count, CPUs, and available memory.

    An explicit SOLVER_WORKERS > 0 always wins. In auto mode the CPU-derived
    cap is additionally bounded by how many estimated worker footprints fit
    into the available memory (keeping SOLVER_MEMORY_HEADROOM_PCT free); when
    memory cannot be measured, sizing stays CPU-based.
    """
    cpu_cap = os.cpu_count() or 1
    estimated_mb = _estimate_worker_memory_mb(clusters)
    if SOLVER_WORKERS > 0:
        return PoolSizing(
            n_workers=max(1, min(len(clusters), SOLVER_WORKERS)),
            cpu_cap=cpu_cap,
            memory_cap=None,
            available_memory_mb=None,
            estimated_worker_memory_mb=estimated_mb,
            explicit_override=True,
        )

    available_mb = _available_memory_mb()
    memory_cap: Optional[int] = None
    if available_mb is not None:
        usable_mb = available_mb * (1.0 - constants.SOLVER_MEMORY_HEADROOM_PCT / 100.0)
        memory_cap = max(1, int(usable_mb / estimated_mb))

    n_workers = max(
        1,
        min(
            len(clusters),
            cpu_cap,
            memory_cap if memory_cap is not None else cpu_cap,
        ),
    )
    return PoolSizing(
        n_workers=n_workers,
        cpu_cap=cpu_cap,
        memory_cap=memory_cap,
        available_memory_mb=available_mb,
        estimated_worker_memory_mb=estimated_mb,
        explicit_override=False,
    )


def _pool_initializer() -> None:
    """Run once in each worker process before any task arrives.

    Pre-imports OR-Tools and the cluster solver so the first task pays no
    import cost.
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # noqa: F401
    import fl_op.solver.cluster_solver  # noqa: F401


def _worker_fn(
    cluster_dict: dict[str, Any],
    orders: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    implements: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    depots: list[dict[str, Any]],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
    held_windows: Optional[HeldWindows] = None,
    travel_lookup: Optional[TravelLookup] = None,
    solve_time_limit_s: Optional[int] = None,
    now_epoch: Optional[int] = None,
    weather_blocked: Optional[BlockedWindows] = None,
    resource_prices: Optional[ResourcePrices] = None,
    optimization_objective: str = constants.OBJECTIVE_MODE_COST,
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    """Top-level worker function - module-level def required for pickling."""
    from fl_op.solver.cluster_solver import solve_cluster_instrumented
    return solve_cluster_instrumented(
        cluster_dict, orders, vehicles, implements, fields, depots,
        greedy_assignment, vehicle_index, implement_index, held_windows,
        travel_lookup, solve_time_limit_s, now_epoch, weather_blocked,
        resource_prices, optimization_objective,
    )


def pool_solve(
    clusters: list[ClusterSpec],
    orders: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    implements: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    depots: list[dict[str, Any]],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
    held_windows: Optional[HeldWindows] = None,
    travel_lookup: Optional[TravelLookup] = None,
    solve_time_limit_s: Optional[int] = None,
    now_epoch: Optional[int] = None,
    weather_blocked: Optional[BlockedWindows] = None,
    resource_prices: Optional[ResourcePrices] = None,
    lns_time_limit_s: Optional[int] = None,
    optimization_objective: str = constants.OBJECTIVE_MODE_COST,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Solve all clusters in parallel.

    Returns (all_dispatch, all_infeasible, cluster_telemetry): one
    machine-readable solve record per cluster, including synthesized records
    for crashed workers and pool-timeout cancellations. ``now_epoch`` is the
    planning time origin shared by every worker (snapshot effective time);
    None lets each worker fall back to wall-clock now. ``weather_blocked``
    maps task ids to blocked epoch intervals (non-compliant forecast windows)
    the routing model must keep execution out of. ``resource_prices`` are the
    resolved energy/material prices for arc costs and dispatch margins.
    ``optimization_objective`` keeps cost optimization as the default and can
    switch routing costs to travel/service/completion time.
    """
    if not clusters:
        return [], [], []

    sizing = compute_pool_sizing(clusters)
    n_workers = sizing.n_workers

    cluster_dicts = [dict(c) for c in clusters]
    # The optional LNS improvement pass adds its own per-cluster budget, now
    # scaled from retained objective-delta feedback.
    lns_budget_s = _assign_lns_budgets(cluster_dicts, lns_time_limit_s)
    cluster_limit_s = (
        solve_time_limit_s
        if solve_time_limit_s is not None
        else CLUSTER_SOLVE_TIME_LIMIT_S
    )
    task_cap_s = cluster_limit_s + lns_budget_s + _SOLVER_GRACE_S
    n_rounds = (len(clusters) + n_workers - 1) // n_workers
    overall_timeout = (n_rounds + 1) * task_cap_s

    logger.info(
        "Cluster pool: %d clusters, %d workers (cpu cap %d, memory cap %s, "
        "available %s MB, est %.0f MB/worker%s), overall timeout %ds",
        len(clusters),
        n_workers,
        sizing.cpu_cap,
        sizing.memory_cap if sizing.memory_cap is not None else "n/a",
        f"{sizing.available_memory_mb:.0f}" if sizing.available_memory_mb else "n/a",
        sizing.estimated_worker_memory_mb,
        ", explicit SOLVER_WORKERS" if sizing.explicit_override else "",
        overall_timeout,
    )

    from fl_op.solver.solve_telemetry import STATUS_POOL_TIMEOUT, STATUS_WORKER_ERROR

    all_dispatch: list[dict[str, Any]] = []
    all_infeasible: list[dict[str, Any]] = []
    cluster_telemetry: list[dict[str, Any]] = []
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx, initializer=_pool_initializer) as executor:
        future_to_cluster: dict[
            Future[tuple[list[dict], list[dict], dict[str, Any]]], dict[str, Any]
        ] = {
            executor.submit(
                _worker_fn,
                cd, orders, vehicles, implements, fields, depots,
                greedy_assignment, vehicle_index, implement_index, held_windows,
                travel_lookup, solve_time_limit_s, now_epoch, weather_blocked,
                resource_prices, optimization_objective,
            ): cd
            for cd in cluster_dicts
        }

        try:
            for future in as_completed(future_to_cluster, timeout=overall_timeout):
                cd = future_to_cluster[future]
                cluster_id = cd.get("cluster_id", "?")
                try:
                    dispatch, infeasible, telemetry = future.result()
                    all_dispatch.extend(dispatch)
                    all_infeasible.extend(infeasible)
                    cluster_telemetry.append(dict(telemetry))
                except Exception as exc:
                    logger.error("Cluster %s worker crashed: %s", cluster_id, exc)
                    all_infeasible.extend(
                        {
                            "task_id": oid,
                            "cluster_id": cluster_id,
                            "reason_code": ReasonCode.UNKNOWN.value,
                            "detail": str(exc),
                        }
                        for oid in cd.get("task_ids", [])
                    )
                    cluster_telemetry.append(
                        {
                            "cluster_id": cluster_id,
                            "status": STATUS_WORKER_ERROR,
                            "n_tasks": len(cd.get("task_ids", [])),
                            "detail": str(exc),
                        }
                    )

        except concurrent.futures.TimeoutError:
            for future, cd in future_to_cluster.items():
                if not future.done():
                    cluster_id = cd.get("cluster_id", "?")
                    logger.error(
                        "Cluster %s stalled past overall timeout (%ds), cancelling",
                        cluster_id, overall_timeout,
                    )
                    future.cancel()
                    all_infeasible.extend(
                        {
                            "task_id": oid,
                            "cluster_id": cluster_id,
                            "reason_code": ReasonCode.OPTIMIZATION_TRADEOFF.value,
                            "detail": f"worker did not complete within {overall_timeout}s",
                        }
                        for oid in cd.get("task_ids", [])
                    )
                    cluster_telemetry.append(
                        {
                            "cluster_id": cluster_id,
                            "status": STATUS_POOL_TIMEOUT,
                            "n_tasks": len(cd.get("task_ids", [])),
                            "hit_time_limit": True,
                            "time_limit_s": overall_timeout,
                            "detail": f"worker did not complete within {overall_timeout}s",
                        }
                    )

    from fl_op.solver.performance_feedback import record_solver_feedback

    record_solver_feedback(cluster_telemetry)
    return all_dispatch, all_infeasible, cluster_telemetry


def _assign_lns_budgets(
    cluster_dicts: list[dict[str, Any]],
    lns_time_limit_s: Optional[int] = None,
) -> int:
    """Stamp per-cluster LNS budgets; return the largest budget assigned."""
    base_budget = (
        lns_time_limit_s
        if lns_time_limit_s is not None
        else (
            constants.CLUSTER_LNS_TIME_LIMIT_S
            if constants.CLUSTER_LNS_ENABLED
            else 0
        )
    )
    if base_budget <= 0:
        return 0
    from fl_op.solver.performance_feedback import lns_budget_multiplier

    multiplier = lns_budget_multiplier()
    max_budget = 0
    for cluster in cluster_dicts:
        total_penalty = float(cluster.get("total_penalty_per_day", 0.0) or 0.0)
        if total_penalty < constants.CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY:
            continue
        budget = max(1, int(round(base_budget * multiplier)))
        cluster["lns_time_limit_s"] = budget
        max_budget = max(max_budget, budget)
    return max_budget

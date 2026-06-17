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

# Floor on a single cluster's share of a value-weighted operator-sharing group
# budget, so a low-penalty cluster in a capped group still gets usable search.
_GROUP_MIN_CLUSTER_TIME_S = 5

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


@dataclasses.dataclass(frozen=True)
class _SolveArgs:
    """Per-cluster solve inputs shared by the parallel and sequential paths."""

    orders: list[dict[str, Any]]
    vehicles: list[dict[str, Any]]
    implements: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    depots: list[dict[str, Any]]
    greedy_assignment: dict[str, tuple[int, int]]
    vehicle_index: dict[str, int]
    implement_index: dict[str, int]
    held_windows: Optional[HeldWindows]
    travel_lookup: Optional[TravelLookup]
    solve_time_limit_s: Optional[int]
    now_epoch: Optional[int]
    weather_blocked: Optional[BlockedWindows]
    resource_prices: Optional[ResourcePrices]
    optimization_objective: str


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

    cluster_dicts = [dict(c) for c in clusters]
    # The optional LNS improvement pass adds its own per-cluster budget, now
    # scaled from retained objective-delta feedback.
    lns_budget_s = _assign_lns_budgets(cluster_dicts, lns_time_limit_s)
    args = _SolveArgs(
        orders=orders, vehicles=vehicles, implements=implements, fields=fields,
        depots=depots, greedy_assignment=greedy_assignment,
        vehicle_index=vehicle_index, implement_index=implement_index,
        held_windows=held_windows, travel_lookup=travel_lookup,
        solve_time_limit_s=solve_time_limit_s, now_epoch=now_epoch,
        weather_blocked=weather_blocked, resource_prices=resource_prices,
        optimization_objective=optimization_objective,
    )

    # Operator-sharing groups (OPERATOR_SHARING_SEQUENTIAL only) solve
    # sequentially; everything else runs in the parallel pool as before. Off by
    # default, so there are no groups and the parallel path is unchanged.
    groups = (
        _operator_sharing_groups(cluster_dicts)
        if constants.OPERATOR_SHARING_SEQUENTIAL
        else []
    )
    grouped_ids = {cid for group in groups for cid in group}
    independent = [cd for cd in cluster_dicts if cd["cluster_id"] not in grouped_ids]

    all_dispatch, all_infeasible, cluster_telemetry = _solve_parallel_batch(
        independent, lns_budget_s, args
    )
    if groups:
        by_id = {cd["cluster_id"]: cd for cd in cluster_dicts}
        seq_dispatch, seq_infeasible, seq_telemetry = _solve_sequential_groups(
            groups, by_id, args
        )
        all_dispatch.extend(seq_dispatch)
        all_infeasible.extend(seq_infeasible)
        cluster_telemetry.extend(seq_telemetry)

    from fl_op.solver.performance_feedback import record_solver_feedback

    record_solver_feedback(cluster_telemetry)
    return all_dispatch, all_infeasible, cluster_telemetry


def _solve_parallel_batch(
    cluster_dicts: list[dict[str, Any]],
    lns_budget_s: int,
    args: "_SolveArgs",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Solve a set of independent clusters in the process pool (no feedback)."""
    if not cluster_dicts:
        return [], [], []

    sizing = compute_pool_sizing(cluster_dicts)
    n_workers = sizing.n_workers
    cluster_limit_s = (
        args.solve_time_limit_s
        if args.solve_time_limit_s is not None
        else CLUSTER_SOLVE_TIME_LIMIT_S
    )
    task_cap_s = cluster_limit_s + lns_budget_s + _SOLVER_GRACE_S
    n_rounds = (len(cluster_dicts) + n_workers - 1) // n_workers
    overall_timeout = (n_rounds + 1) * task_cap_s

    logger.info(
        "Cluster pool: %d clusters, %d workers (cpu cap %d, memory cap %s, "
        "available %s MB, est %.0f MB/worker%s), overall timeout %ds",
        len(cluster_dicts),
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
                cd, args.orders, args.vehicles, args.implements, args.fields,
                args.depots, args.greedy_assignment, args.vehicle_index,
                args.implement_index, args.held_windows, args.travel_lookup,
                args.solve_time_limit_s, args.now_epoch, args.weather_blocked,
                args.resource_prices, args.optimization_objective,
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

    return all_dispatch, all_infeasible, cluster_telemetry


def _solve_sequential_groups(
    groups: list[list[str]],
    by_id: dict[str, dict[str, Any]],
    args: "_SolveArgs",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Solve each operator-sharing group, internally sequential.

    Within a group the clusters solve in value order (highest total penalty
    first), and each solved cluster's committed intervals for the shared backup
    operators feed the remaining clusters as in-model operator breaks, so the
    shared operator is never double-booked. ``OPERATOR_SHARING_GROUP_TIME_LIMIT_S``
    bounds a group's total solve time (value-weighted across its clusters) so a
    large group cannot run unboundedly. Groups share no operator, so several are
    solved concurrently in the process pool; a lone group stays in-process.
    """
    per_cluster_limit = (
        args.solve_time_limit_s
        if args.solve_time_limit_s is not None
        else CLUSTER_SOLVE_TIME_LIMIT_S
    )
    group_total = constants.OPERATOR_SHARING_GROUP_TIME_LIMIT_S
    prepared: list[tuple[list[dict[str, Any]], dict[str, int]]] = []
    for group in groups:
        ordered_ids = sorted(
            group,
            key=lambda cid: (-float(by_id[cid].get("total_penalty_per_day", 0.0)), cid),
        )
        ordered = [by_id[cid] for cid in ordered_ids]
        budgets = _group_budgets(ordered, per_cluster_limit, group_total)
        logger.info(
            "Operator-sharing group of %d clusters (budgets %s): %s",
            len(ordered),
            budgets,
            ", ".join(ordered_ids),
        )
        prepared.append((ordered, budgets))

    if len(prepared) == 1:
        ordered, budgets = prepared[0]
        return _solve_group(ordered, budgets, args)
    return _solve_groups_parallel(prepared, args)


def _group_budgets(
    ordered_clusters: list[dict[str, Any]],
    per_cluster_limit: int,
    group_total_limit: int,
) -> dict[str, int]:
    """Per-cluster solve-time budget for one group.

    With no group cap, every cluster keeps the per-cluster limit. With a cap, the
    budget is split across the group's clusters in proportion to a value x
    difficulty weight (:func:`_cluster_budget_weight`), so a cluster that is both
    valuable and hard to solve gets the most search, floored so none is starved.
    """
    if group_total_limit <= 0:
        return {cd["cluster_id"]: per_cluster_limit for cd in ordered_clusters}
    weights = [_cluster_budget_weight(cd) for cd in ordered_clusters]
    total_weight = sum(weights) or float(len(ordered_clusters))
    budgets: dict[str, int] = {}
    for cd, weight in zip(ordered_clusters, weights):
        share = int(group_total_limit * weight / total_weight)
        budgets[cd["cluster_id"]] = max(_GROUP_MIN_CLUSTER_TIME_S, share)
    return budgets


def _cluster_budget_weight(cd: dict[str, Any]) -> float:
    """Value x difficulty weight for a cluster's share of a group time budget.

    Value is the cluster's total penalty (what is at stake if its tasks drop);
    difficulty is a routing-model-size proxy -- task count times vehicle count,
    the dimensions that drive search effort -- so extra budget lands on the
    clusters where more search actually changes the solution, not on a trivially
    solved one that happens to carry a high penalty.
    """
    penalty = max(1.0, float(cd.get("total_penalty_per_day", 0.0) or 0.0))
    n_tasks = max(1, len(cd.get("task_ids", [])))
    n_vehicles = max(1, len(cd.get("allocated_prime_related", {})))
    difficulty = n_tasks * (n_vehicles + 1)
    return penalty * difficulty


def _solve_group(
    ordered_clusters: list[dict[str, Any]],
    budgets: dict[str, int],
    args: "_SolveArgs",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Solve one group's clusters in order, feeding committed operator intervals
    forward as in-model breaks. Module-level so it is picklable for the pool."""
    from fl_op.solver.cluster_solver import solve_cluster_instrumented

    base_held = args.held_windows or {}
    dispatch_all: list[dict[str, Any]] = []
    infeasible_all: list[dict[str, Any]] = []
    telemetry_all: list[dict[str, Any]] = []
    operator_held: dict[str, list[tuple[int, int]]] = {}
    for cd in ordered_clusters:
        shared_ops = set(cd.get("shared_backup_operators", []))
        dispatch, infeasible, telemetry = solve_cluster_instrumented(
            cd, args.orders, args.vehicles, args.implements, args.fields,
            args.depots, args.greedy_assignment, args.vehicle_index,
            args.implement_index, _merge_held_windows(base_held, operator_held),
            args.travel_lookup, budgets.get(cd["cluster_id"]), args.now_epoch,
            args.weather_blocked, args.resource_prices,
            args.optimization_objective,
        )
        dispatch_all.extend(dispatch)
        infeasible_all.extend(infeasible)
        telemetry_all.append(dict(telemetry))
        for operator_id, intervals in _operator_intervals(dispatch).items():
            if operator_id in shared_ops:
                operator_held.setdefault(operator_id, []).extend(intervals)
    return dispatch_all, infeasible_all, telemetry_all


def _solve_groups_parallel(
    prepared: list[tuple[list[dict[str, Any]], dict[str, int]]],
    args: "_SolveArgs",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Solve independent operator-sharing groups concurrently (each sequential)."""
    from fl_op.solver.solve_telemetry import STATUS_WORKER_ERROR

    n_workers = max(1, min(len(prepared), os.cpu_count() or 1))
    overall_timeout = (
        max(sum(budgets.values()) for _ordered, budgets in prepared)
        + _SOLVER_GRACE_S
    ) * (len(prepared) + 1)
    logger.info(
        "Operator-sharing: %d independent groups across %d workers, timeout %ds",
        len(prepared),
        n_workers,
        overall_timeout,
    )
    dispatch_all: list[dict[str, Any]] = []
    infeasible_all: list[dict[str, Any]] = []
    telemetry_all: list[dict[str, Any]] = []
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=n_workers, mp_context=ctx, initializer=_pool_initializer
    ) as executor:
        future_to_group = {
            executor.submit(_solve_group, ordered, budgets, args): ordered
            for ordered, budgets in prepared
        }
        try:
            for future in as_completed(future_to_group, timeout=overall_timeout):
                ordered = future_to_group[future]
                try:
                    dispatch, infeasible, telemetry = future.result()
                    dispatch_all.extend(dispatch)
                    infeasible_all.extend(infeasible)
                    telemetry_all.extend(telemetry)
                except Exception as exc:
                    logger.error("Operator-sharing group worker crashed: %s", exc)
                    failed_infeasible, failed_telemetry = _mark_group_failed(
                        ordered, STATUS_WORKER_ERROR, str(exc)
                    )
                    infeasible_all.extend(failed_infeasible)
                    telemetry_all.extend(failed_telemetry)
        except concurrent.futures.TimeoutError:
            for future, ordered in future_to_group.items():
                if not future.done():
                    future.cancel()
                    detail = f"group did not complete within {overall_timeout}s"
                    failed_infeasible, failed_telemetry = _mark_group_failed(
                        ordered, STATUS_WORKER_ERROR, detail
                    )
                    infeasible_all.extend(failed_infeasible)
                    telemetry_all.extend(failed_telemetry)
    return dispatch_all, infeasible_all, telemetry_all


def _mark_group_failed(
    ordered_clusters: list[dict[str, Any]],
    status: str,
    detail: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Synthesize infeasible + telemetry records for a crashed/timed-out group."""
    infeasible = [
        {
            "task_id": oid,
            "cluster_id": cd.get("cluster_id", "?"),
            "reason_code": ReasonCode.UNKNOWN.value,
            "detail": detail,
        }
        for cd in ordered_clusters
        for oid in cd.get("task_ids", [])
    ]
    telemetry = [
        {
            "cluster_id": cd.get("cluster_id", "?"),
            "status": status,
            "n_tasks": len(cd.get("task_ids", [])),
            "detail": detail,
        }
        for cd in ordered_clusters
    ]
    return infeasible, telemetry


def _operator_sharing_groups(cluster_dicts: list[dict[str, Any]]) -> list[list[str]]:
    """Connected components of clusters that share an overlap-claimed operator."""
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        parent[find(b)] = find(a)

    operator_clusters: dict[str, list[str]] = {}
    for cd in cluster_dicts:
        for operator_id in cd.get("shared_backup_operators", []):
            operator_clusters.setdefault(operator_id, []).append(cd["cluster_id"])
    for cluster_ids in operator_clusters.values():
        for other in cluster_ids[1:]:
            union(cluster_ids[0], other)

    groups: dict[str, list[str]] = {}
    for cd in cluster_dicts:
        cluster_id = cd["cluster_id"]
        if cluster_id in parent:
            groups.setdefault(find(cluster_id), []).append(cluster_id)
    return [members for members in groups.values() if len(members) >= 2]


def _operator_intervals(
    dispatch: list[dict[str, Any]],
) -> dict[str, list[tuple[int, int]]]:
    """Committed [start, end] epoch intervals per operator from a cluster's plan."""
    intervals: dict[str, list[tuple[int, int]]] = {}
    for package in dispatch:
        operator_id = str(package.get("operator_asset_id") or "")
        if not operator_id:
            continue
        start = _epoch_or_none(package.get("scheduled_start"))
        end = _epoch_or_none(package.get("scheduled_end"))
        if start is not None and end is not None and end > start:
            intervals.setdefault(operator_id, []).append((start, end))
    return intervals


def _epoch_or_none(value: Any) -> Optional[int]:
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(str(value)).timestamp())
    except (TypeError, ValueError):
        return None


def _merge_held_windows(
    base: HeldWindows,
    extra: dict[str, list[tuple[int, int]]],
) -> HeldWindows:
    """Combine the run's held windows with forward-fed operator intervals."""
    if not extra:
        return dict(base)
    merged: dict[str, list[tuple[int, int]]] = {k: list(v) for k, v in base.items()}
    for asset_id, windows in extra.items():
        merged.setdefault(asset_id, []).extend(windows)
    return merged


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

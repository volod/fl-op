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
import logging
import multiprocessing
import os
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from typing import Any

from fl_op.canonical.enums import ReasonCode
from fl_op.core.constants import CLUSTER_SOLVE_TIME_LIMIT_S, SOLVER_WORKERS
from fl_op.models.types import ClusterSpec

logger = logging.getLogger(__name__)

_SOLVER_GRACE_S = 30


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
) -> tuple[list[dict], list[dict]]:
    """Top-level worker function — module-level def required for pickling."""
    from fl_op.solver.cluster_solver import solve_cluster
    return solve_cluster(
        cluster_dict, orders, vehicles, implements, fields, depots,
        greedy_assignment, vehicle_index, implement_index,
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Solve all clusters in parallel; return (all_dispatch, all_infeasible)."""
    if not clusters:
        return [], []

    cpu_count = os.cpu_count() or 1
    n_workers = max(1, min(
        len(clusters),
        SOLVER_WORKERS if SOLVER_WORKERS > 0 else cpu_count,
    ))

    task_cap_s = CLUSTER_SOLVE_TIME_LIMIT_S + _SOLVER_GRACE_S
    n_rounds = (len(clusters) + n_workers - 1) // n_workers
    overall_timeout = (n_rounds + 1) * task_cap_s

    logger.info(
        "Cluster pool: %d clusters, %d workers, overall timeout %ds",
        len(clusters), n_workers, overall_timeout,
    )

    all_dispatch: list[dict[str, Any]] = []
    all_infeasible: list[dict[str, Any]] = []
    cluster_dicts = [dict(c) for c in clusters]

    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx, initializer=_pool_initializer) as executor:
        future_to_cluster: dict[Future[tuple[list[dict], list[dict]]], dict[str, Any]] = {
            executor.submit(
                _worker_fn,
                cd, orders, vehicles, implements, fields, depots,
                greedy_assignment, vehicle_index, implement_index,
            ): cd
            for cd in cluster_dicts
        }

        try:
            for future in as_completed(future_to_cluster, timeout=overall_timeout):
                cd = future_to_cluster[future]
                cluster_id = cd.get("cluster_id", "?")
                try:
                    dispatch, infeasible = future.result()
                    all_dispatch.extend(dispatch)
                    all_infeasible.extend(infeasible)
                except Exception as exc:
                    logger.error("Cluster %s worker crashed: %s", cluster_id, exc)
                    all_infeasible.extend(
                        {
                            "order_id": oid,
                            "cluster_id": cluster_id,
                            "reason_code": ReasonCode.UNKNOWN.value,
                            "detail": str(exc),
                        }
                        for oid in cd.get("order_ids", [])
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
                            "order_id": oid,
                            "cluster_id": cluster_id,
                            "reason_code": ReasonCode.OPTIMIZATION_TRADEOFF.value,
                            "detail": f"worker did not complete within {overall_timeout}s",
                        }
                        for oid in cd.get("order_ids", [])
                    )

    return all_dispatch, all_infeasible

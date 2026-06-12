"""OR-Tools routing library cluster solver public wrapper.

Accepts and returns plain Python dicts only so the function is safe to call
across a ProcessPoolExecutor(spawn) boundary. The routing model is built and
destroyed inside helper modules; no shared OR-Tools state persists between calls.
"""

import logging
from typing import Any, Optional

from fl_op.canonical.enums import ReasonCode
from fl_op.solver.cluster.infeasible import mark_all_infeasible
from fl_op.solver.cluster.routing import HeldWindows
from fl_op.solver.cluster.solve import solve_cluster_inner
from fl_op.solver.cost_rates import ResourcePrices
from fl_op.solver.enforcement import BlockedWindows
from fl_op.solver.solve_telemetry import STATUS_WORKER_ERROR, ClusterSolveTelemetry
from fl_op.solver.travel_time import TravelLookup

logger = logging.getLogger(__name__)


def solve_cluster_instrumented(
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
) -> tuple[list[dict], list[dict], ClusterSolveTelemetry]:
    """Solve one cluster with machine-readable diagnostics.

    Returns (dispatch_packages, infeasible_orders, solve_telemetry). Always
    returns a 3-tuple, even when no solution is found. Never raises.
    """
    try:
        dispatch, infeasible, telemetry = solve_cluster_inner(
            cluster_dict,
            orders,
            vehicles,
            implements,
            fields,
            depots,
            greedy_assignment,
            vehicle_index,
            held_windows,
            travel_lookup,
            solve_time_limit_s,
            now_epoch,
            weather_blocked,
            resource_prices,
        )
        _stamp_worker_rss(telemetry)
        return dispatch, infeasible, telemetry
    except Exception as exc:
        logger.error(
            "Cluster %s solver exception: %s",
            cluster_dict.get("cluster_id", "?"),
            exc,
            exc_info=True,
        )
        dispatch, infeasible = mark_all_infeasible(
            cluster_dict,
            ReasonCode.UNKNOWN,
            f"unhandled exception: {exc}",
        )
        telemetry: ClusterSolveTelemetry = {
            "cluster_id": cluster_dict.get("cluster_id", ""),
            "status": STATUS_WORKER_ERROR,
            "n_tasks": len(cluster_dict.get("task_ids", [])),
            "n_dispatched": 0,
            "n_unserved": len(infeasible),
            "detail": str(exc),
        }
        _stamp_worker_rss(telemetry)
        return dispatch, infeasible, telemetry


def solve_cluster(
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
) -> tuple[list[dict], list[dict]]:
    """Solve one geographic cluster; return (dispatch_packages, infeasible_orders).

    Always returns a 2-tuple even when no solution is found. Never raises.
    The pool uses solve_cluster_instrumented to additionally collect the
    machine-readable solve telemetry.
    """
    dispatch, infeasible, _ = solve_cluster_instrumented(
        cluster_dict,
        orders,
        vehicles,
        implements,
        fields,
        depots,
        greedy_assignment,
        vehicle_index,
        implement_index,
        held_windows,
        travel_lookup,
        solve_time_limit_s,
        now_epoch,
        weather_blocked,
        resource_prices,
    )
    return dispatch, infeasible


def _stamp_worker_rss(telemetry: ClusterSolveTelemetry) -> None:
    try:
        from fl_op.core.telemetry import current_process_max_rss_mb

        telemetry["worker_max_rss_mb"] = round(current_process_max_rss_mb(), 2)
    except Exception:  # noqa: BLE001 - diagnostics must never fail a solve
        pass


__all__ = ["solve_cluster", "solve_cluster_instrumented"]

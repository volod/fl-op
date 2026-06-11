"""Top-level inner solve flow for one prepared cluster."""

import logging
from typing import Any, Optional

from fl_op.solver.cluster.context import prepare_cluster_context
from fl_op.solver.cluster.routing import HeldWindows, solve_routing_context
from fl_op.solver.solve_telemetry import (
    STATUS_EMPTY,
    STATUS_INPUT_ERROR,
    ClusterSolveTelemetry,
)
from fl_op.solver.travel_time import TravelLookup

logger = logging.getLogger(__name__)


def solve_cluster_inner(
    cluster_dict: dict[str, Any],
    all_orders: list[dict[str, Any]],
    all_vehicles: list[dict[str, Any]],
    all_implements: list[dict[str, Any]],
    all_fields: list[dict[str, Any]],
    all_depots: list[dict[str, Any]],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    held_windows: Optional[HeldWindows] = None,
    travel_lookup: Optional[TravelLookup] = None,
    solve_time_limit_s: Optional[int] = None,
) -> tuple[list[dict], list[dict], ClusterSolveTelemetry]:
    """Prepare and solve one cluster.

    Returns (dispatch_packages, infeasible_orders, solve_telemetry).
    """
    context, early_result = prepare_cluster_context(
        cluster_dict,
        all_orders,
        all_vehicles,
        all_implements,
        all_fields,
        all_depots,
        travel_lookup,
    )
    if early_result is not None:
        dispatch, infeasible = early_result
        telemetry: ClusterSolveTelemetry = {
            "cluster_id": cluster_dict.get("cluster_id", ""),
            "status": STATUS_EMPTY if not infeasible else STATUS_INPUT_ERROR,
            "n_tasks": len(cluster_dict.get("task_ids", [])),
            "n_dispatched": 0,
            "n_unserved": len(infeasible),
            "detail": infeasible[0].get("detail", "") if infeasible else "no tasks",
        }
        return dispatch, infeasible, telemetry
    assert context is not None

    dispatch_packages, infeasible_orders, telemetry = solve_routing_context(
        context,
        cluster_dict,
        greedy_assignment,
        vehicle_index,
        held_windows,
        solve_time_limit_s,
    )
    logger.debug(
        "Cluster %s: %d dispatched, %d infeasible",
        context.cluster_id,
        len(dispatch_packages),
        len(infeasible_orders),
    )
    return dispatch_packages, infeasible_orders, telemetry

"""Top-level inner solve flow for one prepared cluster."""

import logging
from typing import Any, Optional

from fl_op.solver.cluster.context import prepare_cluster_context
from fl_op.solver.cluster.routing import HeldWindows, solve_routing_context

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
) -> tuple[list[dict], list[dict]]:
    """Prepare and solve one cluster, returning dispatch and infeasibility rows."""
    context, early_result = prepare_cluster_context(
        cluster_dict,
        all_orders,
        all_vehicles,
        all_implements,
        all_fields,
        all_depots,
    )
    if early_result is not None:
        return early_result
    assert context is not None

    dispatch_packages, infeasible_orders = solve_routing_context(
        context,
        cluster_dict,
        greedy_assignment,
        vehicle_index,
        held_windows,
    )
    logger.debug(
        "Cluster %s: %d dispatched, %d infeasible",
        context.cluster_id,
        len(dispatch_packages),
        len(infeasible_orders),
    )
    return dispatch_packages, infeasible_orders

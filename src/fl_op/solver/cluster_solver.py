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

logger = logging.getLogger(__name__)


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
) -> tuple[list[dict], list[dict]]:
    """Solve one geographic cluster; return (dispatch_packages, infeasible_orders).

    Always returns a 2-tuple even when no solution is found. Never raises.
    """
    try:
        return solve_cluster_inner(
            cluster_dict,
            orders,
            vehicles,
            implements,
            fields,
            depots,
            greedy_assignment,
            vehicle_index,
            held_windows,
        )
    except Exception as exc:
        logger.error(
            "Cluster %s solver exception: %s",
            cluster_dict.get("cluster_id", "?"),
            exc,
            exc_info=True,
        )
        return mark_all_infeasible(
            cluster_dict,
            ReasonCode.UNKNOWN,
            f"unhandled exception: {exc}",
        )


__all__ = ["solve_cluster"]

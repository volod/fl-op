"""OR-Tools routing model construction and solve for one prepared cluster."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.canonical.enums import ReasonCode
from fl_op.core import constants
from fl_op.solver.cluster.context import ClusterContext
from fl_op.solver.cluster.infeasible import mark_all_infeasible, unserved_orders
from fl_op.solver.cluster.penalties import order_drop_penalty_s
from fl_op.solver.routing_model import (
    _build_initial_routes,
    _build_node_geometry,
    _extract_dispatch_packages,
)
from fl_op.solver.travel_time import _estimate_operation_seconds

logger = logging.getLogger(__name__)

_ROUTING_HORIZON_S = 30 * 24 * 3600

# Vehicle asset_id -> [(start_epoch_s, end_epoch_s), ...] busy intervals from
# held (frozen / carried-forward) assignments of a rolling plan.
HeldWindows = dict[str, list[tuple[int, int]]]


def solve_routing_context(
    context: ClusterContext,
    cluster_dict: dict[str, Any],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    held_windows: Optional[HeldWindows] = None,
) -> tuple[list[dict], list[dict]]:
    """Build, solve, and extract one OR-Tools routing model."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    node_lats, node_lons, time_matrix = _build_node_geometry(
        context.cluster_orders,
        context.field_map,
        context.depot_lat,
        context.depot_lon,
    )
    manager = pywrapcp.RoutingIndexManager(
        len(node_lats),
        len(context.routing_vehicles),
        0,
    )
    routing = pywrapcp.RoutingModel(manager)
    _add_arc_costs(routing, manager, time_matrix)
    service_times = _vehicle_service_times(context)
    time_dim = _add_time_dimension(routing, manager, time_matrix, service_times)

    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    _add_order_windows_and_disjunctions(routing, manager, time_dim, context, now_epoch)
    _add_precedence_constraints(routing, manager, time_dim, context, service_times)
    _add_held_vehicle_breaks(
        routing, time_dim, context, service_times, held_windows, now_epoch
    )

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search_params.time_limit.seconds = constants.CLUSTER_SOLVE_TIME_LIMIT_S
    search_params.log_search = False
    search_params.sat_parameters.num_workers = 1

    initial_routes = _build_initial_routes(
        context.routing_vehicles,
        context.cluster_orders,
        greedy_assignment,
        vehicle_index,
    )
    solution = _solve_with_warm_start(routing, initial_routes, search_params)
    if solution is None:
        return mark_all_infeasible(
            cluster_dict,
            ReasonCode.OPTIMIZATION_TRADEOFF,
            "OR-Tools found no feasible solution within time limit",
        )
    solution = _maybe_improve_with_lns(routing, solution, cluster_dict)

    dispatch_packages, served_task_ids = _extract_dispatch_packages(
        solution,
        routing,
        manager,
        context.routing_vehicles,
        context.cluster_orders,
        context.field_map,
        node_lats,
        node_lons,
        time_dim,
        context.cluster_id,
        context.depot_id,
        cluster_dict,
        now_epoch,
    )
    return dispatch_packages, unserved_orders(
        context.task_ids,
        context.cluster_id,
        served_task_ids,
    )


def _add_arc_costs(routing: Any, manager: Any, time_matrix: list[list[int]]) -> None:
    def time_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return time_matrix[from_node][to_node]

    cost_cb_idx = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_cb_idx)


def _vehicle_service_times(context: ClusterContext) -> list[list[int]]:
    """Per-vehicle node service durations (node 0 is the depot)."""
    return [
        [0]
        + [
            _estimate_operation_seconds(order, routing_vehicle["related"])
            for order in context.cluster_orders
        ]
        for routing_vehicle in context.routing_vehicles
    ]


def _add_time_dimension(
    routing: Any,
    manager: Any,
    time_matrix: list[list[int]],
    service_times: list[list[int]],
) -> Any:
    vehicle_transit_cb_indices: list[int] = []
    for service_s_by_node in service_times:

        def vehicle_time_callback(
            from_index: int,
            to_index: int,
            service_s_by_node: list[int] = service_s_by_node,
        ) -> int:
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            return time_matrix[from_node][to_node] + service_s_by_node[from_node]

        vehicle_transit_cb_indices.append(
            routing.RegisterTransitCallback(vehicle_time_callback)
        )

    routing.AddDimensionWithVehicleTransits(
        vehicle_transit_cb_indices,
        _ROUTING_HORIZON_S,
        _ROUTING_HORIZON_S,
        False,
        "Time",
    )
    return routing.GetDimensionOrDie("Time")


def _add_order_windows_and_disjunctions(
    routing: Any,
    manager: Any,
    time_dim: Any,
    context: ClusterContext,
    now_epoch: int,
) -> None:
    for node_idx, order in enumerate(context.cluster_orders, start=1):
        deadline_from_now = _deadline_from_now_s(order.deadline or "", now_epoch)
        node = manager.NodeToIndex(node_idx)
        cumul = time_dim.CumulVar(node)
        cumul.SetRange(0, deadline_from_now)
        _restrict_to_workable_windows(
            routing, cumul, node, order, now_epoch, deadline_from_now
        )
        routing.AddDisjunction([node], order_drop_penalty_s(order))


def _restrict_to_workable_windows(
    routing: Any,
    cumul: Any,
    node: Any,
    order: Any,
    now_epoch: int,
    deadline_from_now: int,
) -> None:
    """Constrain task start into the union of its workable windows.

    Window semantics: execution must *start* inside a declared window. With no
    declared windows the full [now, deadline] range stays open. When no window
    survives clamping to [now, deadline], the node is forced inactive (the
    chain-level pre-filter normally catches this case first).
    """
    from fl_op.solver.task_relations import parse_time_windows

    windows = parse_time_windows(order.time_windows)
    if not windows:
        return
    offsets: list[tuple[int, int]] = []
    for start, end in windows:
        start_off = max(0, int(start.timestamp()) - now_epoch)
        end_off = (
            deadline_from_now
            if end is None
            else min(deadline_from_now, int(end.timestamp()) - now_epoch)
        )
        if end_off < start_off or start_off > deadline_from_now:
            continue
        offsets.append((start_off, end_off))
    if not offsets:
        routing.solver().Add(routing.ActiveVar(node) == 0)
        return
    offsets.sort()
    cumul.SetRange(offsets[0][0], offsets[-1][1])
    for (_, prev_end), (next_start, _) in zip(offsets, offsets[1:]):
        if next_start > prev_end + 1:
            cumul.RemoveInterval(prev_end + 1, next_start - 1)


def _add_precedence_constraints(
    routing: Any,
    manager: Any,
    time_dim: Any,
    context: ClusterContext,
    service_times: list[list[int]],
) -> None:
    """Order chained tasks: a dependent starts after its predecessor finishes.

    Active-variable implication ensures a dependent is served only when its
    predecessor is; the big-M term disables the time ordering when the
    dependent is dropped. The predecessor's finish is bounded with the fastest
    vehicle's service time (the exact serving vehicle is a search decision).
    """
    node_of = {
        order.task_id: idx
        for idx, order in enumerate(context.cluster_orders, start=1)
    }
    solver = routing.solver()
    big_m = 2 * _ROUTING_HORIZON_S
    for node_idx, order in enumerate(context.cluster_orders, start=1):
        predecessor_id = str(order.depends_on_task_ref or "")
        if not predecessor_id:
            continue
        pred_node = node_of.get(predecessor_id)
        if pred_node is None or pred_node == node_idx:
            continue
        succ_index = manager.NodeToIndex(node_idx)
        pred_index = manager.NodeToIndex(pred_node)
        solver.Add(routing.ActiveVar(succ_index) <= routing.ActiveVar(pred_index))
        min_service = (
            min(per_vehicle[pred_node] for per_vehicle in service_times)
            if service_times
            else 0
        )
        solver.Add(
            time_dim.CumulVar(succ_index)
            + (1 - routing.ActiveVar(succ_index)) * big_m
            >= time_dim.CumulVar(pred_index) + min_service
        )


def _add_held_vehicle_breaks(
    routing: Any,
    time_dim: Any,
    context: ClusterContext,
    service_times: list[list[int]],
    held_windows: Optional[HeldWindows],
    now_epoch: int,
) -> None:
    """Block held vehicles during their frozen/carried assignment windows.

    Each busy interval becomes a fixed break on the vehicle's time dimension,
    so an incremental replan may reuse a held vehicle only in a real
    non-overlapping gap instead of excluding the vehicle outright.
    """
    if not held_windows:
        return
    solver = routing.solver()
    for rv_idx, routing_vehicle in enumerate(context.routing_vehicles):
        vehicle_id = routing_vehicle["prime"].asset_id
        intervals = []
        for seq, (start_epoch, end_epoch) in enumerate(held_windows.get(vehicle_id, [])):
            start_off = max(0, int(start_epoch) - now_epoch)
            end_off = min(int(end_epoch) - now_epoch, _ROUTING_HORIZON_S)
            if end_off <= 0 or start_off >= _ROUTING_HORIZON_S or end_off <= start_off:
                continue
            intervals.append(
                solver.FixedDurationIntervalVar(
                    start_off,
                    start_off,
                    end_off - start_off,
                    False,
                    f"held_{vehicle_id}_{seq}",
                )
            )
        if intervals:
            time_dim.SetBreakIntervalsOfVehicle(
                intervals, rv_idx, service_times[rv_idx]
            )
            logger.debug(
                "Vehicle %s: %d held windows added as break intervals",
                vehicle_id,
                len(intervals),
            )


def _deadline_from_now_s(deadline_str: str, now_epoch: int) -> int:
    try:
        deadline_epoch = int(datetime.fromisoformat(deadline_str).timestamp())
        return max(0, deadline_epoch - now_epoch)
    except (ValueError, TypeError):
        return _ROUTING_HORIZON_S


def _maybe_improve_with_lns(
    routing: Any,
    solution: Any,
    cluster_dict: dict[str, Any],
) -> Any:
    """Optionally continue search with LNS for a high-value cluster.

    Runs a second solve from the first solution with guided local search and
    LNS neighbourhood operators, bounded by its own time budget. The original
    solution is kept unless the improvement pass finds a strictly better one.
    """
    if not constants.CLUSTER_LNS_ENABLED:
        return solution
    total_penalty = float(cluster_dict.get("total_penalty_per_day", 0.0) or 0.0)
    if total_penalty < constants.CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY:
        return solution

    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    from ortools.util import optional_boolean_pb2

    lns_params = pywrapcp.DefaultRoutingSearchParameters()
    lns_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    lns_params.local_search_operators.use_path_lns = optional_boolean_pb2.BOOL_TRUE
    lns_params.local_search_operators.use_inactive_lns = optional_boolean_pb2.BOOL_TRUE
    lns_params.time_limit.seconds = constants.CLUSTER_LNS_TIME_LIMIT_S
    lns_params.log_search = False
    lns_params.sat_parameters.num_workers = 1

    improved = routing.SolveFromAssignmentWithParameters(solution, lns_params)
    if improved is not None and improved.ObjectiveValue() < solution.ObjectiveValue():
        logger.info(
            "Cluster %s: LNS improved objective %d -> %d",
            cluster_dict.get("cluster_id", "?"),
            solution.ObjectiveValue(),
            improved.ObjectiveValue(),
        )
        return improved
    return solution


def _solve_with_warm_start(routing: Any, initial_routes: list[list[int]], search_params: Any):
    try:
        routing.CloseModelWithParameters(search_params)
        initial_solution = routing.ReadAssignmentFromRoutes(initial_routes, True)
        return routing.SolveFromAssignmentWithParameters(initial_solution, search_params)
    except Exception:
        return routing.SolveWithParameters(search_params)

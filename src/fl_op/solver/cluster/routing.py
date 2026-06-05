"""OR-Tools routing model construction and solve for one prepared cluster."""

from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import CLUSTER_SOLVE_TIME_LIMIT_S
from fl_op.solver.cluster.context import ClusterContext
from fl_op.solver.cluster.infeasible import mark_all_infeasible, unserved_orders
from fl_op.solver.cluster.penalties import order_drop_penalty_s
from fl_op.solver.routing_model import (
    _build_initial_routes,
    _build_node_geometry,
    _extract_dispatch_packages,
)
from fl_op.solver.travel_time import _estimate_operation_seconds

_ROUTING_HORIZON_S = 30 * 24 * 3600


def solve_routing_context(
    context: ClusterContext,
    cluster_dict: dict[str, Any],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
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
    time_dim = _add_time_dimension(routing, manager, context, time_matrix)

    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    _add_order_windows_and_disjunctions(routing, manager, time_dim, context, now_epoch)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search_params.time_limit.seconds = CLUSTER_SOLVE_TIME_LIMIT_S
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
            "no_solution",
            "OR-Tools found no feasible solution within time limit",
        )

    dispatch_packages, served_order_ids = _extract_dispatch_packages(
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
        context.order_ids,
        context.cluster_id,
        served_order_ids,
    )


def _add_arc_costs(routing: Any, manager: Any, time_matrix: list[list[int]]) -> None:
    def time_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return time_matrix[from_node][to_node]

    cost_cb_idx = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_cb_idx)


def _add_time_dimension(
    routing: Any,
    manager: Any,
    context: ClusterContext,
    time_matrix: list[list[int]],
) -> Any:
    vehicle_transit_cb_indices: list[int] = []
    for routing_vehicle in context.routing_vehicles:
        service_s_by_node = [0] + [
            _estimate_operation_seconds(order, routing_vehicle["implement"])
            for order in context.cluster_orders
        ]

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
        deadline_from_now = _deadline_from_now_s(order.get("deadline", ""), now_epoch)
        node = manager.NodeToIndex(node_idx)
        time_dim.CumulVar(node).SetRange(0, deadline_from_now)
        routing.AddDisjunction([node], order_drop_penalty_s(order))


def _deadline_from_now_s(deadline_str: str, now_epoch: int) -> int:
    try:
        deadline_epoch = int(datetime.fromisoformat(deadline_str).timestamp())
        return max(0, deadline_epoch - now_epoch)
    except (ValueError, TypeError):
        return _ROUTING_HORIZON_S


def _solve_with_warm_start(routing: Any, initial_routes: list[list[int]], search_params: Any):
    try:
        routing.CloseModelWithParameters(search_params)
        initial_solution = routing.ReadAssignmentFromRoutes(initial_routes, True)
        return routing.SolveFromAssignmentWithParameters(initial_solution, search_params)
    except Exception:
        return routing.SolveWithParameters(search_params)

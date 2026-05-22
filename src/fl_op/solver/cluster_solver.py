"""OR-Tools routing library cluster solver.

Accepts and returns plain Python dicts only (no Pydantic, no OR-Tools objects
outside this function) so the function is safe to call across a
ProcessPoolExecutor(spawn) boundary.

The routing model is created and destroyed inside solve_cluster(); no shared
state persists between calls.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import CLUSTER_SOLVE_TIME_LIMIT_S
from fl_op.solver.routing_model import (
    _build_initial_routes,
    _build_node_geometry,
    _extract_dispatch_packages,
)
from fl_op.solver.travel_time import _estimate_operation_seconds

logger = logging.getLogger(__name__)

_ROUTING_HORIZON_S = 30 * 24 * 3600  # 30-day scheduling horizon


def _mark_all_infeasible(
    cluster_dict: dict[str, Any],
    reason: str,
    detail: str,
) -> tuple[list[dict], list[dict]]:
    infeasible = [
        {
            "order_id": oid,
            "cluster_id": cluster_dict.get("cluster_id", ""),
            "reason": reason,
            "detail": detail,
        }
        for oid in cluster_dict.get("order_ids", [])
    ]
    return [], infeasible


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
) -> tuple[list[dict], list[dict]]:
    """Solve one geographic cluster; return (dispatch_packages, infeasible_orders).

    Always returns a 2-tuple even when no solution is found. Never raises.
    num_search_workers=1 prevents CPU over-subscription inside ProcessPoolExecutor.
    """
    try:
        return _solve_cluster_inner(
            cluster_dict, orders, vehicles, implements, fields, depots,
            greedy_assignment, vehicle_index, implement_index,
        )
    except Exception as exc:
        logger.error(
            "Cluster %s solver exception: %s",
            cluster_dict.get("cluster_id", "?"),
            exc,
            exc_info=True,
        )
        return _mark_all_infeasible(cluster_dict, "solver_exception", f"unhandled exception: {exc}")


def _solve_cluster_inner(
    cluster_dict: dict[str, Any],
    all_orders: list[dict[str, Any]],
    all_vehicles: list[dict[str, Any]],
    all_implements: list[dict[str, Any]],
    all_fields: list[dict[str, Any]],
    all_depots: list[dict[str, Any]],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
) -> tuple[list[dict], list[dict]]:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    cluster_id = cluster_dict.get("cluster_id", "")
    order_ids = cluster_dict.get("order_ids", [])
    depot_id = cluster_dict.get("depot_id", "")
    allocated: dict[str, list[str]] = cluster_dict.get("allocated_vehicle_implements", {})

    if not order_ids:
        return [], []

    order_map = {o["order_id"]: o for o in all_orders}
    field_map = {f["field_id"]: f for f in all_fields}
    depot_map = {d["depot_id"]: d for d in all_depots}
    vehicle_map = {v["vehicle_id"]: v for v in all_vehicles}
    implement_map = {im["implement_id"]: im for im in all_implements}

    cluster_orders = [order_map[oid] for oid in order_ids if oid in order_map]
    if not cluster_orders:
        return _mark_all_infeasible(cluster_dict, "no_order_data", "orders not found in dataset")

    depot = depot_map.get(depot_id)
    if depot is None:
        return _mark_all_infeasible(cluster_dict, "no_depot_data", f"depot {depot_id} not found")

    routing_vehicles: list[dict[str, Any]] = []
    for vid, iids in allocated.items():
        if not iids:
            continue
        v = vehicle_map.get(vid)
        im = implement_map.get(iids[0])
        if v is not None and im is not None:
            routing_vehicles.append({"vehicle": v, "implement": im})

    if not routing_vehicles:
        return _mark_all_infeasible(
            cluster_dict, "no_allocated_vehicles", "resource_allocator found no feasible pairs"
        )

    depot_lat = float(depot["lat"])
    depot_lon = float(depot["lon"])
    node_lats, node_lons, time_matrix = _build_node_geometry(
        cluster_orders, field_map, depot_lat, depot_lon
    )

    n_nodes = len(node_lats)
    n_vehicles = len(routing_vehicles)

    manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    def time_callback(from_index: int, to_index: int) -> int:
        return time_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    cost_cb_idx = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_cb_idx)

    vehicle_transit_cb_indices: list[int] = []
    for rv in routing_vehicles:
        service_s_by_node = [0] + [
            _estimate_operation_seconds(order, rv["implement"]) for order in cluster_orders
        ]

        def vehicle_time_callback(
            from_index: int,
            to_index: int,
            service_s_by_node: list[int] = service_s_by_node,
        ) -> int:
            fi = manager.IndexToNode(from_index)
            ti = manager.IndexToNode(to_index)
            return time_matrix[fi][ti] + service_s_by_node[fi]

        vehicle_transit_cb_indices.append(routing.RegisterTransitCallback(vehicle_time_callback))

    routing.AddDimensionWithVehicleTransits(
        vehicle_transit_cb_indices,
        _ROUTING_HORIZON_S,
        _ROUTING_HORIZON_S,
        False,
        "Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")

    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    for node_idx, order in enumerate(cluster_orders, start=1):
        deadline_str = order.get("deadline", "")
        try:
            deadline_epoch = int(datetime.fromisoformat(deadline_str).timestamp())
            deadline_from_now = max(0, deadline_epoch - now_epoch)
        except (ValueError, TypeError):
            deadline_from_now = _ROUTING_HORIZON_S
        time_dim.CumulVar(manager.NodeToIndex(node_idx)).SetRange(0, deadline_from_now)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_params.time_limit.seconds = CLUSTER_SOLVE_TIME_LIMIT_S
    search_params.log_search = False
    search_params.sat_parameters.num_workers = 1

    initial_routes = _build_initial_routes(
        routing_vehicles, cluster_orders, greedy_assignment, vehicle_index
    )

    try:
        routing.CloseModelWithParameters(search_params)
        initial_solution = routing.ReadAssignmentFromRoutes(initial_routes, True)
        solution = routing.SolveFromAssignmentWithParameters(initial_solution, search_params)
    except Exception:
        solution = routing.SolveWithParameters(search_params)

    if solution is None:
        return _mark_all_infeasible(
            cluster_dict, "no_solution", "OR-Tools found no feasible solution within time limit"
        )

    dispatch_packages, served_order_ids = _extract_dispatch_packages(
        solution, routing, manager, routing_vehicles, cluster_orders,
        field_map, node_lats, node_lons, time_dim,
        cluster_id, depot_id, cluster_dict, now_epoch,
    )

    infeasible_orders = [
        {
            "order_id": oid,
            "cluster_id": cluster_id,
            "reason": "prize_collecting_unserved",
            "detail": "OR-Tools routing did not assign this order to any vehicle",
        }
        for oid in order_ids
        if oid not in served_order_ids
    ]

    logger.debug(
        "Cluster %s: %d dispatched, %d infeasible",
        cluster_id, len(dispatch_packages), len(infeasible_orders),
    )
    return dispatch_packages, infeasible_orders

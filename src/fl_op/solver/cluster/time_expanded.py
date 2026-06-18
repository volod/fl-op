"""Opt-in single-pass time-expanded routing for timed restriction windows.

The default cluster solver (``cluster/routing.py:solve_routing_context``) makes
restricted polygons time-dependent through an iterative post-solve refinement
loop: it solves with the always-active polygons only, inspects each solved arc's
real occupancy interval, reactivates the timed polygons it crossed, and re-solves
(bounded, with a conservative all-window final pass).

This module is the single-pass alternative. It partitions the horizon into the
stable-restriction segments (``horizon_restriction_segments``) and replicates each
task node once per segment, binding every copy's Time-dimension cumul to its
segment's bounds. OR-Tools then enforces "this task is served in that segment",
and each copy's outbound arcs are priced by exactly the polygons active in its
segment -- so one solve already accounts for time-dependent restrictions, with no
re-solve iterations and no conservative fallback.

It is gated behind ``ROUTE_TIME_EXPANDED_ENABLED`` (off by default) and only
handles the single-vehicle, no-load subset today (no reloads, pickups, held
windows, or task time windows); any richer cluster falls back to the refinement
path. Extending the replication to those features and promoting it to the default
is intentionally out of scope (the refinement path already routes every cluster
correctly); this model is a single-pass demonstration for the simple subset.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.canonical.enums import ReasonCode
from fl_op.core import constants
from fl_op.solver.cluster.context import ClusterContext
from fl_op.solver.cluster.conflict import no_solution_conflict
from fl_op.solver.cluster.infeasible import mark_all_infeasible, unserved_orders
from fl_op.solver.cluster.loads import load_kg
from fl_op.solver.cost_rates import ResourcePrices
from fl_op.solver.routing_geography import (
    RestrictionSegment,
    RouteRestriction,
    arc_route,
    horizon_restriction_segments,
    route_restrictions_for_vehicle,
)
from fl_op.solver.routing_model import (
    NODE_TASK,
    RoutingNode,
    _extract_dispatch_packages,
    build_node_table,
)
from fl_op.solver.solve_telemetry import (
    STATUS_NO_SOLUTION,
    STATUS_SOLVED,
    ClusterSolveTelemetry,
    routing_status_name,
)
from fl_op.solver.travel_time import (
    _estimate_operation_seconds,
    travel_mode_for_vehicle,
    vehicle_fallback_speed_kmh,
)

logger = logging.getLogger(__name__)

_HORIZON_S = constants.ROUTING_HORIZON_S

# (base_node_index, segment_index) behind one replicated routing node.
_CopyMeta = tuple[int, int]
RoutingResult = tuple[list[dict], list[dict], ClusterSolveTelemetry]


def maybe_solve_time_expanded(
    context: ClusterContext,
    cluster_dict: dict[str, Any],
    now_epoch: int,
    resource_prices: ResourcePrices,
    solve_time_limit_s: Optional[int],
    optimization_objective: str,
    held_windows: Optional[Any],
) -> Optional[RoutingResult]:
    """Solve a qualifying cluster with the time-expanded model, else return None.

    Returns the same ``(dispatch, infeasible, telemetry)`` tuple as
    ``solve_routing_context`` when the cluster is handled, or ``None`` to signal
    the caller to fall back to the refinement path.
    """
    if not constants.ROUTE_TIME_EXPANDED_ENABLED:
        return None
    if not _subset_supported(context, held_windows):
        return None

    routing_vehicle = context.routing_vehicles[0]
    restrictions = route_restrictions_for_vehicle(
        list(context.field_map.values()), routing_vehicle, now_epoch
    )
    segments = horizon_restriction_segments(restrictions, now_epoch, _HORIZON_S)
    # Only worth it when restrictions are actually time-dependent (more than the
    # single whole-horizon segment) and the replication stays small.
    if len(segments) < 2 or len(segments) > constants.ROUTE_TIME_EXPANDED_MAX_SEGMENTS:
        return None

    base_nodes = build_node_table(
        context.cluster_orders,
        context.field_map,
        context.depot_lat,
        context.depot_lon,
        context.depot_id,
    )
    related = routing_vehicle["related"]
    travel_mode = travel_mode_for_vehicle(routing_vehicle["prime"])
    fallback_speed = vehicle_fallback_speed_kmh(routing_vehicle["prime"])
    segment_matrices = _segment_time_matrices(
        context, base_nodes, segments, travel_mode, fallback_speed
    )
    service_base = [
        _estimate_operation_seconds(context.cluster_orders[node.order_idx], related)
        if node.kind == NODE_TASK
        else 0
        for node in base_nodes
    ]

    expansion = _expand_nodes(context, base_nodes, segments, now_epoch)
    if expansion is None:
        return None
    exp_nodes, exp_meta, cumul_bounds, task_copies = expansion

    return _solve_expanded(
        context,
        cluster_dict,
        now_epoch,
        resource_prices,
        solve_time_limit_s,
        optimization_objective,
        restrictions,
        segment_matrices,
        service_base,
        exp_nodes,
        exp_meta,
        cumul_bounds,
        task_copies,
    )


def _subset_supported(
    context: ClusterContext, held_windows: Optional[Any]
) -> bool:
    """Whether the cluster is within the time-expanded model's handled subset."""
    if len(context.routing_vehicles) != 1:
        return False
    orders = context.cluster_orders
    if not orders or len(orders) > constants.ROUTE_TIME_EXPANDED_MAX_ORDERS:
        return False
    if held_windows:
        return False
    if context.pickup_location_map:
        return False
    for order in orders:
        if load_kg(order.load_demand) > 0:
            return False
        if getattr(order, "pickup_location_ref", "") or getattr(
            order, "time_windows", ""
        ):
            return False
    return True


def _segment_time_matrices(
    context: ClusterContext,
    base_nodes: list[RoutingNode],
    segments: list[RestrictionSegment],
    travel_mode: str,
    fallback_speed: float,
) -> list[list[list[int]]]:
    """One restriction-aware base travel matrix per stable-restriction segment."""
    matrices: list[list[list[int]]] = []
    for segment in segments:
        polygons = [list(polygon) for polygon in segment.polygons]
        matrix = [[0] * len(base_nodes) for _ in base_nodes]
        for i, from_node in enumerate(base_nodes):
            for j, to_node in enumerate(base_nodes):
                if i == j:
                    continue
                matrix[i][j] = arc_route(
                    from_node.location_ref,
                    to_node.location_ref,
                    (from_node.lat, from_node.lon),
                    (to_node.lat, to_node.lon),
                    context.travel_lookup,
                    travel_mode,
                    fallback_speed,
                    polygons,
                ).seconds
        matrices.append(matrix)
    return matrices


def _expand_nodes(
    context: ClusterContext,
    base_nodes: list[RoutingNode],
    segments: list[RestrictionSegment],
    now_epoch: int,
) -> Optional[
    tuple[
        list[RoutingNode],
        list[_CopyMeta],
        list[Optional[tuple[int, int]]],
        dict[int, list[int]],
    ]
]:
    """Replicate each task node per segment; depot stays a single segment-0 node.

    Returns the expanded node list, per-copy ``(base_index, segment_index)``
    metadata, per-copy ``(cumul_lo, cumul_hi)`` time bounds (None for the depot),
    and the per-order list of replicated routing-node indices. Returns None when a
    task has no feasible segment copy (let the refinement path handle the edge).
    """
    exp_nodes: list[RoutingNode] = [base_nodes[0]]
    exp_meta: list[_CopyMeta] = [(0, 0)]
    cumul_bounds: list[Optional[tuple[int, int]]] = [None]
    task_copies: dict[int, list[int]] = {}

    for base_idx, node in enumerate(base_nodes):
        if node.kind != NODE_TASK:
            continue
        deadline_off = _deadline_offset(
            context.cluster_orders[node.order_idx], now_epoch
        )
        copies: list[int] = []
        for seg_idx, segment in enumerate(segments):
            lo = segment.start_offset_s
            hi = min(segment.end_offset_s - 1, deadline_off)
            if hi < lo:
                continue
            copies.append(len(exp_nodes))
            exp_nodes.append(node)
            exp_meta.append((base_idx, seg_idx))
            cumul_bounds.append((lo, hi))
        if not copies:
            return None
        task_copies[node.order_idx] = copies
    return exp_nodes, exp_meta, cumul_bounds, task_copies


def _solve_expanded(
    context: ClusterContext,
    cluster_dict: dict[str, Any],
    now_epoch: int,
    resource_prices: ResourcePrices,
    solve_time_limit_s: Optional[int],
    optimization_objective: str,
    restrictions: list[RouteRestriction],
    segment_matrices: list[list[list[int]]],
    service_base: list[int],
    exp_nodes: list[RoutingNode],
    exp_meta: list[_CopyMeta],
    cumul_bounds: list[Optional[tuple[int, int]]],
    task_copies: dict[int, list[int]],
) -> RoutingResult:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    expanded_matrix = _expanded_matrix(exp_meta, segment_matrices)
    manager = pywrapcp.RoutingIndexManager(len(exp_nodes), 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def travel_cb(from_index: int, to_index: int) -> int:
        return expanded_matrix[manager.IndexToNode(from_index)][
            manager.IndexToNode(to_index)
        ]

    def time_cb(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        return (
            expanded_matrix[from_node][manager.IndexToNode(to_index)]
            + service_base[exp_meta[from_node][0]]
        )

    cost_idx = routing.RegisterTransitCallback(travel_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_idx)
    time_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(time_idx, _HORIZON_S, _HORIZON_S, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    # The vehicle departs the depot at the planning origin; each task copy is
    # pinned to its segment's interval, so visiting it means serving the task in
    # that segment (slack lets the vehicle wait en route to reach the segment).
    time_dim.CumulVar(routing.Start(0)).SetRange(0, 0)
    for copy_idx, bounds in enumerate(cumul_bounds):
        if bounds is not None:
            lo, hi = bounds
            time_dim.CumulVar(manager.NodeToIndex(copy_idx)).SetRange(lo, hi)

    for copies in task_copies.values():
        routing.AddDisjunction(
            [manager.NodeToIndex(copy_idx) for copy_idx in copies],
            constants.ROUTE_TIME_EXPANDED_DROP_PENALTY,
        )

    time_limit_s = (
        solve_time_limit_s
        if solve_time_limit_s is not None
        else constants.CLUSTER_SOLVE_TIME_LIMIT_S
    )
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search_params.time_limit.seconds = time_limit_s
    search_params.log_search = False
    search_params.sat_parameters.num_workers = 1

    solve_started = time.perf_counter()
    solution = routing.SolveWithParameters(search_params)
    wall_s = time.perf_counter() - solve_started

    telemetry: ClusterSolveTelemetry = {
        "cluster_id": context.cluster_id,
        "status": STATUS_NO_SOLUTION,
        "n_tasks": len(context.cluster_orders),
        "n_routing_vehicles": 1,
        "time_limit_s": time_limit_s,
        "optimization_objective": optimization_objective,
        "solve_wall_s": round(wall_s, 3),
        "time_expanded": True,
    }
    if solution is None:
        telemetry.update(
            {
                "routing_status": routing_status_name(routing),
                "hit_time_limit": wall_s >= time_limit_s,
                "objective_value": None,
                "first_solution_objective": None,
                "n_dispatched": 0,
                "n_unserved": len(context.task_ids),
                "resource_conflict": no_solution_conflict(
                    hit_time_limit=wall_s >= time_limit_s,
                    n_unserved=len(context.task_ids),
                ),
            }
        )
        dispatch, infeasible = mark_all_infeasible(
            cluster_dict,
            ReasonCode.OPTIMIZATION_TRADEOFF,
            "time-expanded model found no feasible solution within time limit",
        )
        return dispatch, infeasible, telemetry

    dispatch_packages, served_task_ids = _extract_dispatch_packages(
        solution,
        routing,
        manager,
        context.routing_vehicles,
        context.cluster_orders,
        exp_nodes,
        time_dim,
        context.cluster_id,
        context.depot_id,
        cluster_dict,
        now_epoch,
        [expanded_matrix],
        resource_prices,
        None,
        list(context.field_map.values()),
        context.travel_lookup,
        [restrictions],
    )
    infeasible = unserved_orders(
        context.task_ids,
        context.cluster_id,
        served_task_ids,
        context.cluster_orders,
    )
    status_name = routing_status_name(routing)
    telemetry.update(
        {
            "status": STATUS_SOLVED,
            "routing_status": status_name,
            "hit_time_limit": "TIMEOUT" in status_name or wall_s >= time_limit_s,
            "objective_value": int(solution.ObjectiveValue()),
            "first_solution_objective": int(solution.ObjectiveValue()),
            "n_dispatched": len(dispatch_packages),
            "n_unserved": len(infeasible),
        }
    )
    if infeasible:
        telemetry["resource_conflict"] = no_solution_conflict(
            hit_time_limit=False, n_unserved=len(infeasible)
        )
    return dispatch_packages, infeasible, telemetry


def _expanded_matrix(
    exp_meta: list[_CopyMeta],
    segment_matrices: list[list[list[int]]],
) -> list[list[int]]:
    """Travel between copies, priced by the departure copy's segment polygons."""
    size = len(exp_meta)
    matrix = [[0] * size for _ in range(size)]
    for from_idx, (from_base, from_seg) in enumerate(exp_meta):
        seg_matrix = segment_matrices[from_seg]
        for to_idx, (to_base, _to_seg) in enumerate(exp_meta):
            matrix[from_idx][to_idx] = seg_matrix[from_base][to_base]
    return matrix


def _deadline_offset(order: Any, now_epoch: int) -> int:
    """Seconds from now to the order deadline, clamped to the horizon."""
    deadline = getattr(order, "deadline", "") or ""
    if not deadline:
        return _HORIZON_S
    try:
        epoch = int(datetime.fromisoformat(deadline).timestamp())
    except (ValueError, TypeError):
        return _HORIZON_S
    return max(0, min(_HORIZON_S, epoch - now_epoch))

"""OR-Tools routing model construction and solve for one prepared cluster."""

import logging
import time
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
from fl_op.solver.solve_telemetry import (
    STATUS_NO_SOLUTION,
    STATUS_SOLVED,
    ClusterSolveTelemetry,
    routing_status_name,
)
from fl_op.solver.travel_time import _estimate_operation_seconds

logger = logging.getLogger(__name__)

_ROUTING_HORIZON_S = constants.ROUTING_HORIZON_S

# Vehicle asset_id -> [(start_epoch_s, end_epoch_s), ...] busy intervals from
# held (frozen / carried-forward) assignments of a rolling plan.
HeldWindows = dict[str, list[tuple[int, int]]]


def solve_routing_context(
    context: ClusterContext,
    cluster_dict: dict[str, Any],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    held_windows: Optional[HeldWindows] = None,
    solve_time_limit_s: Optional[int] = None,
) -> tuple[list[dict], list[dict], ClusterSolveTelemetry]:
    """Build, solve, and extract one OR-Tools routing model.

    Returns (dispatch_packages, infeasible_orders, solve_telemetry); the
    telemetry record carries the machine-readable solve diagnostics.
    ``solve_time_limit_s`` overrides the engine default per-cluster budget
    (tunable via SolverParameters).
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    node_lats, node_lons, time_matrix = _build_node_geometry(
        context.cluster_orders,
        context.field_map,
        context.depot_lat,
        context.depot_lon,
        context.depot_id,
        context.travel_lookup,
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
    _add_load_capacity_dimension(routing, manager, context)
    _add_precedence_constraints(routing, manager, time_dim, context, service_times)
    _add_held_vehicle_breaks(
        routing, time_dim, context, service_times, held_windows, now_epoch
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

    initial_routes = _build_initial_routes(
        context.routing_vehicles,
        context.cluster_orders,
        greedy_assignment,
        vehicle_index,
    )
    solve_started = time.perf_counter()
    solution = _solve_with_warm_start(routing, initial_routes, search_params)
    telemetry: ClusterSolveTelemetry = {
        "cluster_id": context.cluster_id,
        "status": STATUS_NO_SOLUTION,
        "n_tasks": len(context.cluster_orders),
        "n_routing_vehicles": len(context.routing_vehicles),
        "time_limit_s": time_limit_s,
        "lns_attempted": False,
        "lns_improved": False,
        "lns_objective_delta": 0,
    }
    if solution is None:
        wall_s = time.perf_counter() - solve_started
        telemetry.update(
            {
                "solve_wall_s": round(wall_s, 3),
                "routing_status": routing_status_name(routing),
                "hit_time_limit": wall_s >= time_limit_s,
                "objective_value": None,
                "first_solution_objective": None,
                "n_dispatched": 0,
                "n_unserved": len(context.task_ids),
                "detail": "OR-Tools found no feasible solution within time limit",
            }
        )
        dispatch, infeasible = mark_all_infeasible(
            cluster_dict,
            ReasonCode.OPTIMIZATION_TRADEOFF,
            "OR-Tools found no feasible solution within time limit",
        )
        return dispatch, infeasible, telemetry

    first_objective = solution.ObjectiveValue()
    solution, lns_info = _maybe_improve_with_lns(routing, solution, cluster_dict)
    wall_s = time.perf_counter() - solve_started

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
    infeasible = unserved_orders(
        context.task_ids,
        context.cluster_id,
        served_task_ids,
    )
    status_name = routing_status_name(routing)
    telemetry.update(
        {
            "status": STATUS_SOLVED,
            "solve_wall_s": round(wall_s, 3),
            "routing_status": status_name,
            "hit_time_limit": "TIMEOUT" in status_name or wall_s >= time_limit_s,
            "objective_value": int(solution.ObjectiveValue()),
            "first_solution_objective": int(first_objective),
            "n_dispatched": len(dispatch_packages),
            "n_unserved": len(infeasible),
            **lns_info,
        }
    )
    return dispatch_packages, infeasible, telemetry


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
        _restrict_start_intervals(
            routing, cumul, node, order, context, now_epoch, deadline_from_now
        )
        routing.AddDisjunction([node], order_drop_penalty_s(order))


def _restrict_start_intervals(
    routing: Any,
    cumul: Any,
    node: Any,
    order: Any,
    context: ClusterContext,
    now_epoch: int,
    deadline_from_now: int,
) -> None:
    """Constrain task start into its admissible intervals.

    Admissible means: inside the union of the task's workable windows (the
    full [now, deadline] range when none are declared) and outside the
    location's restriction windows. Both constrain where execution *starts*.
    When nothing admissible survives, the node is forced inactive (the
    chain-level pre-filters normally catch this case first).
    """
    from fl_op.solver.restrictions import allowed_start_intervals
    from fl_op.solver.task_relations import parse_time_windows

    site = context.field_map.get(order.location_ref)
    has_windows = bool(parse_time_windows(order.time_windows))
    has_restrictions = bool(
        site is not None and parse_time_windows(site.restriction_windows)
    )
    if not has_windows and not has_restrictions:
        return

    epoch_intervals = allowed_start_intervals(
        order, site, now_epoch, now_epoch + deadline_from_now
    )
    offsets = [
        (start - now_epoch, end - now_epoch) for start, end in epoch_intervals
    ]
    if not offsets:
        routing.solver().Add(routing.ActiveVar(node) == 0)
        return
    cumul.SetRange(offsets[0][0], offsets[-1][1])
    for (_, prev_end), (next_start, _) in zip(offsets, offsets[1:]):
        if next_start > prev_end + 1:
            cumul.RemoveInterval(prev_end + 1, next_start - 1)


def _add_load_capacity_dimension(
    routing: Any,
    manager: Any,
    context: ClusterContext,
) -> None:
    """Bound each route's cumulative delivered mass by the vehicle's capacity.

    Task load demands accumulate along the route (single-trip delivery
    semantics, no depot reload); vehicles declaring no capacity are
    unconstrained. Skipped entirely when no task demands a load.
    """
    demands_g = [0] + [
        int(_load_kg(order.load_demand) * constants.SCALE_MASS_UNITS_PER_KG)
        for order in context.cluster_orders
    ]
    if not any(demands_g):
        return

    def demand_callback(from_index: int) -> int:
        return demands_g[manager.IndexToNode(from_index)]

    capacities_g = []
    for routing_vehicle in context.routing_vehicles:
        capacity_kg = _load_kg(routing_vehicle["prime"].load_capacity)
        if capacity_kg <= 0:
            capacity_kg = constants.VEHICLE_LOAD_UNLIMITED_KG
        capacities_g.append(int(capacity_kg * constants.SCALE_MASS_UNITS_PER_KG))

    demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb_idx,
        0,
        capacities_g,
        True,
        "Load",
    )


def _load_kg(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


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
) -> tuple[Any, dict[str, Any]]:
    """Optionally continue search with LNS for a high-value cluster.

    Runs a second solve from the first solution with guided local search and
    LNS neighbourhood operators, bounded by its own time budget. The original
    solution is kept unless the improvement pass finds a strictly better one.
    Returns (solution, lns_info) where lns_info carries the telemetry fields
    (attempted/improved flags and the objective delta, negative = better).
    """
    lns_info: dict[str, Any] = {
        "lns_attempted": False,
        "lns_improved": False,
        "lns_objective_delta": 0,
    }
    if not constants.CLUSTER_LNS_ENABLED:
        return solution, lns_info
    total_penalty = float(cluster_dict.get("total_penalty_per_day", 0.0) or 0.0)
    if total_penalty < constants.CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY:
        return solution, lns_info

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

    lns_info["lns_attempted"] = True
    improved = routing.SolveFromAssignmentWithParameters(solution, lns_params)
    if improved is not None and improved.ObjectiveValue() < solution.ObjectiveValue():
        logger.info(
            "Cluster %s: LNS improved objective %d -> %d",
            cluster_dict.get("cluster_id", "?"),
            solution.ObjectiveValue(),
            improved.ObjectiveValue(),
        )
        lns_info["lns_improved"] = True
        lns_info["lns_objective_delta"] = int(
            improved.ObjectiveValue() - solution.ObjectiveValue()
        )
        return improved, lns_info
    return solution, lns_info


def _solve_with_warm_start(routing: Any, initial_routes: list[list[int]], search_params: Any):
    try:
        routing.CloseModelWithParameters(search_params)
        initial_solution = routing.ReadAssignmentFromRoutes(initial_routes, True)
        return routing.SolveFromAssignmentWithParameters(initial_solution, search_params)
    except Exception:
        return routing.SolveWithParameters(search_params)

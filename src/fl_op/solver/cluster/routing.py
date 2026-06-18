"""OR-Tools routing model construction and solve for one prepared cluster."""

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.canonical.enums import ReasonCode
from fl_op.core import constants
from fl_op.solver.cluster.bundles import bundle_supports_operation
from fl_op.solver.cluster.conflict import build_resource_conflict, no_solution_conflict
from fl_op.solver.cluster.context import ClusterContext
from fl_op.solver.cluster.infeasible import mark_all_infeasible, unserved_orders
from fl_op.solver.cluster.loads import compartment_capacity_kg, load_kg
from fl_op.solver.cluster.penalties import (
    EUR_TO_DROP_PENALTY_SECONDS,
    order_drop_penalty_s,
)
from fl_op.solver.cluster.warm_start import build_capacity_aware_initial_routes
from fl_op.solver.cost_rates import (
    ResourcePrices,
    vehicle_energy_consumption_rate,
    vehicle_energy_resource_type,
    vehicle_machine_wear_eur_per_h,
)
from fl_op.solver.routing_model import (
    NODE_PICKUP,
    NODE_RELOAD,
    NODE_TASK,
    RoutingNode,
    _extract_dispatch_packages,
    build_node_table,
    build_vehicle_cost_matrices,
    build_vehicle_time_matrices,
    pickup_node_indices,
    task_node_indices,
)
from fl_op.solver.cluster.time_expanded import maybe_solve_time_expanded
from fl_op.solver.routing_geography import (
    ArcRoute,
    RouteRestriction,
    active_polygons,
    arc_route,
    route_restrictions_for_vehicle,
)
from fl_op.solver.solve_telemetry import (
    STATUS_NO_SOLUTION,
    STATUS_SOLVED,
    ClusterSolveTelemetry,
    routing_status_name,
)
from fl_op.solver.travel_time import (
    _estimate_operation_seconds,
    operation_set,
    travel_mode_for_vehicle,
    vehicle_fallback_speed_kmh,
)

logger = logging.getLogger(__name__)

_ROUTING_HORIZON_S = constants.ROUTING_HORIZON_S
ArcTimeOverrides = dict[tuple[int, int, int], int]

# Asset id -> [(start_epoch_s, end_epoch_s), ...] busy intervals from held
# (frozen / carried-forward) assignments of a rolling plan. Covers prime
# movers and related equipment (a routing vehicle is blocked by the union of
# its pair's intervals as vehicle breaks) plus operators (each held operator's
# windows block that operator's own tasks in-model; see
# _block_held_operator_windows). Hold-aware allocation scoring also reads them.
HeldWindows = dict[str, list[tuple[int, int]]]


def solve_routing_context(
    context: ClusterContext,
    cluster_dict: dict[str, Any],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    held_windows: Optional[HeldWindows] = None,
    solve_time_limit_s: Optional[int] = None,
    now_epoch: Optional[int] = None,
    resource_prices: Optional[ResourcePrices] = None,
    optimization_objective: str = constants.OBJECTIVE_MODE_COST,
    _route_time_overrides: Optional[ArcTimeOverrides] = None,
    _restriction_refinement: int = 0,
    _final_restriction_pass: bool = False,
) -> tuple[list[dict], list[dict], ClusterSolveTelemetry]:
    """Build, solve, and extract one OR-Tools routing model.

    Returns (dispatch_packages, infeasible_orders, solve_telemetry); the
    telemetry record carries the machine-readable solve diagnostics.
    ``solve_time_limit_s`` overrides the engine default per-cluster budget
    (tunable via SolverParameters). ``now_epoch`` is the planning time origin
    for deadlines and held-window offsets (the snapshot effective time when
    solving through an adapter); None falls back to wall-clock now.
    ``resource_prices`` are the resolved energy/material prices driving arc
    costs and dispatch margins; None falls back to the engine constants.
    ``optimization_objective`` is "cost" by default; "time" prices arcs by
    travel/service seconds and adds a task-start cumulative-time term.
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    if resource_prices is None:
        resource_prices = ResourcePrices()
    if now_epoch is None:
        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    # Opt-in single-pass time-expanded path (off by default); only the initial
    # solve, never the refinement re-entries. Returns None for any cluster outside
    # its supported subset, so the refinement path below stays the default.
    if _restriction_refinement == 0 and not _final_restriction_pass:
        expanded = maybe_solve_time_expanded(
            context,
            cluster_dict,
            now_epoch,
            resource_prices,
            solve_time_limit_s,
            optimization_objective,
            held_windows,
        )
        if expanded is not None:
            return expanded
    route_restrictions = [
        route_restrictions_for_vehicle(
            list(context.field_map.values()), routing_vehicle, now_epoch
        )
        for routing_vehicle in context.routing_vehicles
    ]
    has_load = any(load_kg(o.load_demand) > 0 for o in context.cluster_orders)
    n_reloads = (
        _reload_nodes_per_vehicle(context) * len(context.routing_vehicles)
        if constants.DEPOT_RELOAD_ENABLED and has_load
        else 0
    )
    nodes = build_node_table(
        context.cluster_orders,
        context.field_map,
        context.depot_lat,
        context.depot_lon,
        context.depot_id,
        n_reloads,
        pickup_map=context.pickup_location_map,
    )
    time_matrices = build_vehicle_time_matrices(
        nodes,
        context.routing_vehicles,
        context.travel_lookup,
        list(context.field_map.values()),
        route_restrictions,
    )
    for (vehicle_idx, from_node, to_node), seconds in (
        _route_time_overrides or {}
    ).items():
        time_matrices[vehicle_idx][from_node][to_node] = seconds
    # Per-operator wage band of the cluster's assigned operator (fleet labour
    # fallback) and per-vehicle network-aware distance/toll matrices, built only
    # when a toll is in play (a fleet per-km rate or any tolled link).
    operator_wages = cluster_dict.get("operator_wages", {})
    cluster_operator_wage = operator_wages.get(
        cluster_dict.get("operator_ref", ""), resource_prices.labor_eur_per_h
    )
    toll_active = resource_prices.toll_eur_per_km > 0 or getattr(
        context.travel_lookup, "has_tolls", False
    )
    if toll_active:
        distance_matrices, toll_matrices = build_vehicle_cost_matrices(
            nodes,
            context.routing_vehicles,
            context.travel_lookup,
            resource_prices.toll_eur_per_km,
        )
    else:
        distance_matrices = None
        toll_matrices = None
    service_times = _vehicle_service_times(context, nodes)
    manager = pywrapcp.RoutingIndexManager(
        len(nodes),
        len(context.routing_vehicles),
        0,
    )
    routing = pywrapcp.RoutingModel(manager)
    _add_arc_costs(
        routing,
        manager,
        time_matrices,
        service_times,
        context,
        resource_prices,
        optimization_objective,
        toll_matrices,
        nodes,
        cluster_operator_wage,
    )
    time_dim = _add_time_dimension(routing, manager, time_matrices, service_times)
    _add_operation_vehicle_constraints(routing, manager, context, nodes)

    _add_order_windows_and_disjunctions(
        routing, manager, time_dim, context, now_epoch, service_times, nodes
    )
    _make_reload_nodes_optional(routing, manager, nodes)
    _add_completion_time_costs(
        routing,
        manager,
        time_dim,
        context,
        nodes,
        optimization_objective,
        now_epoch,
    )
    _add_load_capacity_dimensions(routing, manager, context, nodes)
    _add_precedence_constraints(
        routing, manager, time_dim, context, service_times, nodes
    )
    _add_held_vehicle_breaks(
        routing, time_dim, context, service_times, held_windows, now_epoch
    )
    _add_operator_no_overlap(
        routing, manager, time_dim, cluster_dict, context, service_times, nodes,
        held_windows, now_epoch,
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

    initial_routes = build_capacity_aware_initial_routes(
        context, greedy_assignment, vehicle_index, nodes
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
        "lns_time_limit_s": int(cluster_dict.get("lns_time_limit_s", 0) or 0),
        "optimization_objective": optimization_objective,
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
        telemetry["resource_conflict"] = no_solution_conflict(
            hit_time_limit=bool(telemetry["hit_time_limit"]),
            n_unserved=len(context.task_ids),
        )
        dispatch, infeasible = mark_all_infeasible(
            cluster_dict,
            ReasonCode.OPTIMIZATION_TRADEOFF,
            "OR-Tools found no feasible solution within time limit",
        )
        return dispatch, infeasible, telemetry

    first_objective = solution.ObjectiveValue()
    if not _final_restriction_pass:
        activated = _activated_timed_arc_overrides(
            solution,
            routing,
            manager,
            time_dim,
            context,
            nodes,
            time_matrices,
            service_times,
            route_restrictions,
            now_epoch,
        )
        current_overrides = dict(_route_time_overrides or {})
        new_activations = {
            arc: seconds
            for arc, seconds in activated.items()
            if seconds > current_overrides.get(arc, 0)
        }
        if new_activations:
            current_overrides.update(new_activations)
            final_pass = (
                _restriction_refinement
                >= constants.ROUTE_RESTRICTION_MAX_REFINEMENTS
            )
            if final_pass:
                current_overrides.update(
                    _all_timed_arc_overrides(
                        context,
                        nodes,
                        time_matrices,
                        route_restrictions,
                    )
                )
            return solve_routing_context(
                context,
                cluster_dict,
                greedy_assignment,
                vehicle_index,
                held_windows=held_windows,
                solve_time_limit_s=solve_time_limit_s,
                now_epoch=now_epoch,
                resource_prices=resource_prices,
                optimization_objective=optimization_objective,
                _route_time_overrides=current_overrides,
                _restriction_refinement=_restriction_refinement + 1,
                _final_restriction_pass=final_pass,
            )
    solution, lns_info = _maybe_improve_with_lns(routing, solution, cluster_dict)
    wall_s = time.perf_counter() - solve_started

    dispatch_packages, served_task_ids = _extract_dispatch_packages(
        solution,
        routing,
        manager,
        context.routing_vehicles,
        context.cluster_orders,
        nodes,
        time_dim,
        context.cluster_id,
        context.depot_id,
        cluster_dict,
        now_epoch,
        time_matrices,
        resource_prices,
        distance_matrices,
        toll_matrices,
        list(context.field_map.values()),
        context.travel_lookup,
        route_restrictions,
        resource_prices.service_fee_eur_per_visit,
        operator_wages,
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
    telemetry["resource_conflict"] = _extract_resource_conflict(
        solution, routing, manager, time_dim, context, len(infeasible)
    )
    return dispatch_packages, infeasible, telemetry


def _add_arc_costs(
    routing: Any,
    manager: Any,
    time_matrices: list[list[list[int]]],
    service_times: list[list[int]],
    context: ClusterContext,
    resource_prices: ResourcePrices,
    optimization_objective: str,
    toll_matrices: Optional[list[list[list[float]]]] = None,
    nodes: Optional[list[RoutingNode]] = None,
    operator_wage: Optional[float] = None,
) -> None:
    """Price arcs by the selected objective.

    In cost mode, the arc cost sums every priced driver of one leg, converted
    into the objective currency drop penalties use (one EUR of business value =
    EUR_TO_DROP_PENALTY_SECONDS units):

    - energy: travel hours x the prime mover's consumption rate x the resolved
      resource price;
    - operating time: travel plus on-task service hours x the per-vehicle
      operating rate (this prime mover's machine wear plus the cluster operator's
      wage, each falling back to the fleet rate), so a faster or cheaper-to-run
      bundle saves wages and wear, not just energy;
    - tolls: the per-link toll where a travel link exists, else the fleet per-km
      rate on the geodesic leg (``toll_matrices`` already in EUR);
    - service fee: a fixed per-visit fee charged on every arc into a task node,
      shifting the serve-vs-drop trade-off independent of service duration.

    The operating, toll, and service-fee rates default to zero, so with no
    cost-rate data the arc cost collapses to the energy-only term. Per-vehicle
    evaluators let efficient machines win long repositioning legs over expensive
    ones on time-equal routes.

    In time mode, arc cost uses travel seconds plus service seconds at the
    departing node, matching the Time dimension scale.
    """
    service_fee_penalty = (
        resource_prices.service_fee_eur_per_visit * EUR_TO_DROP_PENALTY_SECONDS
    )
    task_nodes = (
        {i for i, node in enumerate(nodes) if node.kind == NODE_TASK}
        if nodes is not None
        else set()
    )
    for rv_idx, routing_vehicle in enumerate(context.routing_vehicles):
        if _is_time_objective(optimization_objective):

            def time_cost_callback(
                from_index: int,
                to_index: int,
                rv_idx: int = rv_idx,
            ) -> int:
                from_node = manager.IndexToNode(from_index)
                to_node = manager.IndexToNode(to_index)
                return int(
                    time_matrices[rv_idx][from_node][to_node]
                    + service_times[rv_idx][from_node]
                )

            cost_cb_idx = routing.RegisterTransitCallback(time_cost_callback)
            routing.SetArcCostEvaluatorOfVehicle(cost_cb_idx, rv_idx)
            continue

        prime = routing_vehicle["prime"]
        burn_l_per_h = _nonnegative_rate(vehicle_energy_consumption_rate(prime))
        energy_cost_per_travel_second = (
            burn_l_per_h
            * resource_prices.price_for(vehicle_energy_resource_type(prime))
            * EUR_TO_DROP_PENALTY_SECONDS
            / 3600.0
        )
        wage = (
            operator_wage if operator_wage is not None
            else resource_prices.labor_eur_per_h
        )
        operating_cost_per_second = (
            (vehicle_machine_wear_eur_per_h(prime, resource_prices.machine_wear_eur_per_h) + wage)
            * EUR_TO_DROP_PENALTY_SECONDS
            / 3600.0
        )
        toll_matrix = toll_matrices[rv_idx] if toll_matrices is not None else None

        def cost_callback(
            from_index: int,
            to_index: int,
            rv_idx: int = rv_idx,
            energy_cost_per_travel_second: float = energy_cost_per_travel_second,
            operating_cost_per_second: float = operating_cost_per_second,
            toll_matrix: Optional[list[list[float]]] = toll_matrix,
        ) -> int:
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            travel_s = time_matrices[rv_idx][from_node][to_node]
            cost = travel_s * energy_cost_per_travel_second
            if operating_cost_per_second:
                cost += (
                    travel_s + service_times[rv_idx][from_node]
                ) * operating_cost_per_second
            if toll_matrix is not None:
                cost += toll_matrix[from_node][to_node] * EUR_TO_DROP_PENALTY_SECONDS
            if service_fee_penalty and to_node in task_nodes:
                cost += service_fee_penalty
            return int(round(cost))

        cost_cb_idx = routing.RegisterTransitCallback(cost_callback)
        routing.SetArcCostEvaluatorOfVehicle(cost_cb_idx, rv_idx)


def _is_time_objective(optimization_objective: str) -> bool:
    return str(optimization_objective or "").lower() == constants.OBJECTIVE_MODE_TIME


def _nonnegative_rate(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _vehicle_service_times(
    context: ClusterContext, nodes: list[RoutingNode]
) -> list[list[int]]:
    """Per-vehicle node service durations.

    Task nodes carry the quantity-driven estimate, reload nodes the depot
    handling time; the depot and pickup stops are instantaneous.
    """
    def node_service_s(node: RoutingNode, routing_vehicle: dict) -> int:
        if node.kind == NODE_TASK:
            return _estimate_operation_seconds(
                context.cluster_orders[node.order_idx], routing_vehicle["related"]
            )
        if node.kind == NODE_RELOAD:
            return constants.DEPOT_RELOAD_SERVICE_S
        return 0

    return [
        [node_service_s(node, routing_vehicle) for node in nodes]
        for routing_vehicle in context.routing_vehicles
    ]


def _add_operation_vehicle_constraints(
    routing: Any,
    manager: Any,
    context: ClusterContext,
    nodes: list[RoutingNode],
) -> None:
    """Restrict each task variant to bundles compatible with its operation."""
    for node_idx, node in enumerate(nodes):
        if node.kind not in (NODE_PICKUP, NODE_TASK) or node.order_idx < 0:
            continue
        order = context.cluster_orders[node.order_idx]
        operation = str(getattr(order, "operation_type", "") or "").upper()
        allowed = [
            rv_idx
            for rv_idx, routing_vehicle in enumerate(context.routing_vehicles)
            if bundle_supports_operation(routing_vehicle, operation)
        ]
        index = manager.NodeToIndex(node_idx)
        if allowed:
            routing.VehicleVar(index).SetValues([-1, *allowed])
        else:
            routing.solver().Add(routing.ActiveVar(index) == 0)


def _add_time_dimension(
    routing: Any,
    manager: Any,
    time_matrices: list[list[list[int]]],
    service_times: list[list[int]],
) -> Any:
    vehicle_transit_cb_indices: list[int] = []
    for vehicle_idx, service_s_by_node in enumerate(service_times):

        def vehicle_time_callback(
            from_index: int,
            to_index: int,
            service_s_by_node: list[int] = service_s_by_node,
            vehicle_idx: int = vehicle_idx,
        ) -> int:
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            return (
                time_matrices[vehicle_idx][from_node][to_node]
                + service_s_by_node[from_node]
            )

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


def _activated_timed_arc_overrides(
    solution: Any,
    routing: Any,
    manager: Any,
    time_dim: Any,
    context: ClusterContext,
    nodes: list[RoutingNode],
    time_matrices: list[list[list[int]]],
    service_times: list[list[int]],
    restrictions_by_vehicle: list[list[RouteRestriction]],
    now_epoch: int,
) -> ArcTimeOverrides:
    """Detour durations for solved arcs overlapping active timed polygons."""
    overrides: ArcTimeOverrides = {}
    for vehicle_idx, routing_vehicle in enumerate(context.routing_vehicles):
        index = routing.Start(vehicle_idx)
        travel_mode = travel_mode_for_vehicle(routing_vehicle["prime"])
        fallback_speed = vehicle_fallback_speed_kmh(routing_vehicle["prime"])
        while not routing.IsEnd(index):
            next_index = solution.Value(routing.NextVar(index))
            from_node = manager.IndexToNode(index)
            to_node = manager.IndexToNode(next_index)
            start_epoch = (
                now_epoch
                + solution.Value(time_dim.CumulVar(index))
                + service_times[vehicle_idx][from_node]
            )
            end_epoch = now_epoch + solution.Value(
                time_dim.CumulVar(next_index)
            )
            polygons = active_polygons(
                restrictions_by_vehicle[vehicle_idx], start_epoch, end_epoch
            )
            route = _route_for_nodes(
                context,
                nodes[from_node],
                nodes[to_node],
                travel_mode,
                fallback_speed,
                polygons,
            )
            base_seconds = time_matrices[vehicle_idx][from_node][to_node]
            if route.detoured and route.seconds > base_seconds:
                overrides[(vehicle_idx, from_node, to_node)] = route.seconds
            index = next_index
    return overrides


def _all_timed_arc_overrides(
    context: ClusterContext,
    nodes: list[RoutingNode],
    time_matrices: list[list[list[int]]],
    restrictions_by_vehicle: list[list[RouteRestriction]],
) -> ArcTimeOverrides:
    """Conservative all-window detours used only after refinement exhaustion."""
    overrides: ArcTimeOverrides = {}
    for vehicle_idx, routing_vehicle in enumerate(context.routing_vehicles):
        polygons = [
            restriction.polygon
            for restriction in restrictions_by_vehicle[vehicle_idx]
        ]
        travel_mode = travel_mode_for_vehicle(routing_vehicle["prime"])
        fallback_speed = vehicle_fallback_speed_kmh(routing_vehicle["prime"])
        for from_node, from_route_node in enumerate(nodes):
            for to_node, to_route_node in enumerate(nodes):
                if from_node == to_node:
                    continue
                route = _route_for_nodes(
                    context,
                    from_route_node,
                    to_route_node,
                    travel_mode,
                    fallback_speed,
                    polygons,
                )
                base_seconds = time_matrices[vehicle_idx][from_node][to_node]
                if route.detoured and route.seconds > base_seconds:
                    overrides[(vehicle_idx, from_node, to_node)] = route.seconds
    return overrides


def _route_for_nodes(
    context: ClusterContext,
    from_node: RoutingNode,
    to_node: RoutingNode,
    travel_mode: str,
    fallback_speed: float,
    polygons: list[list[tuple[float, float]]],
) -> ArcRoute:
    return arc_route(
        from_node.location_ref,
        to_node.location_ref,
        (from_node.lat, from_node.lon),
        (to_node.lat, to_node.lon),
        context.travel_lookup,
        travel_mode,
        fallback_speed,
        polygons,
    )


def _add_order_windows_and_disjunctions(
    routing: Any,
    manager: Any,
    time_dim: Any,
    context: ClusterContext,
    now_epoch: int,
    service_times: list[list[int]],
    nodes: list[RoutingNode],
) -> None:
    """Constrain task nodes and pair pickups with deliveries.

    A paired order's pickup node carries no penalty of its own: the pair is
    served or dropped together (active-variable equality), on one vehicle,
    pickup first. Reload nodes are handled separately
    (``_make_reload_nodes_optional``): every offered stop is optional, and the
    capacity-aware warm start inserts only those needed by its seeded routes.
    """
    solver = routing.solver()
    pickup_nodes = pickup_node_indices(nodes)
    grouped_task_indices: dict[str, list[int]] = {}
    grouped_penalties: dict[str, int] = {}
    for node_idx, node in enumerate(nodes):
        if node.kind != NODE_TASK:
            continue
        order = context.cluster_orders[node.order_idx]
        deadline_from_now = _deadline_from_now_s(order.deadline or "", now_epoch)
        index = manager.NodeToIndex(node_idx)
        cumul = time_dim.CumulVar(index)
        cumul.SetRange(0, deadline_from_now)
        blocked_offsets = _blocked_occupancy_offsets(
            order, context, now_epoch, deadline_from_now
        )
        _restrict_start_intervals(
            routing, cumul, index, order, now_epoch, deadline_from_now,
            blocked_offsets,
        )
        _add_occupancy_constraints(
            routing, cumul, index, node_idx, blocked_offsets, service_times
        )
        _enforce_finish_within_windows(
            routing, cumul, index, node_idx, order, now_epoch,
            deadline_from_now, service_times,
        )
        alt_group = str(getattr(order, "alternative_group_ref", "") or "")
        if alt_group:
            grouped_task_indices.setdefault(alt_group, []).append(index)
            grouped_penalties[alt_group] = max(
                grouped_penalties.get(alt_group, 0),
                order_drop_penalty_s(order),
            )
        else:
            routing.AddDisjunction([index], order_drop_penalty_s(order))

        pickup_node_idx = pickup_nodes.get(node.order_idx)
        if pickup_node_idx is None:
            continue
        pickup_index = manager.NodeToIndex(pickup_node_idx)
        routing.AddDisjunction([pickup_index], 0)
        routing.AddPickupAndDelivery(pickup_index, index)
        solver.Add(routing.VehicleVar(pickup_index) == routing.VehicleVar(index))
        solver.Add(time_dim.CumulVar(pickup_index) <= cumul)
        solver.Add(routing.ActiveVar(pickup_index) == routing.ActiveVar(index))

    for alt_group, indices in grouped_task_indices.items():
        penalty = grouped_penalties.get(alt_group, 0)
        try:
            routing.AddDisjunction(indices, penalty, 1)
        except TypeError:
            # Older OR-Tools Python bindings use max_cardinality=1 by default.
            routing.AddDisjunction(indices, penalty)


def _coerce_priority_class(order: Any) -> Optional[int]:
    """Parse a TaskRow priority_class string into an int, or None when unset.

    Priority class is a free-form string field on the canonical task; only
    numeric values participate in urgency calibration. Non-numeric or blank
    values fall back to None so the task earns no class-based boost.
    """
    raw = getattr(order, "priority_class", "")
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _completion_weight_for_order(order: Any, now_epoch: int, base_weight: int) -> int:
    """Scale the completion-time soft weight by a per-task urgency factor.

    Returns ``base_weight`` unchanged unless TIME_OBJECTIVE_URGENCY_CALIBRATION
    is enabled. When enabled, higher-priority customer classes (numerically
    below the baseline) and tighter deadline slack add integer urgency steps so
    those task starts are pulled earlier. The base weight is never reduced, so
    calibration only ever strengthens the ordering, never weakens it.
    """
    if not constants.TIME_OBJECTIVE_URGENCY_CALIBRATION:
        return base_weight
    urgency_steps = 0
    class_step = int(constants.TIME_OBJECTIVE_CLASS_WEIGHT_STEP)
    if class_step > 0:
        priority_class = _coerce_priority_class(order)
        if priority_class is not None:
            deficit = (
                int(constants.TIME_OBJECTIVE_BASELINE_PRIORITY_CLASS) - priority_class
            )
            if deficit > 0:
                urgency_steps += deficit * class_step
    slack_bonus = int(constants.TIME_OBJECTIVE_SLACK_WEIGHT_BONUS)
    reference = int(constants.TIME_OBJECTIVE_SLACK_REFERENCE_S)
    if slack_bonus > 0 and reference > 0:
        slack = _deadline_from_now_s(getattr(order, "deadline", "") or "", now_epoch)
        if slack < reference:
            # Linear ramp: zero slack -> full bonus, reference slack -> none.
            urgency_steps += ((reference - slack) * slack_bonus) // reference
    return base_weight * (1 + urgency_steps)


def _add_completion_time_costs(
    routing: Any,
    manager: Any,
    time_dim: Any,
    context: ClusterContext,
    nodes: list[RoutingNode],
    optimization_objective: str,
    now_epoch: int,
) -> None:
    """In time mode, favor earlier task starts across the route set.

    The Time dimension transit already includes service duration at the
    departing node, so arc costs minimize total route time. Soft cumulative
    costs at task nodes add a completion-time proxy that prefers serving work
    earlier when two solutions have similar travel/service totals.

    When TIME_OBJECTIVE_URGENCY_CALIBRATION is enabled, the per-task soft weight
    is scaled by an urgency factor derived from customer class and deadline
    slack so more urgent work is pulled earlier; otherwise every task uses the
    flat TIME_OBJECTIVE_COMPLETION_WEIGHT.
    """
    if not _is_time_objective(optimization_objective):
        return
    base_weight = int(constants.TIME_OBJECTIVE_COMPLETION_WEIGHT)
    if base_weight <= 0:
        return
    for node_idx, node in enumerate(nodes):
        if node.kind != NODE_TASK:
            continue
        index = manager.NodeToIndex(node_idx)
        if hasattr(time_dim, "SetCumulVarSoftUpperBound"):
            order = context.cluster_orders[node.order_idx]
            weight = _completion_weight_for_order(order, now_epoch, base_weight)
            time_dim.SetCumulVarSoftUpperBound(index, 0, weight)
        else:
            routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(index))


def _blocked_occupancy_offsets(
    order: Any,
    context: ClusterContext,
    now_epoch: int,
    deadline_from_now: int,
) -> list[tuple[int, int]]:
    """Merged horizon-offset intervals the task execution must stay out of.

    One shared blocked-interval set for both sources: the site's restriction
    windows (curfew, protection period) and the task's non-compliant weather
    windows. Clamped to [0, deadline] in horizon offsets.
    """
    from fl_op.solver.restrictions import _epoch_intervals, merge_intervals

    clamp_end = now_epoch + deadline_from_now
    site = context.field_map.get(order.location_ref)
    blocked = (
        _epoch_intervals(site.restriction_windows, now_epoch, clamp_end)
        if site is not None
        else []
    )
    for start, end in context.weather_blocked.get(order.task_id, []):
        start_c = max(int(start), now_epoch)
        end_c = min(int(end), clamp_end)
        if end_c >= start_c:
            blocked.append((start_c, end_c))
    return [
        (start - now_epoch, end - now_epoch)
        for start, end in merge_intervals(blocked)
    ]


def _restrict_start_intervals(
    routing: Any,
    cumul: Any,
    node: Any,
    order: Any,
    now_epoch: int,
    deadline_from_now: int,
    blocked_offsets: list[tuple[int, int]],
) -> None:
    """Constrain task start into its admissible intervals.

    Admissible means: inside the union of the task's workable windows (the
    full [now, deadline] range when none are declared) and outside every
    blocked interval (location restriction windows plus non-compliant weather
    windows). When nothing admissible survives, the node is forced inactive
    (the chain-level pre-filters normally catch the restriction case first).
    """
    from fl_op.solver.restrictions import _epoch_intervals, subtract_intervals
    from fl_op.solver.task_relations import parse_time_windows

    has_windows = bool(parse_time_windows(order.time_windows))
    if not has_windows and not blocked_offsets:
        return

    if has_windows:
        base = [
            (start - now_epoch, end - now_epoch)
            for start, end in _epoch_intervals(
                order.time_windows, now_epoch, now_epoch + deadline_from_now
            )
        ]
    else:
        base = [(0, deadline_from_now)]
    offsets = subtract_intervals(base, blocked_offsets)
    if not offsets:
        routing.solver().Add(routing.ActiveVar(node) == 0)
        return
    cumul.SetRange(offsets[0][0], offsets[-1][1])
    for (_, prev_end), (next_start, _) in zip(offsets, offsets[1:]):
        if next_start > prev_end + 1:
            cumul.RemoveInterval(prev_end + 1, next_start - 1)


def _add_occupancy_constraints(
    routing: Any,
    cumul: Any,
    node: Any,
    node_idx: int,
    blocked_offsets: list[tuple[int, int]],
    service_times: list[list[int]],
) -> None:
    """Keep the whole execution interval out of every blocked interval.

    Start-domain pruning already forbids *starting* inside a block; these
    reified constraints additionally forbid running into a block started
    before it: the execution must finish by the block start or begin after
    the block ends. The service duration is the serving vehicle's, resolved
    by element lookup on the vehicle variable; for a dropped node (vehicle
    -1) the constraint is disabled through the active-variable term.
    """
    if not blocked_offsets or not service_times:
        return
    solver = routing.solver()
    active = routing.ActiveVar(node)
    service_expr = _vehicle_service_expr(routing, node, node_idx, service_times)
    for block_start, block_end in blocked_offsets:
        if block_start <= 0:
            # Block already runs at the horizon origin: nothing can finish
            # before it, so the start domain alone decides.
            continue
        finishes_before = (cumul + service_expr <= block_start).Var()
        starts_after = (cumul >= block_end + 1).Var()
        solver.Add(finishes_before + starts_after + (1 - active) >= 1)


def _vehicle_service_expr(
    routing: Any,
    node: Any,
    node_idx: int,
    service_times: list[list[int]],
) -> Any:
    """Service duration of the node, resolved on the serving vehicle.

    Service cost is per (vehicle, node), so the live duration depends on which
    vehicle wins the node. An element lookup over the vehicle variable returns
    that vehicle's service time; a dropped node has vehicle -1, clamped to 0 so
    the element stays in range (callers gate the constraint on the active var).
    """
    solver = routing.solver()
    vehicle_var = routing.VehicleVar(node)
    service_by_vehicle = [per_vehicle[node_idx] for per_vehicle in service_times]
    return solver.Element(service_by_vehicle, solver.Max(vehicle_var, 0).Var())


def _enforce_finish_within_windows(
    routing: Any,
    cumul: Any,
    node: Any,
    node_idx: int,
    order: Any,
    now_epoch: int,
    deadline_from_now: int,
    service_times: list[list[int]],
) -> None:
    """Require both start and finish to land inside one workable window.

    Start-domain pruning keeps the *start* in the union of workable windows;
    this adds the missing half so the whole execution interval
    [start, start + service] fits within a single declared window and work
    cannot spill past a window end (a curfew or agronomic close). The service
    duration is the serving vehicle's, so the fit is vehicle-aware; a dropped
    node is exempted through the active-variable term. Orders without declared
    workable windows are left to the deadline bound on the start alone, so this
    is a no-op for them.
    """
    from fl_op.solver.restrictions import _epoch_intervals
    from fl_op.solver.task_relations import parse_time_windows

    if not service_times or not parse_time_windows(order.time_windows):
        return
    window_offsets = [
        (start - now_epoch, end - now_epoch)
        for start, end in _epoch_intervals(
            order.time_windows, now_epoch, now_epoch + deadline_from_now
        )
    ]
    if not window_offsets:
        return
    solver = routing.solver()
    active = routing.ActiveVar(node)
    service_expr = _vehicle_service_expr(routing, node, node_idx, service_times)
    fit_terms = [
        (
            (cumul >= win_start).Var() + (cumul + service_expr <= win_end).Var()
            >= 2
        ).Var()
        for win_start, win_end in window_offsets
    ]
    fit_count = fit_terms[0]
    for term in fit_terms[1:]:
        fit_count = fit_count + term
    solver.Add(fit_count + (1 - active) >= 1)


def _add_operator_no_overlap(
    routing: Any,
    manager: Any,
    time_dim: Any,
    cluster_dict: dict[str, Any],
    context: ClusterContext,
    service_times: list[list[int]],
    nodes: list[RoutingNode],
    held_windows: Optional[HeldWindows] = None,
    now_epoch: int = 0,
) -> None:
    """Serialize tasks that share one operator and block held operator calendars.

    The vehicle visit order already serializes tasks on the *same* vehicle, but
    a cluster can run several (prime, related) pairs in parallel while a single
    certified operator backs more than one of them. This adds the missing
    operator time dimension: for every pair of active tasks resolved to the same
    operator, their execution intervals [start, start + service] may not overlap
    (one must finish before the other starts). The serving vehicle decides the
    service duration, so the no-overlap is vehicle-aware; a dropped task is
    exempted through its active variable. Tasks without a resolved operator (no
    operator_ref and no backup) are left unconstrained.

    A *held* operator (carried/frozen on another assignment of a rolling plan)
    also blocks its own tasks in this cluster: each of that operator's task
    intervals may not overlap the operator's busy windows, so a held operator is
    reused only in a genuine gap -- the same exact in-model time blocking prime
    movers and implements already get as vehicle breaks, rather than relying on
    hold-aware allocation scoring alone.
    """
    if not service_times:
        return
    task_operators: dict[str, str] = cluster_dict.get("task_operators", {}) or {}
    default_operator = str(cluster_dict.get("operator_ref", "") or "")
    # operator_id -> list of (start cumul var, end expr, active var)
    by_operator: dict[str, list[tuple[Any, Any, Any]]] = {}
    for node_idx, node in enumerate(nodes):
        if node.kind != NODE_TASK:
            continue
        order = context.cluster_orders[node.order_idx]
        operator_id = str(task_operators.get(order.task_id) or default_operator)
        if not operator_id:
            continue
        index = manager.NodeToIndex(node_idx)
        cumul = time_dim.CumulVar(index)
        service_expr = _vehicle_service_expr(routing, index, node_idx, service_times)
        end_expr = cumul + service_expr
        active = routing.ActiveVar(index)
        by_operator.setdefault(operator_id, []).append((cumul, end_expr, active))

    solver = routing.solver()
    for entries in by_operator.values():
        if len(entries) < 2:
            continue
        for i in range(len(entries)):
            start_a, end_a, active_a = entries[i]
            for j in range(i + 1, len(entries)):
                start_b, end_b, active_b = entries[j]
                a_before_b = (end_a <= start_b).Var()
                b_before_a = (end_b <= start_a).Var()
                solver.Add(
                    a_before_b + b_before_a + (1 - active_a) + (1 - active_b) >= 1
                )

    _block_held_operator_windows(solver, by_operator, held_windows, now_epoch)


def _block_held_operator_windows(
    solver: Any,
    by_operator: dict[str, list[tuple[Any, Any, Any]]],
    held_windows: Optional[HeldWindows],
    now_epoch: int,
) -> None:
    """Forbid each held operator's tasks from running during its busy windows.

    For every operator carrying held windows, each of its (active) task
    intervals must finish before a window starts or start after it ends. A
    single task still gets blocked (unlike the pairwise no-overlap, which needs
    two), so a held operator's calendar is honoured even when it backs one task.
    """
    if not held_windows:
        return
    for operator_id, entries in by_operator.items():
        for start_off, end_off in _held_window_offsets(
            held_windows.get(operator_id, []), now_epoch
        ):
            for start_cumul, end_expr, active in entries:
                before = (end_expr <= start_off).Var()
                after = (start_cumul >= end_off).Var()
                solver.Add(before + after + (1 - active) >= 1)


def _reload_nodes_per_vehicle(context: ClusterContext) -> int:
    """Optional reload stops to offer each routing vehicle.

    Enough stops for one vehicle to clear the cluster's heaviest single-material
    demand in successive fills (refills = ceil(total_demand / smallest matching
    compartment) - 1), bounded by DEPOT_RELOAD_MAX_TRIPS_PER_VEHICLE and at
    least one. Because reload stops are optional, offering a few extra never
    forces unnecessary depot trips; it only widens the search for multi-trip
    routes when demand exceeds a single fill.
    """
    materials = {
        str(order.load_material or "")
        for order in context.cluster_orders
        if load_kg(order.load_demand) > 0
    }
    refills = 1
    for material in materials:
        total = sum(
            load_kg(order.load_demand)
            for order in context.cluster_orders
            if str(order.load_material or "") == material
        )
        capacities = [
            compartment_capacity_kg(rv["prime"], material)
            for rv in context.routing_vehicles
        ]
        min_capacity = min((c for c in capacities if c > 0), default=0.0)
        if min_capacity > 0:
            refills = max(refills, math.ceil(total / min_capacity) - 1)
    return max(1, min(refills, constants.DEPOT_RELOAD_MAX_TRIPS_PER_VEHICLE))


def _make_reload_nodes_optional(
    routing: Any,
    manager: Any,
    nodes: list[RoutingNode],
) -> None:
    """Make every offered reload visit optional with zero drop penalty.

    The capacity-aware warm start inserts the reload/task pairs needed by its
    seeded routes, so first-solution search no longer depends on a mandatory
    reload anchor. Unused reloads therefore disappear entirely, avoiding both
    their handling time and an unnecessary depot visit.
    """
    for node_idx, node in enumerate(nodes):
        if node.kind == NODE_RELOAD:
            routing.AddDisjunction([manager.NodeToIndex(node_idx)], 0)


def _cluster_load_materials(context: ClusterContext) -> set[str]:
    """The load materials any cluster task demands (one capacity dimension each)."""
    return {
        str(order.load_material or "")
        for order in context.cluster_orders
        if load_kg(order.load_demand) > 0
    }


def _load_dimension_name(material: str) -> str:
    return f"Load_{material}" if material else "Load"


def _extract_resource_conflict(
    solution: Any,
    routing: Any,
    manager: Any,
    time_dim: Any,
    context: ClusterContext,
    n_unserved: int,
) -> dict[str, Any]:
    """Measure primal resource utilization off the solved routes (never raises).

    Walks each used vehicle's route once, reading the Time dimension's route-end
    cumulative and each Load dimension's peak cumulative, and normalizes them
    into time / per-material capacity / fleet utilization for
    ``build_resource_conflict``. Diagnostics must never fail a solve, so any
    error yields an empty record.
    """
    try:
        scale = constants.SCALE_MASS_UNITS_PER_KG
        load_dims: dict[str, Any] = {}
        for material in _cluster_load_materials(context):
            try:
                load_dims[material] = routing.GetDimensionOrDie(
                    _load_dimension_name(material)
                )
            except Exception:  # noqa: BLE001 - a missing dimension just drops out
                continue
        n_used = 0
        max_end_s = 0
        capacity_utilization: dict[str, float] = {}
        for rv_idx, rv in enumerate(context.routing_vehicles):
            if not routing.IsVehicleUsed(solution, rv_idx):
                continue
            n_used += 1
            index = routing.Start(rv_idx)
            peak_load = {material: 0 for material in load_dims}
            # Read the load cumulatives at every node, the end node included:
            # for a depot-carried delivery the on-board mass lands on the leg
            # into the end node, so stopping before it would read a flat zero.
            while True:
                for material, dim in load_dims.items():
                    peak_load[material] = max(
                        peak_load[material], solution.Value(dim.CumulVar(index))
                    )
                if routing.IsEnd(index):
                    break
                index = solution.Value(routing.NextVar(index))
            max_end_s = max(max_end_s, solution.Value(time_dim.CumulVar(index)))
            for material, dim in load_dims.items():
                capacity_units = compartment_capacity_kg(rv["prime"], material) * scale
                if capacity_units > 0:
                    capacity_utilization[material] = max(
                        capacity_utilization.get(material, 0.0),
                        peak_load[material] / capacity_units,
                    )
        time_util = max_end_s / _ROUTING_HORIZON_S if _ROUTING_HORIZON_S else 0.0
        return build_resource_conflict(
            n_unserved=n_unserved,
            n_vehicles=len(context.routing_vehicles),
            n_vehicles_used=n_used,
            time_utilization=time_util,
            capacity_utilization=capacity_utilization,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must never fail a solve
        logger.debug("Resource-conflict extraction failed: %s", exc)
        return {}


def _add_load_capacity_dimensions(
    routing: Any,
    manager: Any,
    context: ClusterContext,
    nodes: list[RoutingNode],
) -> None:
    """One capacity dimension per load material, with optional depot reloads.

    Task loads accumulate along the route per material code; the vehicle's
    compartment capacity for that material (load_capacities) bounds the
    cumulative mass, falling back to the aggregate load_capacity. A paired
    pickup-and-delivery order adds its load at the pickup node and releases
    it at the task node; a plain order accumulates depot-carried mass at its
    task node. Reload nodes reset every load dimension: their large negative
    transit plus free slack lets the solver re-fill at the depot (slack is
    fixed to zero everywhere else - the cvrp-reload construction), so demand
    beyond one vehicle fill becomes extra trips instead of dropped tasks.
    """
    materials = _cluster_load_materials(context)
    if not materials:
        return
    scale = constants.SCALE_MASS_UNITS_PER_KG
    reload_node_idxs = [i for i, n in enumerate(nodes) if n.kind == NODE_RELOAD]

    for material in sorted(materials):
        demands = [0] * len(nodes)
        for i, node in enumerate(nodes):
            if node.order_idx < 0:
                continue
            order = context.cluster_orders[node.order_idx]
            if str(order.load_material or "") != material:
                continue
            quantity = int(load_kg(order.load_demand) * scale)
            if quantity <= 0:
                continue
            paired = bool(str(order.pickup_location_ref or ""))
            if node.kind == NODE_PICKUP:
                demands[i] = quantity
            elif node.kind == NODE_TASK:
                demands[i] = -quantity if paired else quantity

        capacities = [
            int(compartment_capacity_kg(rv["prime"], material) * scale)
            for rv in context.routing_vehicles
        ]
        max_capacity = max(capacities)
        for i in reload_node_idxs:
            demands[i] = -max_capacity

        def demand_callback(from_index: int, demands: list[int] = demands) -> int:
            return demands[manager.IndexToNode(from_index)]

        demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        name = _load_dimension_name(material)
        routing.AddDimensionWithVehicleCapacity(
            demand_cb_idx,
            max_capacity if reload_node_idxs else 0,
            capacities,
            True,
            name,
        )
        if reload_node_idxs:
            dimension = routing.GetDimensionOrDie(name)
            for i, node in enumerate(nodes):
                if i == 0 or node.kind == NODE_RELOAD:
                    continue
                dimension.SlackVar(manager.NodeToIndex(i)).SetValue(0)
            for v_idx in range(len(context.routing_vehicles)):
                dimension.SlackVar(routing.Start(v_idx)).SetValue(0)


def _add_precedence_constraints(
    routing: Any,
    manager: Any,
    time_dim: Any,
    context: ClusterContext,
    service_times: list[list[int]],
    nodes: list[RoutingNode],
) -> None:
    """Order chained tasks: a dependent starts after its predecessor finishes.

    Active-variable implication ensures a dependent is served only when its
    predecessor is; the big-M term disables the time ordering when the
    dependent is dropped. The predecessor's finish is bounded with the fastest
    vehicle's service time (the exact serving vehicle is a search decision).
    """
    task_nodes = task_node_indices(nodes)
    node_of = {
        order.task_id: task_nodes[order_idx]
        for order_idx, order in enumerate(context.cluster_orders)
    }
    solver = routing.solver()
    big_m = 2 * _ROUTING_HORIZON_S
    for order_idx, order in enumerate(context.cluster_orders):
        predecessor_id = str(order.depends_on_task_ref or "")
        if not predecessor_id:
            continue
        node_idx = task_nodes[order_idx]
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


def _held_window_offsets(
    raw_windows: list[tuple[int, int]],
    now_epoch: int,
) -> list[tuple[int, int]]:
    """Clamp held busy intervals to horizon offsets and merge overlaps.

    Shared by held prime-mover/implement breaks and held-operator no-overlap:
    epoch-second windows become [start_off, end_off) offsets from the planning
    origin, clipped to [0, horizon], with empty/out-of-range windows dropped.
    """
    from fl_op.solver.restrictions import merge_intervals

    offsets = []
    for start_epoch, end_epoch in raw_windows:
        start_off = max(0, int(start_epoch) - now_epoch)
        end_off = min(int(end_epoch) - now_epoch, _ROUTING_HORIZON_S)
        if end_off <= 0 or start_off >= _ROUTING_HORIZON_S or end_off <= start_off:
            continue
        offsets.append((start_off, end_off))
    return merge_intervals(offsets)


def _add_held_vehicle_breaks(
    routing: Any,
    time_dim: Any,
    context: ClusterContext,
    service_times: list[list[int]],
    held_windows: Optional[HeldWindows],
    now_epoch: int,
) -> None:
    """Block held assets during their frozen/carried assignment windows.

    A routing vehicle is a (prime mover, related equipment) pair; the union
    of both assets' busy intervals becomes fixed breaks on the vehicle's time
    dimension, so an incremental replan may reuse a held prime mover *or* a
    held implement only in a real non-overlapping gap instead of excluding
    either outright.
    """
    if not held_windows:
        return

    solver = routing.solver()
    for rv_idx, routing_vehicle in enumerate(context.routing_vehicles):
        vehicle_id = routing_vehicle["prime"].asset_id
        related_id = routing_vehicle["related"].asset_id
        # Overlapping holds (the pair held by one assignment) merge into one
        # break each, so the dimension never sees duplicate breaks.
        merged = _held_window_offsets(
            [
                *held_windows.get(vehicle_id, []),
                *held_windows.get(related_id, []),
            ],
            now_epoch,
        )
        intervals = [
            solver.FixedDurationIntervalVar(
                start_off,
                start_off,
                end_off - start_off,
                False,
                f"held_{vehicle_id}_{seq}",
            )
            for seq, (start_off, end_off) in enumerate(merged)
        ]
        if intervals:
            time_dim.SetBreakIntervalsOfVehicle(
                intervals, rv_idx, service_times[rv_idx]
            )
            logger.debug(
                "Vehicle %s + %s: %d held windows added as break intervals",
                vehicle_id,
                related_id,
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
    lns_budget_s = int(cluster_dict.get("lns_time_limit_s", 0) or 0)
    if lns_budget_s <= 0:
        if not constants.CLUSTER_LNS_ENABLED:
            return solution, lns_info
        lns_budget_s = constants.CLUSTER_LNS_TIME_LIMIT_S
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
    lns_params.time_limit.seconds = lns_budget_s
    lns_params.log_search = False
    lns_params.sat_parameters.num_workers = 1

    lns_info["lns_attempted"] = True
    lns_info["lns_time_limit_s"] = lns_budget_s
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

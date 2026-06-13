"""OR-Tools routing model construction and solve for one prepared cluster."""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.canonical.enums import ReasonCode
from fl_op.core import constants
from fl_op.solver.cluster.context import ClusterContext
from fl_op.solver.cluster.infeasible import mark_all_infeasible, unserved_orders
from fl_op.solver.cluster.penalties import (
    EUR_TO_DROP_PENALTY_SECONDS,
    order_drop_penalty_s,
)
from fl_op.solver.cost_rates import (
    ResourcePrices,
    vehicle_energy_consumption_rate,
    vehicle_energy_resource_type,
)
from fl_op.solver.routing_model import (
    NODE_PICKUP,
    NODE_RELOAD,
    NODE_TASK,
    RoutingNode,
    _build_initial_routes,
    _extract_dispatch_packages,
    build_node_table,
    build_vehicle_time_matrices,
    pickup_node_indices,
    task_node_indices,
)
from fl_op.solver.solve_telemetry import (
    STATUS_NO_SOLUTION,
    STATUS_SOLVED,
    ClusterSolveTelemetry,
    routing_status_name,
)
from fl_op.solver.travel_time import _estimate_operation_seconds, operation_set

logger = logging.getLogger(__name__)

_ROUTING_HORIZON_S = constants.ROUTING_HORIZON_S

# Asset id -> [(start_epoch_s, end_epoch_s), ...] busy intervals from held
# (frozen / carried-forward) assignments of a rolling plan. Covers prime
# movers and related equipment (a routing vehicle is blocked by the union of
# its pair's intervals) plus operators (consumed by hold-aware allocation
# scoring, not by the routing model).
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
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    if resource_prices is None:
        resource_prices = ResourcePrices()
    has_load = any(_load_kg(o.load_demand) > 0 for o in context.cluster_orders)
    n_reloads = (
        len(context.routing_vehicles)
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
    )
    time_matrices = build_vehicle_time_matrices(
        nodes, context.routing_vehicles, context.travel_lookup
    )
    manager = pywrapcp.RoutingIndexManager(
        len(nodes),
        len(context.routing_vehicles),
        0,
    )
    routing = pywrapcp.RoutingModel(manager)
    _add_arc_costs(routing, manager, time_matrices, context, resource_prices)
    service_times = _vehicle_service_times(context, nodes)
    time_dim = _add_time_dimension(routing, manager, time_matrices, service_times)
    _add_operation_vehicle_constraints(routing, manager, context, nodes)

    if now_epoch is None:
        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    _add_order_windows_and_disjunctions(
        routing, manager, time_dim, context, now_epoch, service_times, nodes
    )
    _add_load_capacity_dimensions(routing, manager, context, nodes)
    _add_precedence_constraints(
        routing, manager, time_dim, context, service_times, nodes
    )
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
        nodes,
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
        nodes,
        time_dim,
        context.cluster_id,
        context.depot_id,
        cluster_dict,
        now_epoch,
        time_matrices,
        resource_prices,
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
    return dispatch_packages, infeasible, telemetry


def _add_arc_costs(
    routing: Any,
    manager: Any,
    time_matrices: list[list[list[int]]],
    context: ClusterContext,
    resource_prices: ResourcePrices,
) -> None:
    """Price arcs by the serving vehicle's travel energy cost.

    Arc cost = travel hours x the prime mover's consumption rate x the
    resolved resource price, converted into the objective currency drop
    penalties use (one EUR of business value = EUR_TO_DROP_PENALTY_SECONDS
    units). Per-vehicle evaluators let efficient machines win long
    repositioning legs over expensive ones on time-equal routes.
    """
    for rv_idx, routing_vehicle in enumerate(context.routing_vehicles):
        prime = routing_vehicle["prime"]
        burn_l_per_h = _nonnegative_rate(vehicle_energy_consumption_rate(prime))
        cost_per_travel_second = (
            burn_l_per_h
            * resource_prices.price_for(vehicle_energy_resource_type(prime))
            * EUR_TO_DROP_PENALTY_SECONDS
            / 3600.0
        )

        def fuel_cost_callback(
            from_index: int,
            to_index: int,
            cost_per_travel_second: float = cost_per_travel_second,
        ) -> int:
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            return int(round(
                time_matrices[rv_idx][from_node][to_node] * cost_per_travel_second
            ))

        cost_cb_idx = routing.RegisterTransitCallback(fuel_cost_callback)
        routing.SetArcCostEvaluatorOfVehicle(cost_cb_idx, rv_idx)


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
            if _bundle_supports_operation(routing_vehicle, operation)
        ]
        index = manager.NodeToIndex(node_idx)
        if allowed:
            routing.VehicleVar(index).SetValues([-1, *allowed])
        else:
            routing.solver().Add(routing.ActiveVar(index) == 0)


def _bundle_supports_operation(routing_vehicle: dict[str, Any], operation: str) -> bool:
    prime_ops = operation_set(
        getattr(routing_vehicle.get("prime"), "compatible_operations", [])
    )
    related_ops = operation_set(
        getattr(routing_vehicle.get("related"), "compatible_operations", [])
    )
    return (not prime_ops or operation in prime_ops) and (
        not related_ops or operation in related_ops
    )


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
    pickup first. Reload nodes get no disjunction: they are mandatory depot
    stops (negligible cost when unused, since they sit at the depot), so the
    first-solution construction always places them and serving a task behind
    a reload stays a single insertion move for the local search.
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
    vehicle_var = routing.VehicleVar(node)
    service_by_vehicle = [per_vehicle[node_idx] for per_vehicle in service_times]
    service_expr = solver.Element(
        service_by_vehicle, solver.Max(vehicle_var, 0).Var()
    )
    for block_start, block_end in blocked_offsets:
        if block_start <= 0:
            # Block already runs at the horizon origin: nothing can finish
            # before it, so the start domain alone decides.
            continue
        finishes_before = (cumul + service_expr <= block_start).Var()
        starts_after = (cumul >= block_end + 1).Var()
        solver.Add(finishes_before + starts_after + (1 - active) >= 1)


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
    materials = {
        str(order.load_material or "")
        for order in context.cluster_orders
        if _load_kg(order.load_demand) > 0
    }
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
            quantity = int(_load_kg(order.load_demand) * scale)
            if quantity <= 0:
                continue
            paired = bool(str(order.pickup_location_ref or ""))
            if node.kind == NODE_PICKUP:
                demands[i] = quantity
            elif node.kind == NODE_TASK:
                demands[i] = -quantity if paired else quantity

        capacities = [
            int(_compartment_capacity_kg(rv["prime"], material) * scale)
            for rv in context.routing_vehicles
        ]
        max_capacity = max(capacities)
        for i in reload_node_idxs:
            demands[i] = -max_capacity

        def demand_callback(from_index: int, demands: list[int] = demands) -> int:
            return demands[manager.IndexToNode(from_index)]

        demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        name = f"Load_{material}" if material else "Load"
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


def _compartment_capacity_kg(prime: Any, material: str) -> float:
    """Vehicle capacity for one material: compartment, aggregate, unlimited."""
    compartments = (
        prime.load_capacities if isinstance(prime.load_capacities, dict) else {}
    )
    value = compartments.get(material) if material else None
    if value is None:
        value = prime.load_capacity
    capacity_kg = _load_kg(value)
    return capacity_kg if capacity_kg > 0 else constants.VEHICLE_LOAD_UNLIMITED_KG


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
    from fl_op.solver.restrictions import merge_intervals

    solver = routing.solver()
    for rv_idx, routing_vehicle in enumerate(context.routing_vehicles):
        vehicle_id = routing_vehicle["prime"].asset_id
        related_id = routing_vehicle["related"].asset_id
        offsets = []
        for start_epoch, end_epoch in [
            *held_windows.get(vehicle_id, []),
            *held_windows.get(related_id, []),
        ]:
            start_off = max(0, int(start_epoch) - now_epoch)
            end_off = min(int(end_epoch) - now_epoch, _ROUTING_HORIZON_S)
            if end_off <= 0 or start_off >= _ROUTING_HORIZON_S or end_off <= start_off:
                continue
            offsets.append((start_off, end_off))
        intervals = [
            solver.FixedDurationIntervalVar(
                start_off,
                start_off,
                end_off - start_off,
                False,
                f"held_{vehicle_id}_{seq}",
            )
            # Overlapping holds (the pair held by one assignment) merge into
            # one break each, so the dimension never sees duplicate breaks.
            for seq, (start_off, end_off) in enumerate(merge_intervals(offsets))
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

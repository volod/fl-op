"""Capacity-aware route construction for the OR-Tools warm start."""

from typing import Optional

from fl_op.solver.cluster.bundles import bundle_supports_operation
from fl_op.solver.cluster.context import ClusterContext
from fl_op.solver.cluster.loads import compartment_capacity_kg, load_kg
from fl_op.solver.routing_model import (
    NODE_RELOAD,
    RoutingNode,
    pickup_node_indices,
    task_node_indices,
)


def build_capacity_aware_initial_routes(
    context: ClusterContext,
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    nodes: list[RoutingNode],
) -> list[list[int]]:
    """Seed all compatible tasks and any reloads they need into warm routes.

    The allocation-level greedy assignment intentionally claims each implement
    once, so it normally seeds only one task per routing vehicle. This builder
    keeps those preferred vehicle choices, then places the remaining tasks on
    compatible allocated bundles. Plain depot-carried loads insert a reload
    before the next task when a material compartment would overflow. Paired
    pickups release their load at delivery and therefore do not consume
    persistent route capacity.
    """
    task_nodes = task_node_indices(nodes)
    pickup_nodes = pickup_node_indices(nodes)
    reload_nodes = [
        node_idx for node_idx, node in enumerate(nodes) if node.kind == NODE_RELOAD
    ]
    routes: list[list[int]] = [[] for _ in context.routing_vehicles]
    route_loads: list[dict[str, float]] = [
        {} for _ in context.routing_vehicles
    ]
    route_task_counts = [0] * len(context.routing_vehicles)
    vehicle_id_to_route = {
        rv["prime"].asset_id: route_idx
        for route_idx, rv in enumerate(context.routing_vehicles)
    }
    source_vehicle_ids = {index: asset_id for asset_id, index in vehicle_index.items()}
    used_reload_nodes: set[int] = set()
    served_alternative_groups: set[str] = set()

    for order_idx, order in enumerate(context.cluster_orders):
        alternative_group = str(getattr(order, "alternative_group_ref", "") or "")
        if alternative_group and alternative_group in served_alternative_groups:
            continue
        compatible = [
            route_idx
            for route_idx, routing_vehicle in enumerate(context.routing_vehicles)
            if bundle_supports_operation(
                routing_vehicle, str(order.operation_type or "").upper()
            )
        ]
        if not compatible:
            continue

        preferred_route = _preferred_route(
            order.task_id,
            greedy_assignment,
            source_vehicle_ids,
            vehicle_id_to_route,
            compatible,
        )
        demand = load_kg(order.load_demand)
        material = str(order.load_material or "")
        paired = order_idx in pickup_nodes
        route_idx = _select_route(
            context,
            compatible,
            preferred_route,
            route_loads,
            route_task_counts,
            material,
            demand,
            paired,
        )
        if route_idx is None:
            continue

        capacity = compartment_capacity_kg(
            context.routing_vehicles[route_idx]["prime"], material
        )
        if not paired and route_loads[route_idx].get(material, 0.0) + demand > capacity:
            reload_node = next(
                (node for node in reload_nodes if node not in used_reload_nodes), None
            )
            if reload_node is None:
                continue
            routes[route_idx].append(reload_node)
            used_reload_nodes.add(reload_node)
            route_loads[route_idx].clear()
        if paired:
            routes[route_idx].append(pickup_nodes[order_idx])
        routes[route_idx].append(task_nodes[order_idx])
        if not paired and demand > 0:
            route_loads[route_idx][material] = (
                route_loads[route_idx].get(material, 0.0) + demand
            )
        route_task_counts[route_idx] += 1
        if alternative_group:
            served_alternative_groups.add(alternative_group)

    return routes


def _preferred_route(
    task_id: str,
    greedy_assignment: dict[str, tuple[int, int]],
    source_vehicle_ids: dict[int, str],
    vehicle_id_to_route: dict[str, int],
    compatible: list[int],
) -> Optional[int]:
    greedy = greedy_assignment.get(task_id)
    if greedy is None:
        return None
    preferred_vehicle = source_vehicle_ids.get(greedy[0])
    preferred_route = vehicle_id_to_route.get(preferred_vehicle or "")
    return preferred_route if preferred_route in compatible else None


def _select_route(
    context: ClusterContext,
    compatible: list[int],
    preferred_route: Optional[int],
    route_loads: list[dict[str, float]],
    route_task_counts: list[int],
    material: str,
    demand: float,
    paired: bool,
) -> Optional[int]:
    candidates: list[tuple[bool, int, int, int]] = []
    for route_idx in compatible:
        capacity = compartment_capacity_kg(
            context.routing_vehicles[route_idx]["prime"], material
        )
        if demand > capacity:
            continue
        current_load = route_loads[route_idx].get(material, 0.0)
        needs_reload = not paired and current_load + demand > capacity
        candidates.append(
            (
                route_idx != preferred_route,
                int(needs_reload),
                route_task_counts[route_idx],
                route_idx,
            )
        )
    return min(candidates)[-1] if candidates else None

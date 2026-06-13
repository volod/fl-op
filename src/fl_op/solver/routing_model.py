"""OR-Tools routing model helpers: node table, warm-start, and solution extraction."""

import dataclasses
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.core.constants import RELATED_MATERIAL_FILL_RATIO
from fl_op.core.constants import RATE_TYPE_FUEL
from fl_op.solver.cost_rates import (
    ResourcePrices,
    vehicle_energy_consumption_rate,
    vehicle_energy_resource_type,
    vehicle_energy_unit,
)
from fl_op.solver.travel_time import (
    TravelLookup,
    _estimate_operation_seconds,
    travel_seconds,
    travel_mode_for_vehicle,
)

# Routing node kinds. The depot is always node 0; each order contributes its
# pickup node (paired pickup-and-delivery tasks only) followed by its task
# node; optional depot reload visits close the table.
NODE_DEPOT = "depot"
NODE_TASK = "task"
NODE_PICKUP = "pickup"
NODE_RELOAD = "reload"


@dataclasses.dataclass(frozen=True, slots=True)
class RoutingNode:
    """One node of the routing graph.

    ``order_idx`` indexes cluster_orders for task and pickup nodes and is -1
    for the depot and reload nodes.
    """

    kind: str
    order_idx: int
    location_ref: str
    lat: float
    lon: float


def build_node_table(
    cluster_orders: list[Any],
    field_map: dict[str, Any],
    depot_lat: float,
    depot_lon: float,
    depot_id: str = "",
    n_reload_nodes: int = 0,
) -> list[RoutingNode]:
    """Build the routing node table: depot, pickups/tasks, reload visits."""
    nodes = [RoutingNode(NODE_DEPOT, -1, depot_id, depot_lat, depot_lon)]
    for order_idx, order in enumerate(cluster_orders):
        pickup_ref = str(getattr(order, "pickup_location_ref", "") or "")
        if pickup_ref:
            pickup = field_map.get(pickup_ref)
            nodes.append(
                RoutingNode(
                    NODE_PICKUP,
                    order_idx,
                    pickup_ref,
                    float(pickup.lat) if pickup else depot_lat,
                    float(pickup.lon) if pickup else depot_lon,
                )
            )
        field = field_map.get(order.location_ref)
        nodes.append(
            RoutingNode(
                NODE_TASK,
                order_idx,
                str(order.location_ref or ""),
                float(field.lat) if field else depot_lat,
                float(field.lon) if field else depot_lon,
            )
        )
    for _ in range(n_reload_nodes):
        nodes.append(RoutingNode(NODE_RELOAD, -1, depot_id, depot_lat, depot_lon))
    return nodes


def task_node_indices(nodes: list[RoutingNode]) -> dict[int, int]:
    """order_idx -> node-table index of the order's task node."""
    return {n.order_idx: i for i, n in enumerate(nodes) if n.kind == NODE_TASK}


def pickup_node_indices(nodes: list[RoutingNode]) -> dict[int, int]:
    """order_idx -> node-table index of the order's pickup node."""
    return {n.order_idx: i for i, n in enumerate(nodes) if n.kind == NODE_PICKUP}


def build_time_matrix(
    nodes: list[RoutingNode],
    travel_lookup: Optional[TravelLookup] = None,
    travel_mode: Optional[str] = None,
) -> list[list[int]]:
    """Pairwise arc times: network shortest path where one exists, haversine
    otherwise."""
    return [
        [
            travel_seconds(
                a.location_ref, b.location_ref, a.lat, a.lon, b.lat, b.lon,
                travel_lookup, travel_mode,
            )
            for b in nodes
        ]
        for a in nodes
    ]


def build_vehicle_time_matrices(
    nodes: list[RoutingNode],
    routing_vehicles: list[dict[str, Any]],
    travel_lookup: Optional[TravelLookup] = None,
) -> list[list[list[int]]]:
    """Pairwise arc times per routing vehicle mode."""
    return [
        build_time_matrix(
            nodes,
            travel_lookup,
            travel_mode_for_vehicle(rv["prime"]),
        )
        for rv in routing_vehicles
    ]


def _build_initial_routes(
    routing_vehicles: list[dict[str, Any]],
    cluster_orders: list[dict[str, Any]],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    nodes: list[RoutingNode],
) -> list[list[int]]:
    """Build greedy warm-start route hints from prior greedy assignment.

    A paired order's pickup node is inserted right before its task node so
    the hint satisfies the pickup-and-delivery constraints.
    """
    idx_to_vid: dict[int, str] = {idx: vid for vid, idx in vehicle_index.items()}
    task_nodes = task_node_indices(nodes)
    pickup_nodes = pickup_node_indices(nodes)
    initial_routes: list[list[int]] = []
    used_orders: set[int] = set()

    for rv in routing_vehicles:
        vid = rv["prime"].asset_id
        route: list[int] = []
        for order_idx, order in enumerate(cluster_orders):
            ga = greedy_assignment.get(order.task_id)
            if ga is None or idx_to_vid.get(ga[0]) != vid or order_idx in used_orders:
                continue
            if order_idx in pickup_nodes:
                route.append(pickup_nodes[order_idx])
            route.append(task_nodes[order_idx])
            used_orders.add(order_idx)
        initial_routes.append(route)

    return initial_routes


def _extract_dispatch_packages(
    solution: Any,
    routing: Any,
    manager: Any,
    routing_vehicles: list[dict[str, Any]],
    cluster_orders: list[dict[str, Any]],
    nodes: list[RoutingNode],
    time_dim: Any,
    cluster_id: str,
    depot_id: str,
    cluster_dict: dict[str, Any],
    now_epoch: int,
    time_matrix: Optional[list[list[int]] | list[list[list[int]]]] = None,
    resource_prices: Optional[ResourcePrices] = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Read the OR-Tools solution and build dispatch package dicts.

    Returns (dispatch_packages, served_task_ids). One package per served task
    node; pickup and reload stops are not packages of their own, but their
    travel legs accumulate into the next task's inbound fuel. The margin is
    the order revenue net of fuel and material at the resolved prices.
    """
    if resource_prices is None:
        resource_prices = ResourcePrices()
    dispatch_packages: list[dict[str, Any]] = []
    served_task_ids: set[str] = set()

    for rv_idx, rv in enumerate(routing_vehicles):
        vid = rv["prime"].asset_id
        iid = rv["related"].asset_id
        index = routing.Start(rv_idx)
        prev_node = 0
        travel_s_in = 0

        while not routing.IsEnd(index):
            node_idx = manager.IndexToNode(index)
            node = nodes[node_idx]
            if time_matrix is not None and node_idx != prev_node:
                travel_s_in += _matrix_seconds(time_matrix, rv_idx, prev_node, node_idx)
            prev_node = node_idx
            if node.kind != NODE_TASK:
                index = solution.Value(routing.NextVar(index))
                continue

            order = cluster_orders[node.order_idx]
            oid = order.task_id
            served_task_ids.add(oid)

            arrival_s = solution.Value(time_dim.CumulVar(index))
            op_seconds = _estimate_operation_seconds(order, rv["related"])
            start_epoch = now_epoch + arrival_s
            end_epoch = start_epoch + op_seconds
            energy_resource_type = vehicle_energy_resource_type(rv["prime"])
            energy_unit = vehicle_energy_unit(rv["prime"])
            energy_quantity = (
                (op_seconds + travel_s_in)
                / 3600.0
                * vehicle_energy_consumption_rate(rv["prime"])
            )
            energy_cost = (
                energy_quantity * resource_prices.price_for(energy_resource_type)
            )
            fuel_l = (
                energy_quantity if energy_resource_type == RATE_TYPE_FUEL else 0.0
            )
            travel_s_in = 0
            fertilizer_kg = (
                float(rv["related"].material_capacity) * RELATED_MATERIAL_FILL_RATIO
            )
            margin_eur = (
                float(order.revenue)
                - energy_cost
                - fertilizer_kg * resource_prices.material_eur_per_kg
            )

            dispatch_packages.append(
                {
                    "dispatch_id": str(uuid.uuid4()),
                    "cluster_id": cluster_id,
                    "prime_asset_id": vid,
                    "related_asset_id": iid,
                    "operator_asset_id": (
                        cluster_dict.get("task_operators", {}).get(oid)
                        or cluster_dict.get("operator_ref", "")
                    ),
                    "task_id": oid,
                    "depot_ref": depot_id,
                    "scheduled_start": datetime.fromtimestamp(start_epoch, tz=timezone.utc).isoformat(),
                    "scheduled_end": datetime.fromtimestamp(end_epoch, tz=timezone.utc).isoformat(),
                    "route_waypoints": [{"lat": node.lat, "lon": node.lon}],
                    "estimated_fuel_l": round(fuel_l, 2),
                    "energy_resource_type": energy_resource_type,
                    "estimated_energy_quantity": round(energy_quantity, 2),
                    "estimated_energy_unit": energy_unit,
                    "estimated_energy_cost_eur": round(energy_cost, 2),
                    "estimated_fertilizer_kg": round(fertilizer_kg, 2),
                    "estimated_margin_eur": round(margin_eur, 2),
                }
            )
            index = solution.Value(routing.NextVar(index))

    return dispatch_packages, served_task_ids


def _matrix_seconds(
    time_matrix: list[list[int]] | list[list[list[int]]],
    vehicle_idx: int,
    from_node: int,
    to_node: int,
) -> int:
    if not time_matrix:
        return 0
    first = time_matrix[0]
    if first and isinstance(first[0], list):
        return int(time_matrix[vehicle_idx][from_node][to_node])  # type: ignore[index]
    return int(time_matrix[from_node][to_node])  # type: ignore[index]

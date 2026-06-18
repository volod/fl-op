"""OR-Tools routing model helpers: node table, warm-start, and solution extraction."""

import dataclasses
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.core.constants import FALLBACK_TRAVEL_SPEED_KMH
from fl_op.core.constants import RATE_TYPE_FUEL
from fl_op.core.constants import RELATED_MATERIAL_FILL_RATIO
from fl_op.core.geometry import haversine_km
from fl_op.solver.cost_rates import (
    ResourcePrices,
    operator_wage_eur_per_h,
    vehicle_energy_consumption_rate,
    vehicle_energy_resource_type,
    vehicle_energy_unit,
    vehicle_machine_wear_eur_per_h,
)
from fl_op.solver.routing_geography import (
    RouteRestriction,
    active_polygons,
    detour_waypoints,
    obstacle_aware_travel_seconds,
    restricted_polygons_for_vehicle,
    unconditional_polygons,
)
from fl_op.solver.travel_time import (
    TravelLookup,
    _estimate_operation_seconds,
    network_distance_km,
    network_toll_eur,
    travel_mode_for_vehicle,
    vehicle_fallback_speed_kmh,
)

# Routing node kinds. The depot is always node 0; each order contributes its
# pickup node (paired pickup-and-delivery tasks only) followed by its task
# node; optional depot reload visits close the table.
NODE_DEPOT = "depot"
NODE_TASK = "task"
NODE_PICKUP = "pickup"
NODE_RELOAD = "reload"

logger = logging.getLogger(__name__)


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
    pickup_map: Optional[dict[str, Any]] = None,
) -> list[RoutingNode]:
    """Build the routing node table: depot, pickups/tasks, reload visits.

    Pickup locations resolve against ``pickup_map`` (every known work site,
    supplier, and depot/hub), falling back to ``field_map`` so single-domain
    callers that only pass sites are unaffected. A ref absent from both falls
    back to the cluster depot coordinates and is logged as invalid input.
    """
    pickup_lookup = pickup_map if pickup_map is not None else field_map
    nodes = [RoutingNode(NODE_DEPOT, -1, depot_id, depot_lat, depot_lon)]
    for order_idx, order in enumerate(cluster_orders):
        pickup_ref = str(getattr(order, "pickup_location_ref", "") or "")
        if pickup_ref:
            pickup = pickup_lookup.get(pickup_ref) or field_map.get(pickup_ref)
            if pickup is None:
                logger.warning(
                    "Pickup location %r for task %s is absent from canonical "
                    "locations; falling back to depot coordinates",
                    pickup_ref,
                    getattr(order, "task_id", "?"),
                )
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
    fallback_speed_kmh: float = FALLBACK_TRAVEL_SPEED_KMH,
    restricted_polygons: Optional[list[list[tuple[float, float]]]] = None,
) -> list[list[int]]:
    """Pairwise arc times: network shortest path where one exists, haversine
    otherwise. Geometric fallbacks detour around ``restricted_polygons`` when
    supplied. ``fallback_speed_kmh`` prices the fallback leg per vehicle."""
    obstacles = restricted_polygons or []
    return [
        [
            obstacle_aware_travel_seconds(
                a.location_ref,
                b.location_ref,
                (a.lat, a.lon),
                (b.lat, b.lon),
                travel_lookup,
                travel_mode,
                fallback_speed_kmh,
                obstacles,
            )
            for b in nodes
        ]
        for a in nodes
    ]


def build_vehicle_time_matrices(
    nodes: list[RoutingNode],
    routing_vehicles: list[dict[str, Any]],
    travel_lookup: Optional[TravelLookup] = None,
    restricted_locations: Optional[list[Any]] = None,
    route_restrictions: Optional[list[list[RouteRestriction]]] = None,
) -> list[list[list[int]]]:
    """Pairwise arc times per routing vehicle.

    Each vehicle's matrix uses its own travel mode (selecting the matching
    network) and its own declared travel speed for the no-network haversine
    legs, so a genuinely faster mover gets shorter fallback legs and
    ``--objective time`` can prefer it.
    """
    locations = restricted_locations or []
    matrices: list[list[list[int]]] = []
    for vehicle_idx, routing_vehicle in enumerate(routing_vehicles):
        polygons = (
            unconditional_polygons(route_restrictions[vehicle_idx])
            if route_restrictions is not None
            else restricted_polygons_for_vehicle(locations, routing_vehicle)
        )
        matrices.append(
            build_time_matrix(
                nodes,
                travel_lookup,
                travel_mode_for_vehicle(routing_vehicle["prime"]),
                vehicle_fallback_speed_kmh(routing_vehicle["prime"]),
                polygons,
            )
        )
    return matrices


def build_vehicle_cost_matrices(
    nodes: list[RoutingNode],
    routing_vehicles: list[dict[str, Any]],
    travel_lookup: Optional[TravelLookup],
    toll_eur_per_km: float,
) -> tuple[list[list[list[float]]], list[list[list[float]]]]:
    """Per-vehicle (distance_km, toll_eur) arc matrices, network-aware.

    Distance prefers the vehicle mode's network-link distance and falls back to
    the geodesic estimate. Toll uses the per-link toll where a travel link exists
    (so only genuinely tolled segments charge) and falls back to the fleet
    per-kilometre rate on the geodesic distance for off-network legs.
    """
    n = len(nodes)
    distance_matrices: list[list[list[float]]] = []
    toll_matrices: list[list[list[float]]] = []
    for routing_vehicle in routing_vehicles:
        mode = travel_mode_for_vehicle(routing_vehicle["prime"])
        dist_matrix = [[0.0] * n for _ in range(n)]
        toll_matrix = [[0.0] * n for _ in range(n)]
        for i, a in enumerate(nodes):
            for j, b in enumerate(nodes):
                if i == j:
                    continue
                geodesic_km = haversine_km(a.lat, a.lon, b.lat, b.lon)
                net_km = network_distance_km(
                    travel_lookup, a.location_ref, b.location_ref, mode
                )
                dist_matrix[i][j] = net_km if net_km is not None else geodesic_km
                net_toll = network_toll_eur(
                    travel_lookup, a.location_ref, b.location_ref, mode
                )
                toll_matrix[i][j] = (
                    net_toll
                    if net_toll is not None
                    else geodesic_km * toll_eur_per_km
                )
        distance_matrices.append(dist_matrix)
        toll_matrices.append(toll_matrix)
    return distance_matrices, toll_matrices


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
    distance_matrices: Optional[list[list[list[float]]]] = None,
    toll_matrices: Optional[list[list[list[float]]]] = None,
    restricted_locations: Optional[list[Any]] = None,
    travel_lookup: Optional[TravelLookup] = None,
    route_restrictions: Optional[list[list[RouteRestriction]]] = None,
    service_fee_eur_per_visit: float = 0.0,
    operator_wages: Optional[dict[str, float]] = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Read the OR-Tools solution and build dispatch package dicts.

    Returns (dispatch_packages, served_task_ids). One package per served task
    node; pickup and reload stops are not packages of their own, but their
    travel legs accumulate into the next task's inbound fuel and distance. The
    margin is the order revenue net of energy and material at the resolved
    prices, less the operating cost of the operating hours (driver labour and
    machine wear over travel plus on-task service time) and the tolls over the
    inbound travel distance.
    """
    if resource_prices is None:
        resource_prices = ResourcePrices()
    dispatch_packages: list[dict[str, Any]] = []
    served_task_ids: set[str] = set()
    wages = operator_wages or {}

    for rv_idx, rv in enumerate(routing_vehicles):
        vid = rv["prime"].asset_id
        iid = rv["related"].asset_id
        # Per-vehicle machine-wear rate (EUR/operating hour), fleet rate fallback.
        wear_eur_per_h = vehicle_machine_wear_eur_per_h(
            rv["prime"], resource_prices.machine_wear_eur_per_h
        )
        index = routing.Start(rv_idx)
        prev_index = index
        prev_node = 0
        travel_s_in = 0
        dist_km_in = 0.0
        toll_eur_in = 0.0
        route_waypoints_in: list[dict[str, float]] = []
        travel_mode = travel_mode_for_vehicle(rv["prime"])
        vehicle_restrictions = (
            route_restrictions[rv_idx]
            if route_restrictions is not None
            else [
                RouteRestriction(polygon)
                for polygon in restricted_polygons_for_vehicle(
                    restricted_locations or [], rv
                )
            ]
        )

        while not routing.IsEnd(index):
            node_idx = manager.IndexToNode(index)
            node = nodes[node_idx]
            if node_idx != prev_node:
                previous = nodes[prev_node]
                arc_start_epoch = now_epoch + solution.Value(
                    time_dim.CumulVar(prev_index)
                )
                arc_end_epoch = now_epoch + solution.Value(
                    time_dim.CumulVar(index)
                )
                if time_matrix is not None:
                    travel_s_in += _matrix_seconds(
                        time_matrix, rv_idx, prev_node, node_idx
                    )
                if distance_matrices is not None:
                    dist_km_in += distance_matrices[rv_idx][prev_node][node_idx]
                if toll_matrices is not None:
                    toll_eur_in += toll_matrices[rv_idx][prev_node][node_idx]
                route_waypoints_in.extend(
                    {"lat": lat, "lon": lon}
                    for lat, lon in detour_waypoints(
                        previous.location_ref,
                        node.location_ref,
                        (previous.lat, previous.lon),
                        (node.lat, node.lon),
                        travel_lookup,
                        travel_mode,
                        active_polygons(
                            vehicle_restrictions,
                            arc_start_epoch,
                            arc_end_epoch,
                        ),
                    )
                )
            prev_node = node_idx
            prev_index = index
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
            operating_hours = (op_seconds + travel_s_in) / 3600.0
            energy_quantity = (
                operating_hours * vehicle_energy_consumption_rate(rv["prime"])
            )
            energy_cost = (
                energy_quantity * resource_prices.price_for(energy_resource_type)
            )
            fuel_l = (
                energy_quantity if energy_resource_type == RATE_TYPE_FUEL else 0.0
            )
            operator_id = (
                cluster_dict.get("task_operators", {}).get(oid)
                or cluster_dict.get("operator_ref", "")
            )
            wage_eur_per_h = wages.get(operator_id, resource_prices.labor_eur_per_h)
            labor_cost = operating_hours * wage_eur_per_h
            wear_cost = operating_hours * wear_eur_per_h
            toll_cost = toll_eur_in
            service_fee_cost = service_fee_eur_per_visit
            travel_km = dist_km_in
            travel_s_in = 0
            dist_km_in = 0.0
            toll_eur_in = 0.0
            fertilizer_kg = (
                float(rv["related"].material_capacity) * RELATED_MATERIAL_FILL_RATIO
            )
            margin_eur = (
                float(order.revenue)
                - energy_cost
                - fertilizer_kg * resource_prices.material_eur_per_kg
                - labor_cost
                - wear_cost
                - toll_cost
                - service_fee_cost
            )

            dispatch_packages.append(
                {
                    "dispatch_id": str(uuid.uuid4()),
                    "cluster_id": cluster_id,
                    "prime_asset_id": vid,
                    "related_asset_id": iid,
                    "operator_asset_id": operator_id,
                    "task_id": oid,
                    "depot_ref": depot_id,
                    "scheduled_start": datetime.fromtimestamp(start_epoch, tz=timezone.utc).isoformat(),
                    "scheduled_end": datetime.fromtimestamp(end_epoch, tz=timezone.utc).isoformat(),
                    "route_waypoints": [
                        *route_waypoints_in,
                        {"lat": node.lat, "lon": node.lon},
                    ],
                    "estimated_fuel_l": round(fuel_l, 2),
                    "energy_resource_type": energy_resource_type,
                    "estimated_energy_quantity": round(energy_quantity, 2),
                    "estimated_energy_unit": energy_unit,
                    "estimated_energy_cost_eur": round(energy_cost, 2),
                    "estimated_fertilizer_kg": round(fertilizer_kg, 2),
                    "estimated_distance_km": round(travel_km, 2),
                    "estimated_labor_cost_eur": round(labor_cost, 2),
                    "estimated_machine_wear_cost_eur": round(wear_cost, 2),
                    "estimated_toll_cost_eur": round(toll_cost, 2),
                    "estimated_service_fee_eur": round(service_fee_cost, 2),
                    "estimated_margin_eur": round(margin_eur, 2),
                }
            )
            route_waypoints_in = []
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

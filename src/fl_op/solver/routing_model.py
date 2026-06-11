"""OR-Tools routing model helpers: node geometry, warm-start, and solution extraction."""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.solver.travel_time import (
    TravelLookup,
    _estimate_operation_seconds,
    travel_seconds,
)


def _build_node_geometry(
    cluster_orders: list[dict[str, Any]],
    field_map: dict[str, dict[str, Any]],
    depot_lat: float,
    depot_lon: float,
    depot_id: str = "",
    travel_lookup: Optional[TravelLookup] = None,
) -> tuple[list[float], list[float], list[list[int]]]:
    """Return (node_lats, node_lons, time_matrix) for the routing model.

    Node 0 is the depot; nodes 1..N are orders in cluster_orders order.
    Arc times come from the travel network where a link exists for the
    location pair, otherwise from the haversine estimate.
    """
    node_lats: list[float] = [depot_lat]
    node_lons: list[float] = [depot_lon]
    node_refs: list[str] = [depot_id]
    for order in cluster_orders:
        field = field_map.get(order.location_ref)
        if field:
            node_lats.append(float(field.lat))
            node_lons.append(float(field.lon))
        else:
            node_lats.append(depot_lat)
            node_lons.append(depot_lon)
        node_refs.append(str(order.location_ref or ""))

    n_nodes = len(node_lats)
    time_matrix: list[list[int]] = [
        [
            travel_seconds(
                node_refs[i], node_refs[j],
                node_lats[i], node_lons[i], node_lats[j], node_lons[j],
                travel_lookup,
            )
            for j in range(n_nodes)
        ]
        for i in range(n_nodes)
    ]
    return node_lats, node_lons, time_matrix


def _build_initial_routes(
    routing_vehicles: list[dict[str, Any]],
    cluster_orders: list[dict[str, Any]],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
) -> list[list[int]]:
    """Build greedy warm-start route hints from prior greedy assignment."""
    idx_to_vid: dict[int, str] = {idx: vid for vid, idx in vehicle_index.items()}
    initial_routes: list[list[int]] = []
    used_order_nodes: set[int] = set()

    for rv in routing_vehicles:
        vid = rv["prime"].asset_id
        route: list[int] = []
        for node_idx, order in enumerate(cluster_orders, start=1):
            oid = order.task_id
            ga = greedy_assignment.get(oid)
            if ga is not None:
                assigned_vid = idx_to_vid.get(ga[0])
                if assigned_vid == vid and node_idx not in used_order_nodes:
                    route.append(node_idx)
                    used_order_nodes.add(node_idx)
        initial_routes.append(route)

    return initial_routes


def _extract_dispatch_packages(
    solution: Any,
    routing: Any,
    manager: Any,
    routing_vehicles: list[dict[str, Any]],
    cluster_orders: list[dict[str, Any]],
    field_map: dict[str, dict[str, Any]],
    node_lats: list[float],
    node_lons: list[float],
    time_dim: Any,
    cluster_id: str,
    depot_id: str,
    cluster_dict: dict[str, Any],
    now_epoch: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Read the OR-Tools solution and build dispatch package dicts.

    Returns (dispatch_packages, served_task_ids).
    """
    _FERTILIZER_FILL_RATIO = 0.8

    dispatch_packages: list[dict[str, Any]] = []
    served_task_ids: set[str] = set()

    for rv_idx, rv in enumerate(routing_vehicles):
        vid = rv["prime"].asset_id
        iid = rv["related"].asset_id
        index = routing.Start(rv_idx)

        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node == 0:
                index = solution.Value(routing.NextVar(index))
                continue

            order = cluster_orders[node - 1]
            oid = order.task_id
            served_task_ids.add(oid)

            arrival_s = solution.Value(time_dim.CumulVar(index))
            op_seconds = _estimate_operation_seconds(order, rv["related"])
            start_epoch = now_epoch + arrival_s
            end_epoch = start_epoch + op_seconds
            op_hours = op_seconds / 3600.0

            dispatch_packages.append(
                {
                    "dispatch_id": str(uuid.uuid4()),
                    "cluster_id": cluster_id,
                    "prime_asset_id": vid,
                    "related_asset_id": iid,
                    "operator_asset_id": cluster_dict.get("operator_ref", ""),
                    "task_id": oid,
                    "depot_ref": depot_id,
                    "scheduled_start": datetime.fromtimestamp(start_epoch, tz=timezone.utc).isoformat(),
                    "scheduled_end": datetime.fromtimestamp(end_epoch, tz=timezone.utc).isoformat(),
                    "route_waypoints": [{"lat": node_lats[node], "lon": node_lons[node]}],
                    "estimated_fuel_l": round(
                        op_hours * float(rv["prime"].fuel_consumption_rate), 2
                    ),
                    "estimated_fertilizer_kg": round(
                        float(rv["related"].material_capacity) * _FERTILIZER_FILL_RATIO, 2
                    ),
                    "estimated_margin_eur": round(float(order.revenue), 2),
                }
            )
            index = solution.Value(routing.NextVar(index))

    return dispatch_packages, served_task_ids

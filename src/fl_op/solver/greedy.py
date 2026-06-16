"""Greedy warm-start scorer for OR-Tools initial solution hints.

vectorized_score() computes a score matrix over all feasible (V-I pair, order)
combinations in a single numpy broadcast — no Python-level loops over pairs.

Score = gross_margin_estimate - repositioning_cost

greedy_assign() returns a dict {task_id: (vehicle_id, implement_id)} by
taking the top-1 scoring V-I pair for each order.
"""

import logging
import math
from typing import Any, Optional

import numpy as np

from fl_op.core.constants import (
    FALLBACK_REVENUE_EUR_PER_HA,
    FUEL_COST_EUR_PER_L,
    OBJECTIVE_MODE_TIME,
    SCORE_WEIGHT_MARGIN,
    SCORE_WEIGHT_REPOSITION,
)
from fl_op.core.geometry import (
    haversine_km,
    haversine_km_vector,
    nearest_indices,
    travel_time_seconds,
)
from fl_op.solver.cost_rates import (
    ResourcePrices,
    vehicle_energy_consumption_rate,
    vehicle_energy_resource_type,
)
from fl_op.solver.travel_time import (
    TravelLookup,
    _estimate_operation_seconds,
    network_seconds,
    travel_mode_for_vehicle,
    travel_network_nodes,
)

logger = logging.getLogger(__name__)

# Assumed average field operation hours per hectare for margin estimation
_OPERATION_H_PER_HA = 1.0


def _estimate_gross_margin(order: Any) -> float:
    """Rough gross revenue estimate for completing this order."""
    revenue = float(order.revenue)
    return revenue if revenue > 0 else float(order.area) * FALLBACK_REVENUE_EUR_PER_HA


def _network_seconds_or_nan(
    travel_lookup: TravelLookup,
    from_ref: str,
    to_ref: str,
    travel_mode: str = "any",
) -> float:
    """Directed network time for a pair (reverse fallback), NaN when no path."""
    if not from_ref or not to_ref or from_ref == to_ref:
        return math.nan
    seconds = network_seconds(
        travel_lookup, from_ref, to_ref, travel_mode
    ) or network_seconds(
        travel_lookup, to_ref, from_ref, travel_mode
    )
    return float(seconds) if seconds else math.nan


def _vehicle_network_access(
    vehicles: list[Any],
    travel_lookup: Optional[TravelLookup],
    location_coords: Optional[dict[str, tuple[float, float]]],
) -> list[Optional[tuple[str, int]]]:
    """Per-vehicle nearest travel-network node and the hop onto it.

    Maps each vehicle's current position to the nearest network node that has
    known coordinates, generalizing the road access point beyond the vehicle's
    home depot: a vehicle far from its depot can still join the network at a
    local node. Returns ``(access_ref, approach_seconds)`` per vehicle, or None
    when no node is locatable (the caller then falls back to the home depot and
    the straight-line estimate).
    """
    empty: list[Optional[tuple[str, int]]] = [None] * len(vehicles)
    if not vehicles or not travel_lookup or not location_coords:
        return empty
    located = [
        (ref, location_coords[ref])
        for ref in travel_network_nodes(travel_lookup)
        if ref in location_coords
    ]
    if not located:
        return empty
    node_lats = np.array([coord[0] for _ref, coord in located])
    node_lons = np.array([coord[1] for _ref, coord in located])
    v_lats = np.array([float(v.lat) for v in vehicles])
    v_lons = np.array([float(v.lon) for v in vehicles])
    nearest = nearest_indices(v_lats, v_lons, node_lats, node_lons)
    access: list[Optional[tuple[str, int]]] = []
    for i, vehicle in enumerate(vehicles):
        node_idx = int(nearest[i])
        approach_s = travel_time_seconds(
            float(vehicle.lat),
            float(vehicle.lon),
            float(node_lats[node_idx]),
            float(node_lons[node_idx]),
        )
        access.append((located[node_idx][0], approach_s))
    return access


def _estimate_repositioning_cost(
    vehicle: Any,
    field: Any,
    fuel_price_eur_per_l: Optional[float] = None,
    resource_prices: Optional[ResourcePrices] = None,
) -> float:
    """Energy cost to drive from vehicle's current position to the field centroid."""
    fuel_price = (
        fuel_price_eur_per_l if fuel_price_eur_per_l is not None else FUEL_COST_EUR_PER_L
    )
    energy_price = (
        resource_prices.price_for(vehicle_energy_resource_type(vehicle))
        if resource_prices is not None
        else fuel_price
    )
    dist_km = haversine_km(
        float(vehicle.lat),
        float(vehicle.lon),
        float(field.lat),
        float(field.lon),
    )
    speed_kmh = float(vehicle.travel_speed)
    hours = dist_km / speed_kmh if speed_kmh > 0 else 0
    operating_eur_per_h = (
        resource_prices.operating_eur_per_h if resource_prices is not None else 0.0
    )
    toll_eur_per_km = (
        resource_prices.toll_eur_per_km if resource_prices is not None else 0.0
    )
    return (
        hours * (vehicle_energy_consumption_rate(vehicle) * energy_price + operating_eur_per_h)
        + dist_km * toll_eur_per_km
    )


def vectorized_score(
    orders: list[Any],
    vehicles: list[Any],
    implements: list[Any],
    fields: list[Any],
    feasible_pairs: dict[str, list[tuple[int, int]]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
    fuel_price_eur_per_l: Optional[float] = None,
    resource_prices: Optional[ResourcePrices] = None,
    score_weight_margin: Optional[float] = None,
    score_weight_reposition: Optional[float] = None,
    travel_lookup: Optional[TravelLookup] = None,
    optimization_objective: str = "cost",
    location_coords: Optional[dict[str, tuple[float, float]]] = None,
) -> dict[str, list[tuple[float, int, int]]]:
    """Return {task_id: [(score, v_idx, i_idx), ...]} sorted descending by score.

    Vectorises over all orders and their feasible pairs using numpy broadcast.
    ``resource_prices`` supplies resolved resource costs when vehicles declare
    non-fuel energy. ``fuel_price_eur_per_l`` remains the legacy fallback. The
    score weights default to the engine constants and are tunable via
    SolverParameters.

    With a travel network, repositioning seconds are the best (smallest) of
    three estimates: the straight-line hop from the vehicle's current position,
    the network shortest path from its home depot, and the hop onto the nearest
    network node to its current position plus that node's network path to the
    field. ``location_coords`` (location ref -> (lat, lon)) supplies the node
    coordinates for the nearest-node mapping; without it only the first two
    estimates apply. The pure straight-line estimate is always available, so a
    pair without any network path still scores.

    ``optimization_objective="time"`` switches warm-start scoring to estimated
    arrival-plus-service seconds so pre-allocation favors faster bundles. Cost
    mode remains the default.
    """
    fuel_price = (
        fuel_price_eur_per_l if fuel_price_eur_per_l is not None else FUEL_COST_EUR_PER_L
    )
    weight_margin = (
        score_weight_margin if score_weight_margin is not None else SCORE_WEIGHT_MARGIN
    )
    weight_reposition = (
        score_weight_reposition
        if score_weight_reposition is not None
        else SCORE_WEIGHT_REPOSITION
    )
    field_map = {f.location_id: f for f in fields}
    idx_to_vehicle = {idx: v for v in vehicles for idx in [vehicle_index[v.asset_id]]}
    idx_to_implement = {idx: im for im in implements for idx in [implement_index[im.asset_id]]}

    # Pre-compute vehicle current positions as arrays for batch distance calculation
    v_lats = np.array([float(v.lat) for v in vehicles])
    v_lons = np.array([float(v.lon) for v in vehicles])
    v_speeds = np.array([float(v.travel_speed) for v in vehicles])
    v_consumptions = np.array([vehicle_energy_consumption_rate(v) for v in vehicles])
    v_energy_prices = np.array([
        (
            resource_prices.price_for(vehicle_energy_resource_type(v))
            if resource_prices is not None
            else fuel_price
        )
        for v in vehicles
    ])
    # Fleet-level operating surcharge (driver labour plus machine wear) per
    # hour and toll per km, zero unless cost-rate data prices them.
    operating_eur_per_h = (
        resource_prices.operating_eur_per_h if resource_prices is not None else 0.0
    )
    toll_eur_per_km = (
        resource_prices.toll_eur_per_km if resource_prices is not None else 0.0
    )
    v_home_refs = [str(v.home_depot_ref or "") for v in vehicles]
    v_travel_modes = [travel_mode_for_vehicle(v) for v in vehicles]
    v_access = _vehicle_network_access(vehicles, travel_lookup, location_coords)
    v_access_refs = [a[0] if a else "" for a in v_access]
    v_access_approach_s = [a[1] if a else 0 for a in v_access]

    results: dict[str, list[tuple[float, int, int]]] = {}

    for order in orders:
        oid = order.task_id
        field = field_map.get(order.location_ref)
        if field is None:
            results[oid] = []
            continue

        f_lat = float(field.lat)
        f_lon = float(field.lon)

        pairs = feasible_pairs.get(oid, [])
        if not pairs:
            results[oid] = []
            continue

        v_indices = np.array([p[0] for p in pairs])
        i_indices = np.array([p[1] for p in pairs])

        # Vectorized haversine repositioning cost for all vehicles in pairs
        dist_km = haversine_km_vector(
            v_lats[v_indices], v_lons[v_indices], f_lat, f_lon
        )
        straight_line_s = dist_km / v_speeds[v_indices].clip(1) * 3600.0
        if travel_lookup:
            loc_ref = str(order.location_ref or "")
            home_net = np.array([
                _network_seconds_or_nan(
                    travel_lookup, home_ref, loc_ref, v_travel_modes[idx]
                )
                for idx, home_ref in enumerate(v_home_refs)
            ])
            node_net = np.array([
                v_access_approach_s[idx]
                + _network_seconds_or_nan(
                    travel_lookup, v_access_refs[idx], loc_ref, v_travel_modes[idx]
                )
                if v_access_refs[idx]
                else math.nan
                for idx in range(len(vehicles))
            ])
            candidates = np.vstack([
                straight_line_s,
                home_net[v_indices],
                node_net[v_indices],
            ])
            travel_s = np.nanmin(candidates, axis=0)
        else:
            travel_s = straight_line_s
        hours = travel_s / 3600.0
        reposition_cost = (
            hours
            * (v_consumptions[v_indices] * v_energy_prices[v_indices] + operating_eur_per_h)
            + dist_km * toll_eur_per_km
        )

        # Gross margin: per-order constant for all pairs
        gross_margins = np.full(len(pairs), _estimate_gross_margin(order))

        if str(optimization_objective or "").lower() == OBJECTIVE_MODE_TIME:
            service_seconds = np.array([
                _estimate_operation_seconds(order, implements[int(i_idx)])
                if 0 <= int(i_idx) < len(implements)
                else 0
                for i_idx in i_indices
            ])
            completion_seconds = hours * 3600.0 + service_seconds
            scores = -completion_seconds + gross_margins * 1.0e-6
        else:
            scores = (
                weight_margin * gross_margins
                - weight_reposition * reposition_cost
            )

        scored_pairs = sorted(
            zip(scores.tolist(), v_indices.tolist(), i_indices.tolist()),
            key=lambda x: -x[0],
        )
        results[oid] = scored_pairs

    return results


def _assignment_order_priority(item: tuple[str, list[tuple[float, int, int]]]) -> tuple:
    """Prioritize scarce, high-regret orders before flexible orders."""
    oid, candidates = item
    if not candidates:
        return (1, 0, 0.0, 0.0, oid)

    unique_implements = len({i_idx for _score, _v_idx, i_idx in candidates})
    best_score = candidates[0][0]
    second_score = candidates[1][0] if len(candidates) > 1 else -1.0e12
    regret = best_score - second_score
    return (0, unique_implements, -regret, -best_score, oid)


def greedy_assign(
    scored: dict[str, list[tuple[float, int, int]]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
) -> dict[str, tuple[int, int]]:
    """Greedy assignment: {task_id: (v_idx, i_idx)}.

    Orders with fewer implement alternatives and larger best-vs-second-best
    regret are assigned first. That keeps the warm start from spending a scarce
    implement on a flexible order before a constrained order has a chance to use
    it. Once an implement index is claimed, it is not reused.
    """
    claimed_implements: set[int] = set()
    assignment: dict[str, tuple[int, int]] = {}

    for oid, candidates in sorted(scored.items(), key=_assignment_order_priority):
        for _score, v_idx, i_idx in candidates:
            if i_idx not in claimed_implements:
                claimed_implements.add(i_idx)
                assignment[oid] = (v_idx, i_idx)
                break

    logger.debug(
        "Greedy assign: %d/%d orders assigned a V-I pair",
        len(assignment),
        len(scored),
    )
    return assignment

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
    EARTH_RADIUS_KM,
    FALLBACK_REVENUE_EUR_PER_HA,
    FUEL_COST_EUR_PER_L,
    SCORE_WEIGHT_MARGIN,
    SCORE_WEIGHT_REPOSITION,
)
from fl_op.solver.travel_time import TravelLookup, network_seconds, travel_mode_for_vehicle

logger = logging.getLogger(__name__)

# Assumed average field operation hours per hectare for margin estimation
_OPERATION_H_PER_HA = 1.0


def _haversine_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Haversine distance in km between two lat/lon points."""
    r = EARTH_RADIUS_KM
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


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


def _estimate_repositioning_cost(
    vehicle: Any,
    field: Any,
    fuel_price_eur_per_l: Optional[float] = None,
) -> float:
    """Diesel cost to drive from vehicle's current position to the field centroid."""
    fuel_price = (
        fuel_price_eur_per_l if fuel_price_eur_per_l is not None else FUEL_COST_EUR_PER_L
    )
    dist_km = _haversine_km(
        float(vehicle.lat),
        float(vehicle.lon),
        float(field.lat),
        float(field.lon),
    )
    speed_kmh = float(vehicle.travel_speed)
    hours = dist_km / speed_kmh if speed_kmh > 0 else 0
    fuel_l_per_h = float(vehicle.fuel_consumption_rate)
    return hours * fuel_l_per_h * fuel_price


def vectorized_score(
    orders: list[Any],
    vehicles: list[Any],
    implements: list[Any],
    fields: list[Any],
    feasible_pairs: dict[str, list[tuple[int, int]]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
    fuel_price_eur_per_l: Optional[float] = None,
    score_weight_margin: Optional[float] = None,
    score_weight_reposition: Optional[float] = None,
    travel_lookup: Optional[TravelLookup] = None,
) -> dict[str, list[tuple[float, int, int]]]:
    """Return {task_id: [(score, v_idx, i_idx), ...]} sorted descending by score.

    Vectorises over all orders and their feasible pairs using numpy broadcast.
    ``fuel_price_eur_per_l`` is the resolved cost-rate price; the engine
    constant applies when no rate is supplied. The score weights default to
    the engine constants and are tunable via SolverParameters.

    With a travel network, repositioning hours use the network shortest path
    from the vehicle's home depot (its road access point) to the field where
    one exists; the straight-line estimate from the vehicle's current
    position remains the fallback.
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
    v_consumptions = np.array([float(v.fuel_consumption_rate) for v in vehicles])
    v_home_refs = [str(v.home_depot_ref or "") for v in vehicles]
    v_travel_modes = [travel_mode_for_vehicle(v) for v in vehicles]

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
        lat1 = np.radians(v_lats[v_indices])
        lat2 = math.radians(f_lat)
        lon1 = np.radians(v_lons[v_indices])
        lon2 = math.radians(f_lon)
        dphi = lat2 - lat1
        dlambda = lon2 - lon1
        a = np.sin(dphi / 2) ** 2 + np.cos(lat1) * math.cos(lat2) * np.sin(dlambda / 2) ** 2
        dist_km = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a.clip(0, 1)))
        hours = dist_km / v_speeds[v_indices].clip(1)
        if travel_lookup:
            net_by_vehicle = np.array([
                _network_seconds_or_nan(
                    travel_lookup,
                    home_ref,
                    str(order.location_ref or ""),
                    v_travel_modes[idx],
                )
                for idx, home_ref in enumerate(v_home_refs)
            ])
            net_hours = net_by_vehicle[v_indices] / 3600.0
            hours = np.where(np.isnan(net_hours), hours, net_hours)
        reposition_cost = hours * v_consumptions[v_indices] * fuel_price

        # Gross margin: per-order constant for all pairs
        gross_margins = np.full(len(pairs), _estimate_gross_margin(order))

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

"""Synthetic generators for road travel links and resource prices."""

from datetime import datetime, timedelta
from typing import Any

import numpy as np

from fl_op.core.constants import (
    RATE_TYPE_FUEL,
    RATE_TYPE_LABOR,
    RATE_TYPE_MACHINE_WEAR,
    RATE_TYPE_MATERIAL,
    RATE_TYPE_TOLL,
)
from fl_op.core.geometry import haversine_km

# Road geometry: real road distance exceeds the geodesic line by a curvature
# factor, and convoy road speed differs from the engine's haversine default,
# so network travel times measurably diverge from the fallback estimate.
_ROAD_CURVATURE_FACTOR = 1.3
_ROAD_SPEED_KMH = 20.0
_ROAD_CLASSES = ("paved", "gravel", "dirt")

# Price levels the synthetic market draws around (EUR per unit).
_FUEL_PRICE_BASE_EUR_PER_L = 1.52
_MATERIAL_PRICE_BASE_EUR_PER_KG = 0.58
# Operating rates: driver labour and machine wear per operating hour, and the
# road toll per kilometre. They price driver time, wear, and tolls into the
# same cost-rate mechanism as fuel/material so they can change routing choices.
_LABOR_PRICE_BASE_EUR_PER_H = 24.0
_MACHINE_WEAR_PRICE_BASE_EUR_PER_H = 7.5
_TOLL_PRICE_BASE_EUR_PER_KM = 0.04
_PRICE_JITTER_LOW = 0.9
_PRICE_JITTER_HIGH = 1.15
# Validity horizon of the current price rows, and the age of the expired
# historical fuel row kept to exercise validity-window selection.
_PRICE_VALID_DAYS = 365
_PRICE_HISTORY_DAYS = 30


def _generate_routes(
    rng: np.random.Generator,
    depots: list[dict[str, Any]],
    fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate directed road links between each field and its nearest depot.

    The network is deliberately sparse (field-to-field pairs are absent), so
    the engine's haversine fallback stays exercised alongside network lookups.
    """
    depot_by_id = {d["depot_id"]: d for d in depots}
    routes: list[dict[str, Any]] = []
    rid = 0
    for field in fields:
        depot = depot_by_id.get(field["nearest_depot_id"])
        if depot is None:
            continue
        line_km = haversine_km(
            depot["lat"], depot["lon"], field["centroid_lat"], field["centroid_lon"]
        )
        road_km = round(line_km * _ROAD_CURVATURE_FACTOR, 2)
        travel_s = round(road_km / _ROAD_SPEED_KMH * 3600.0, 1)
        road_class = str(rng.choice(_ROAD_CLASSES))
        depot_point = [float(depot["lat"]), float(depot["lon"])]
        field_point = [float(field["centroid_lat"]), float(field["centroid_lon"])]
        for from_id, to_id, route_geometry in (
            (depot["depot_id"], field["field_id"], [depot_point, field_point]),
            (field["field_id"], depot["depot_id"], [field_point, depot_point]),
        ):
            routes.append(
                {
                    "route_id": f"route_{rid:07d}",
                    "from_id": from_id,
                    "to_id": to_id,
                    "travel_time_s": travel_s,
                    "distance_km": road_km,
                    "route_geometry": route_geometry,
                    "road_class": road_class,
                }
            )
            rid += 1
    return routes


def _generate_prices(
    rng: np.random.Generator,
    now: datetime,
) -> list[dict[str, Any]]:
    """Generate current resource and operating prices plus one expired fuel row.

    Covers the consumable rates (fuel, material) and the operating rates that
    extend the cost model (driver labour and machine wear per hour, toll per
    km); the expired fuel row exercises validity-window selection.
    """
    today = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    current_from = today - timedelta(days=1)
    current_to = today + timedelta(days=_PRICE_VALID_DAYS)
    history_from = today - timedelta(days=_PRICE_HISTORY_DAYS)

    def jitter(base: float) -> float:
        return round(base * float(rng.uniform(_PRICE_JITTER_LOW, _PRICE_JITTER_HIGH)), 4)

    return [
        {
            "price_id": "price_fuel_current",
            "resource_type": RATE_TYPE_FUEL,
            "price_eur": jitter(_FUEL_PRICE_BASE_EUR_PER_L),
            "per_unit": "L",
            "valid_from": current_from.isoformat(),
            "valid_to": current_to.isoformat(),
        },
        {
            "price_id": "price_fuel_previous",
            "resource_type": RATE_TYPE_FUEL,
            "price_eur": jitter(_FUEL_PRICE_BASE_EUR_PER_L),
            "per_unit": "L",
            "valid_from": history_from.isoformat(),
            "valid_to": current_from.isoformat(),
        },
        {
            "price_id": "price_material_current",
            "resource_type": RATE_TYPE_MATERIAL,
            "price_eur": jitter(_MATERIAL_PRICE_BASE_EUR_PER_KG),
            "per_unit": "kg",
            "valid_from": current_from.isoformat(),
            "valid_to": current_to.isoformat(),
        },
        {
            "price_id": "price_labor_current",
            "resource_type": RATE_TYPE_LABOR,
            "price_eur": jitter(_LABOR_PRICE_BASE_EUR_PER_H),
            "per_unit": "h",
            "valid_from": current_from.isoformat(),
            "valid_to": current_to.isoformat(),
        },
        {
            "price_id": "price_machine_wear_current",
            "resource_type": RATE_TYPE_MACHINE_WEAR,
            "price_eur": jitter(_MACHINE_WEAR_PRICE_BASE_EUR_PER_H),
            "per_unit": "h",
            "valid_from": current_from.isoformat(),
            "valid_to": current_to.isoformat(),
        },
        {
            "price_id": "price_toll_current",
            "resource_type": RATE_TYPE_TOLL,
            "price_eur": jitter(_TOLL_PRICE_BASE_EUR_PER_KM),
            "per_unit": "km",
            "valid_from": current_from.isoformat(),
            "valid_to": current_to.isoformat(),
        },
    ]

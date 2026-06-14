"""Synthetic generators for road travel links and resource prices."""

from datetime import datetime, timedelta
from typing import Any

import numpy as np

from fl_op.core.constants import RATE_TYPE_FUEL, RATE_TYPE_MATERIAL
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
        for from_id, to_id in (
            (depot["depot_id"], field["field_id"]),
            (field["field_id"], depot["depot_id"]),
        ):
            routes.append(
                {
                    "route_id": f"route_{rid:07d}",
                    "from_id": from_id,
                    "to_id": to_id,
                    "travel_time_s": travel_s,
                    "distance_km": road_km,
                    "road_class": road_class,
                }
            )
            rid += 1
    return routes


def _generate_prices(
    rng: np.random.Generator,
    now: datetime,
) -> list[dict[str, Any]]:
    """Generate current fuel/material prices plus one expired fuel row."""
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
    ]

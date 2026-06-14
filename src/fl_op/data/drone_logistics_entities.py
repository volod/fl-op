"""Synthetic generator for the drone-logistics domain pack."""

from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from fl_op.core.constants import (
    ARTIFACT_SCHEMA_VERSION,
    DEFAULT_DATA_FORMAT,
    RATE_TYPE_ELECTRICITY,
    RATE_TYPE_FUEL,
    RATE_TYPE_MATERIAL,
)
from fl_op.core.geometry import haversine_km
from fl_op.core.paths import DATA_ROOT
from fl_op.data.drone_logistics_tuning import (
    default_drone_logistics_tuning_path,
    load_drone_logistics_tuning,
)
from fl_op.io import get_codec

logger = logging.getLogger(__name__)

_TABULAR_DATASETS = [
    "ugvs",
    "uavs",
    "payload-modules",
    "drone-operators",
    "logistics-hubs",
    "delivery-locations",
    "restricted-zones",
    "delivery-orders",
    "travel-links",
    "prices",
]

_BASE_LAT = 50.45
_BASE_LON = 30.52
_KWH_PER_L_FUEL_EQUIV = 9.7

_SCENARIO_MANIFEST = "drone-scenarios.json"
_SCENARIO_EVENTS = "scenario-events.jsonl"
_REQUIRED_SCENARIOS = [
    "heavy_manufacturer_delivery",
    "urgent_restaurant_meal",
    "ordinary_online_store_parcel",
    "bad_weather_period",
    "no_fly_zone_activation",
    "road_only_destination",
    "uav_speed_win",
    "ugv_feasibility_win",
    "hub_energy_scarcity",
    "asset_outage_event",
]


def _point_near(
    rng: np.random.Generator,
    base_lat: float = _BASE_LAT,
    base_lon: float = _BASE_LON,
    spread_lat: float = 0.08,
    spread_lon: float = 0.12,
) -> tuple[float, float]:
    return (
        float(base_lat + rng.normal(0.0, spread_lat)),
        float(base_lon + rng.normal(0.0, spread_lon)),
    )


def _distance_km(a: dict[str, Any], b: dict[str, Any]) -> float:
    return haversine_km(
        float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"])
    )


def _polygon_around(lat: float, lon: float, radius_deg: float = 0.012) -> list[list[float]]:
    return [
        [lat - radius_deg, lon - radius_deg],
        [lat - radius_deg, lon + radius_deg],
        [lat + radius_deg, lon + radius_deg],
        [lat + radius_deg, lon - radius_deg],
    ]


def _generate_hubs(rng: np.random.Generator, n_hubs: int) -> list[dict[str, Any]]:
    hubs = []
    for i in range(max(1, n_hubs)):
        lat, lon = _point_near(rng, spread_lat=0.05, spread_lon=0.08)
        hubs.append(
            {
                "hub_id": f"hub_{i:03d}",
                "name": f"Drone logistics hub {i + 1}",
                "lat": lat,
                "lon": lon,
                "energy_units": float(rng.uniform(1200, 4000)),
                "battery_available_kwh": float(rng.uniform(9000, 32000)),
                "charging_power_kw": float(rng.uniform(320, 1500)),
            }
        )
    return hubs


def _apply_scenario_overrides(hubs: list[dict[str, Any]]) -> None:
    """Make default drone datasets carry deterministic scenario anchors."""
    if hubs:
        hubs[0]["energy_units"] = 45.0
        hubs[0]["battery_available_kwh"] = 420.0


def _payload_class_capacity(
    tuning: dict[str, Any],
    vehicle_class: str,
    index: int,
) -> float:
    classes = (
        (tuning.get("payloadCapacityClassesKg") or {}).get(vehicle_class) or {}
    )
    values = [float(value) for value in classes.values()]
    if not values:
        return 0.0
    return values[index % len(values)]


def _road_speed_bucket(
    tuning: dict[str, Any],
    customer_class: str,
) -> float:
    buckets = tuning.get("ugvRoadSpeedBucketsKmh") or {}
    key = {
        "restaurant": "denseUrban",
        "online_store": "arterial",
        "manufacturer": "industrial",
    }.get(customer_class, "arterial")
    return float(buckets.get(key, buckets.get("arterial", 24.0)))


def _delivery_penalty(
    tuning: dict[str, Any],
    customer_class: str,
) -> float:
    deadlines = tuning.get("deadlinePenaltyEurPerDayByCustomerClass") or {}
    drops = tuning.get("deliveryDropPenaltyMultiplierByCustomerClass") or {}
    return float(deadlines.get(customer_class, 700.0)) * float(
        drops.get(customer_class, 1.0)
    )


def _generate_ugvs(
    rng: np.random.Generator,
    n_ugv: int,
    hubs: list[dict[str, Any]],
    tuning: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    road_speeds = [
        float(value) for value in (tuning.get("ugvRoadSpeedBucketsKmh") or {}).values()
    ] or [26.0]
    for i in range(max(1, n_ugv)):
        hub = hubs[i % len(hubs)]
        battery_kwh = float(rng.uniform(650, 1750))
        use_kwh_per_h = float(rng.uniform(42, 92))
        rows.append(
            {
                "ugv_id": f"UGV_{i:04d}",
                "name": f"UGV cargo unit {i + 1}",
                "vehicle_class": "UGV",
                "rated_power_kw": float(rng.uniform(35, 90)),
                "energy_capacity_l_equiv": battery_kwh / _KWH_PER_L_FUEL_EQUIV,
                "energy_use_l_per_h": use_kwh_per_h / _KWH_PER_L_FUEL_EQUIV,
                "energy_resource_type": RATE_TYPE_ELECTRICITY,
                "energy_unit": "kWh",
                "battery_capacity_kwh": battery_kwh,
                "energy_use_kwh_per_h": use_kwh_per_h,
                "current_lat": float(hub["lat"]) + float(rng.normal(0, 0.004)),
                "current_lon": float(hub["lon"]) + float(rng.normal(0, 0.004)),
                "hub_id": hub["hub_id"],
                "travel_speed_kmh": road_speeds[i % len(road_speeds)],
                "payload_capacity_kg": _payload_class_capacity(tuning, "UGV", i),
                "compatible_operations": ["UGV_DELIVERY"],
            }
        )
    return rows


def _generate_uavs(
    rng: np.random.Generator,
    n_uav: int,
    hubs: list[dict[str, Any]],
    tuning: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for i in range(max(1, n_uav)):
        hub = hubs[i % len(hubs)]
        battery_kwh = float(rng.uniform(6, 24))
        use_kwh_per_h = float(rng.uniform(0.9, 3.2))
        rows.append(
            {
                "uav_id": f"UAV_{i:04d}",
                "name": f"UAV courier {i + 1}",
                "vehicle_class": "UAV",
                "rated_power_kw": float(rng.uniform(6, 18)),
                "energy_capacity_l_equiv": battery_kwh / _KWH_PER_L_FUEL_EQUIV,
                "energy_use_l_per_h": use_kwh_per_h / _KWH_PER_L_FUEL_EQUIV,
                "energy_resource_type": RATE_TYPE_ELECTRICITY,
                "energy_unit": "kWh",
                "battery_capacity_kwh": battery_kwh,
                "energy_use_kwh_per_h": use_kwh_per_h,
                "current_lat": float(hub["lat"]) + float(rng.normal(0, 0.002)),
                "current_lon": float(hub["lon"]) + float(rng.normal(0, 0.002)),
                "hub_id": hub["hub_id"],
                "travel_speed_kmh": float(rng.uniform(60, 95)),
                "payload_capacity_kg": _payload_class_capacity(tuning, "UAV", i),
                "compatible_operations": ["UAV_DELIVERY"],
            }
        )
    return rows


def _generate_modules(
    rng: np.random.Generator,
    n_modules: int,
    hubs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    n = max(2, n_modules)
    for i in range(n):
        is_uav = i % 3 == 0
        op = "UAV_DELIVERY" if is_uav else "UGV_DELIVERY"
        rows.append(
            {
                "module_id": f"{'UAV' if is_uav else 'UGV'}_PAYLOAD_{i:04d}",
                "module_class": "aerial_payload_box" if is_uav else "ground_cargo_box",
                "supported_operations": [op],
                "required_power_kw": float(rng.uniform(2, 5) if is_uav else rng.uniform(8, 24)),
                "min_speed_kmh": 0.0,
                "max_speed_kmh": float(rng.uniform(70, 100) if is_uav else rng.uniform(18, 35)),
                "hub_id": hubs[i % len(hubs)]["hub_id"],
                "work_rates": {"items": "6.0" if is_uav else "4.0"},
            }
        )
    return rows


def _generate_operators(n_ops: int, hubs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for i in range(max(2, n_ops)):
        rows.append(
            {
                "operator_id": f"drone_operator_{i:04d}",
                "full_name": f"Drone Operator {i + 1}",
                "shift_start_s": 5 * 3600,
                "shift_end_s": 23 * 3600,
                "certified_operations": ["UGV_DELIVERY", "UAV_DELIVERY"],
                "hub_id": hubs[i % len(hubs)]["hub_id"],
            }
        )
    return rows


def _generate_locations(
    rng: np.random.Generator,
    n_deliveries: int,
) -> list[dict[str, Any]]:
    classes = ("manufacturer", "restaurant", "online_store")
    rows = []
    for i in range(max(1, n_deliveries) * 2):
        lat, lon = _point_near(rng)
        rows.append(
            {
                "location_id": f"delivery_loc_{i:05d}",
                "name": f"{classes[i % len(classes)].replace('_', ' ').title()} point {i + 1}",
                "lat": lat,
                "lon": lon,
                "service_area_ha": 0.01,
                "polygon": "",
                "restricted_operations": [],
                "restriction_windows": [],
                "customer_class": classes[i % len(classes)],
            }
        )
    return rows


def _generate_restricted_zones(
    locations: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    no_fly_site = locations[3 if len(locations) > 3 else 0]
    road_curfew_site = locations[5 if len(locations) > 5 else -1]
    aerial_delivery_site = locations[9 if len(locations) > 9 else -1]
    curfew_start = now.replace(hour=8, minute=0, second=0, microsecond=0)
    curfew_end = curfew_start + timedelta(hours=2)
    return [
        {
            "zone_id": "no_fly_community_core",
            "name": "Community no-fly core",
            "lat": no_fly_site["lat"],
            "lon": no_fly_site["lon"],
            "polygon": _polygon_around(float(no_fly_site["lat"]), float(no_fly_site["lon"])),
            "restricted_operations": ["UAV_DELIVERY"],
            "restriction_windows": [],
        },
        {
            "zone_id": "morning_ground_curfew",
            "name": "Morning ground access curfew",
            "lat": road_curfew_site["lat"],
            "lon": road_curfew_site["lon"],
            "polygon": _polygon_around(float(road_curfew_site["lat"]), float(road_curfew_site["lon"]), 0.008),
            "restricted_operations": ["UGV_DELIVERY"],
            "restriction_windows": [f"{curfew_start.isoformat()}/{curfew_end.isoformat()}"],
        },
        {
            "zone_id": "pedestrian_aerial_delivery_zone",
            "name": "Pedestrian aerial delivery zone",
            "lat": aerial_delivery_site["lat"],
            "lon": aerial_delivery_site["lon"],
            "polygon": _polygon_around(
                float(aerial_delivery_site["lat"]),
                float(aerial_delivery_site["lon"]),
                0.006,
            ),
            "restricted_operations": ["UGV_DELIVERY"],
            "restriction_windows": [],
        },
    ]


def _generate_orders(
    rng: np.random.Generator,
    n_deliveries: int,
    locations: list[dict[str, Any]],
    now: datetime,
    tuning: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    classes = ("manufacturer", "restaurant", "online_store")
    for i in range(max(1, n_deliveries)):
        pickup = locations[(2 * i) % len(locations)]
        dropoff = locations[(2 * i + 1) % len(locations)]
        customer = classes[i % len(classes)]
        if customer == "manufacturer":
            payload = float(rng.uniform(35, 240))
            modes = ["UGV_DELIVERY"]
            deadline_h = rng.uniform(5, 10)
            revenue = rng.uniform(260, 850)
            priority = 4
        elif customer == "restaurant":
            payload = float(rng.uniform(0.8, 5.5))
            modes = (
                ["UGV_DELIVERY", "UAV_DELIVERY"]
                if i % 12 in (1, 4)
                else ["UAV_DELIVERY"]
            )
            deadline_h = rng.uniform(0.8, 2.2)
            revenue = rng.uniform(45, 150)
            priority = 1
        else:
            payload = float(rng.uniform(1.0, 14.0))
            if i % 12 == 8:
                payload = min(payload, 6.5)
            modes = (
                ["UGV_DELIVERY", "UAV_DELIVERY"]
                if payload <= 7.5 and i % 12 == 8
                else ["UGV_DELIVERY"]
            )
            deadline_h = rng.uniform(2.5, 7.0)
            revenue = rng.uniform(70, 260)
            priority = 2
        delivery_id = f"delivery_{i:05d}"
        for mode in modes:
            suffix = "UGV" if mode == "UGV_DELIVERY" else "UAV"
            rows.append(
                {
                    "task_id": f"{delivery_id}-{suffix}",
                    "delivery_id": delivery_id,
                    "pickup_location_id": pickup["location_id"],
                    "dropoff_location_id": dropoff["location_id"],
                    "operation_type": mode,
                    "customer_class": customer,
                    "work_quantity": 1.0,
                    "work_quantity_unit": "items",
                    "service_duration_minutes": 12.0 if mode == "UGV_DELIVERY" else 5.0,
                    "payload_kg": payload,
                    "payload_material": "parcel",
                    "deadline": (now + timedelta(hours=float(deadline_h))).isoformat(),
                    "penalty_per_day_eur": _delivery_penalty(tuning, customer),
                    "priority": priority,
                    "status": "pending",
                    "estimated_revenue_eur": float(revenue * (1.1 if mode == "UAV_DELIVERY" else 1.0)),
                }
            )
    return rows


def _nearest_hubs(
    location: dict[str, Any],
    hubs: list[dict[str, Any]],
    k: int = 3,
) -> list[dict[str, Any]]:
    return sorted(hubs, key=lambda hub: _distance_km(location, hub))[:max(1, min(k, len(hubs)))]


def _add_link(
    rows: list[dict[str, Any]],
    from_id: str,
    to_id: str,
    distance_km: float,
    mode: str,
    speed_kmh: float,
) -> None:
    seconds = max(45.0, distance_km / speed_kmh * 3600.0)
    rows.append(
        {
            "link_id": f"{mode}_{from_id}_{to_id}_{len(rows):06d}",
            "from_id": from_id,
            "to_id": to_id,
            "travel_time_s": float(seconds),
            "distance_km": float(distance_km),
            "network_mode": mode,
        }
    )


def _generate_travel_links(
    hubs: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    tuning: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    loc_map = {loc["location_id"]: loc for loc in locations}
    for loc in locations:
        for hub in _nearest_hubs(loc, hubs, k=3):
            km = _distance_km(loc, hub)
            road_km = km * 1.35
            road_speed = _road_speed_bucket(
                tuning, str(loc.get("customer_class", "online_store"))
            )
            for mode, speed in (("road", road_speed), ("air", 78.0)):
                dist = road_km if mode == "road" else km
                _add_link(rows, hub["hub_id"], loc["location_id"], dist, mode, speed)
                _add_link(rows, loc["location_id"], hub["hub_id"], dist, mode, speed)

    seen_pairs: set[tuple[str, str, str]] = set()
    for order in orders:
        pickup_id = str(order["pickup_location_id"])
        dropoff_id = str(order["dropoff_location_id"])
        pickup = loc_map[pickup_id]
        dropoff = loc_map[dropoff_id]
        km = _distance_km(pickup, dropoff)
        road_speed = min(
            _road_speed_bucket(tuning, str(pickup.get("customer_class", ""))),
            _road_speed_bucket(tuning, str(dropoff.get("customer_class", ""))),
        )
        for mode, speed in (("road", road_speed), ("air", 82.0)):
            for a, b in ((pickup_id, dropoff_id), (dropoff_id, pickup_id)):
                key = (a, b, mode)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                dist = km * 1.35 if mode == "road" else km
                _add_link(rows, a, b, dist, mode, speed)
    return rows


def _generate_weather(
    hubs: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    windows = []
    for i, hub in enumerate(hubs[: max(1, min(12, len(hubs)))]):
        good_from = now
        good_to = now + timedelta(hours=4)
        bad_from = good_to
        bad_to = bad_from + timedelta(hours=3)
        windows.extend(
            [
                {
                    "forecast_id": f"drone_weather_{i:03d}_good",
                    "valid_from": good_from.isoformat(),
                    "valid_to": good_to.isoformat(),
                    "wind_ms": 5.0 + i % 3,
                    "rain_mm_per_h": 0.2,
                    "soil_moisture_pct": 0.0,
                    "lat": hub["lat"],
                    "lon": hub["lon"],
                },
                {
                    "forecast_id": f"drone_weather_{i:03d}_bad",
                    "valid_from": bad_from.isoformat(),
                    "valid_to": bad_to.isoformat(),
                    "wind_ms": 14.0 + i % 4,
                    "rain_mm_per_h": 3.5,
                    "soil_moisture_pct": 0.0,
                    "lat": hub["lat"],
                    "lon": hub["lon"],
                },
            ]
        )
    return windows


def _generate_prices(now: datetime, tuning: dict[str, Any]) -> list[dict[str, Any]]:
    energy = tuning.get("energyCostRates") or {}
    return [
        {
            "rate_id": "drone_electricity_current",
            "rate_type": RATE_TYPE_ELECTRICITY,
            "unit_price_eur": float(energy.get("electricityEurPerKwh", 0.18)),
            "per_unit": "kWh",
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_to": (now + timedelta(days=7)).isoformat(),
        },
        {
            "rate_id": "drone_energy_fuel_equiv",
            "rate_type": RATE_TYPE_FUEL,
            "unit_price_eur": float(energy.get("fuelEquivalentEurPerL", 1.4)),
            "per_unit": "L",
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_to": (now + timedelta(days=7)).isoformat(),
        },
        {
            "rate_id": "parcel_material_placeholder",
            "rate_type": RATE_TYPE_MATERIAL,
            "unit_price_eur": 0.0,
            "per_unit": "kg",
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_to": (now + timedelta(days=7)).isoformat(),
        },
    ]


def _build_scenario_events(
    now: datetime,
    hubs: list[dict[str, Any]],
    ugvs: list[dict[str, Any]],
    uavs: list[dict[str, Any]],
    restricted_zones: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    tuning: dict[str, Any],
) -> list[dict[str, Any]]:
    """Event replay workload covering operational drone scenario triggers."""
    observed = now.isoformat()
    events: list[dict[str, Any]] = []
    if orders:
        started = orders[0]
        events.append(
            {
                "event_id": "drone-scenario-task-started",
                "event_type": "task.started",
                "observed_at": observed,
                "entity_ref": started["task_id"],
                "payload_json": "{}",
            }
        )
        cancellable = next(
            (
                order for order in orders
                if order["customer_class"] == "online_store"
                and order["operation_type"] == "UGV_DELIVERY"
            ),
            orders[-1],
        )
        events.append(
            {
                "event_id": "drone-scenario-customer-cancellation",
                "event_type": "order.cancelled",
                "observed_at": observed,
                "entity_ref": cancellable["task_id"],
                "payload_json": "{}",
            }
        )
    if len(locations) >= 2:
        urgent_task_id = "delivery_urgent_inserted-UAV"
        urgent_payload = {
            "task_id": urgent_task_id,
            "delivery_id": "delivery_urgent_inserted",
            "pickup_location_id": locations[0]["location_id"],
            "dropoff_location_id": locations[1]["location_id"],
            "operation_type": "UAV_DELIVERY",
            "customer_class": "restaurant",
            "work_quantity": 1.0,
            "work_quantity_unit": "items",
            "service_duration_minutes": 5.0,
            "payload_kg": 2.5,
            "payload_material": "meal",
            "deadline": (now + timedelta(minutes=45)).isoformat(),
            "penalty_per_day_eur": _delivery_penalty(tuning, "restaurant"),
            "priority": 1,
            "status": "pending",
            "estimated_revenue_eur": 180.0,
        }
        events.append(
            {
                "event_id": "drone-scenario-urgent-order-insertion",
                "event_type": "order.created",
                "observed_at": observed,
                "entity_ref": urgent_task_id,
                "payload_json": json.dumps(urgent_payload),
            }
        )
    if hubs:
        hub = hubs[0]
        events.append(
            {
                "event_id": "drone-scenario-energy-scarcity",
                "event_type": "inventory.adjusted",
                "observed_at": observed,
                "entity_ref": hub["hub_id"],
                "payload_json": json.dumps(
                    {
                        "hub_id": hub["hub_id"],
                        "battery_available_kwh": 140.0,
                        "energy_units": 15.0,
                    }
                ),
            }
        )
        events.append(
            {
                "event_id": "drone-scenario-weather-degradation",
                "event_type": "forecast.updated",
                "observed_at": observed,
                "entity_ref": hub["hub_id"],
                "payload_json": json.dumps(
                    {
                        "forecast_id": "drone_scenario_weather_severe",
                        "valid_from": observed,
                        "valid_to": (now + timedelta(hours=3)).isoformat(),
                        "wind_ms": 18.0,
                        "rain_mm_per_h": 5.0,
                        "soil_moisture_pct": 0.0,
                        "lat": hub["lat"],
                        "lon": hub["lon"],
                    }
                ),
            }
        )
    asset = (uavs or ugvs)[0] if (uavs or ugvs) else None
    if asset:
        asset_id = asset.get("uav_id") or asset.get("ugv_id")
        events.append(
            {
                "event_id": "drone-scenario-asset-outage",
                "event_type": "asset.unavailable",
                "observed_at": observed,
                "entity_ref": asset_id,
                "payload_json": "{}",
            }
        )
    no_fly = next(
        (zone for zone in restricted_zones if "no_fly" in str(zone.get("zone_id", ""))),
        None,
    )
    if no_fly is not None:
        events.append(
            {
                "event_id": "drone-scenario-no-fly-activation",
                "event_type": "entity.corrected",
                "observed_at": observed,
                "entity_ref": no_fly["zone_id"],
                "payload_json": json.dumps(no_fly),
            }
        )
    return events


def _write_jsonl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")


def _group_orders(orders: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for order in orders:
        groups.setdefault(str(order["delivery_id"]), []).append(order)
    return groups


def _scenario_manifest(
    hubs: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    restricted_zones: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    weather: list[dict[str, Any]],
    scenario_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Machine-readable scenario coverage for drone logistics datasets."""
    groups = _group_orders(orders)
    loc_by_id = {loc["location_id"]: loc for loc in locations}
    event_by_type = {event["event_type"]: event for event in scenario_events}

    def first_order(predicate) -> dict[str, Any] | None:
        return next((order for order in orders if predicate(order)), None)

    def first_group(predicate) -> list[dict[str, Any]]:
        return next((rows for rows in groups.values() if predicate(rows)), [])

    def refs_for_orders(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "delivery_id": rows[0]["delivery_id"] if rows else "",
            "task_ids": [row["task_id"] for row in rows],
            "dropoff_location_id": rows[0]["dropoff_location_id"] if rows else "",
            "customer_class": rows[0]["customer_class"] if rows else "",
        }

    heavy = first_order(
        lambda row: row["customer_class"] == "manufacturer"
        and float(row.get("payload_kg", 0.0)) >= 35.0
    )
    restaurant = first_order(lambda row: row["customer_class"] == "restaurant")
    online = first_order(lambda row: row["customer_class"] == "online_store")
    road_only = first_group(
        lambda rows: any(row["operation_type"] == "UGV_DELIVERY" for row in rows)
        and not any(row["operation_type"] == "UAV_DELIVERY" for row in rows)
    )
    uav_speed = first_group(
        lambda rows: len(rows) > 1
        and rows[0]["customer_class"] == "online_store"
        and any(row["operation_type"] == "UAV_DELIVERY" for row in rows)
        and any(row["operation_type"] == "UGV_DELIVERY" for row in rows)
    )
    no_fly_zone = next(
        (zone for zone in restricted_zones if "no_fly" in str(zone.get("zone_id", ""))),
        None,
    )
    no_fly_location_id = ""
    if no_fly_zone:
        no_fly_location = min(
            locations,
            key=lambda loc: (
                float(loc["lat"]) - float(no_fly_zone["lat"])
            ) ** 2
            + (float(loc["lon"]) - float(no_fly_zone["lon"])) ** 2,
        )
        no_fly_location_id = str(no_fly_location["location_id"])
    ugv_feasible = first_group(
        lambda rows: len(rows) > 1
        and any(row["operation_type"] == "UAV_DELIVERY" for row in rows)
        and any(row["operation_type"] == "UGV_DELIVERY" for row in rows)
        and rows[0]["dropoff_location_id"] == no_fly_location_id
    )

    scenarios: dict[str, dict[str, Any]] = {}

    def add(code: str, description: str, refs: dict[str, Any]) -> None:
        scenarios[code] = {
            "status": "covered" if any(refs.values()) else "missing",
            "description": description,
            "refs": refs,
        }

    add(
        "heavy_manufacturer_delivery",
        "Heavy manufacturer freight that requires UGV capacity.",
        {
            "task_id": heavy["task_id"] if heavy else "",
            "payload_kg": heavy["payload_kg"] if heavy else 0.0,
        },
    )
    add(
        "urgent_restaurant_meal",
        "Short-deadline restaurant delivery biased toward UAV service.",
        {
            "task_id": restaurant["task_id"] if restaurant else "",
            "deadline": restaurant["deadline"] if restaurant else "",
        },
    )
    add(
        "ordinary_online_store_parcel",
        "Ordinary online-store parcel demand.",
        {"task_id": online["task_id"] if online else ""},
    )
    add(
        "bad_weather_period",
        "Forecast windows that should block weather-sensitive UAV work.",
        {
            "forecast_ids": [
                row["forecast_id"]
                for row in weather
                if float(row.get("wind_ms", 0.0)) >= 12.0
                or float(row.get("rain_mm_per_h", 0.0)) >= 3.0
            ][:5]
        },
    )
    add(
        "no_fly_zone_activation",
        "Community no-fly restriction and activation event.",
        {
            "zone_id": no_fly_zone["zone_id"] if no_fly_zone else "",
            "event_id": event_by_type.get("entity.corrected", {}).get("event_id", ""),
        },
    )
    add(
        "road_only_destination",
        "Destination/order group with only UGV service variants.",
        refs_for_orders(road_only),
    )
    add(
        "uav_speed_win",
        "Dual-mode order where UAV should dominate on speed.",
        refs_for_orders(uav_speed),
    )
    add(
        "ugv_feasibility_win",
        "Dual-mode order where no-fly restriction makes UGV the feasible mode.",
        refs_for_orders(ugv_feasible),
    )
    scarce_hub = next(
        (hub for hub in hubs if float(hub.get("battery_available_kwh", 0.0)) <= 500.0),
        None,
    )
    add(
        "hub_energy_scarcity",
        "Hub with intentionally scarce battery energy.",
        {
            "hub_id": scarce_hub["hub_id"] if scarce_hub else "",
            "battery_available_kwh": (
                scarce_hub["battery_available_kwh"] if scarce_hub else 0.0
            ),
            "energy_units": scarce_hub["energy_units"] if scarce_hub else 0.0,
            "event_id": event_by_type.get("inventory.adjusted", {}).get("event_id", ""),
        },
    )
    add(
        "asset_outage_event",
        "Replay event removing one drone asset from service.",
        {
            "event_id": event_by_type.get("asset.unavailable", {}).get("event_id", ""),
            "asset_id": event_by_type.get("asset.unavailable", {}).get("entity_ref", ""),
        },
    )

    missing = [
        code for code in _REQUIRED_SCENARIOS
        if scenarios.get(code, {}).get("status") != "covered"
    ]
    return {
        "schema_version": "1.0",
        "domain": "drone_logistics",
        "required_scenarios": _REQUIRED_SCENARIOS,
        "coverage_complete": not missing,
        "missing_scenarios": missing,
        "scenarios": scenarios,
        "artifacts": {"events": _SCENARIO_EVENTS},
    }


def run_generate_drone_logistics(
    n_vehicles: int,
    n_modules: int,
    n_orders: int,
    n_hubs: int,
    seed: int | None,
    fmt: str = DEFAULT_DATA_FORMAT,
) -> pathlib.Path:
    """Generate a runnable drone-logistics dataset."""
    rng = np.random.default_rng(seed)
    now = datetime.now(tz=timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S")
    out_dir = DATA_ROOT / "generate-data" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Writing drone logistics dataset to %s (format: %s)", out_dir, fmt)

    tuning = load_drone_logistics_tuning()
    fleet = tuning.get("fleet") or {}
    ugv_share = min(0.95, max(0.05, float(fleet.get("ugvShare", 0.6))))
    n_vehicles = max(2, n_vehicles)
    n_ugv = max(1, int(round(n_vehicles * ugv_share)))
    n_uav = max(1, n_vehicles - n_ugv)
    hubs = _generate_hubs(rng, n_hubs)
    _apply_scenario_overrides(hubs)
    ugvs = _generate_ugvs(rng, n_ugv, hubs, tuning)
    uavs = _generate_uavs(rng, n_uav, hubs, tuning)
    modules = _generate_modules(rng, n_modules, hubs)
    operators = _generate_operators(max(n_vehicles, len(hubs) * 2), hubs)
    locations = _generate_locations(rng, n_orders)
    restricted_zones = _generate_restricted_zones(locations, now)
    orders = _generate_orders(rng, n_orders, locations, now, tuning)
    travel_links = _generate_travel_links(hubs, locations, orders, tuning)
    weather = _generate_weather(hubs, now)
    prices = _generate_prices(now, tuning)
    scenario_events = _build_scenario_events(
        now,
        hubs,
        ugvs,
        uavs,
        restricted_zones,
        orders,
        locations,
        tuning,
    )
    scenario_manifest = _scenario_manifest(
        hubs,
        locations,
        restricted_zones,
        orders,
        weather,
        scenario_events,
    )

    codec = get_codec(fmt)
    datasets = {
        "ugvs": ugvs,
        "uavs": uavs,
        "payload-modules": modules,
        "drone-operators": operators,
        "logistics-hubs": hubs,
        "delivery-locations": locations,
        "restricted-zones": restricted_zones,
        "delivery-orders": orders,
        "travel-links": travel_links,
        "prices": prices,
    }
    for name in _TABULAR_DATASETS:
        codec.write(datasets[name], out_dir / f"{name}{codec.extension}")
    (out_dir / "weather.json").write_text(json.dumps(weather, indent=2, default=str))
    (out_dir / _SCENARIO_MANIFEST).write_text(
        json.dumps(scenario_manifest, indent=2, default=str)
    )
    _write_jsonl(out_dir / _SCENARIO_EVENTS, scenario_events)

    metadata: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_metadata": {
            "timestamp": ts,
            "domain": "drone_logistics",
            "seed": seed,
            "data_format": fmt,
            "n_ugvs": len(ugvs),
            "n_uavs": len(uavs),
            "n_payload_modules": len(modules),
            "n_operators": len(operators),
            "n_hubs": len(hubs),
            "n_delivery_locations": len(locations),
            "n_delivery_order_variants": len(orders),
            "n_travel_links": len(travel_links),
            "n_weather_windows": len(weather),
            "scenario_manifest": _SCENARIO_MANIFEST,
            "scenario_events": _SCENARIO_EVENTS,
            "scenario_coverage_complete": scenario_manifest["coverage_complete"],
            "tuning": {
                "source": str(default_drone_logistics_tuning_path()),
                "schema_version": tuning.get("schemaVersion", ""),
                "ugv_share": ugv_share,
                "cluster_target_size": (
                    (tuning.get("solver") or {}).get("clusterTargetSize")
                ),
                "lns_time_limit_s": (tuning.get("solver") or {}).get("lnsTimeLimitS"),
                "rolling_instability_penalty": (
                    (tuning.get("solver") or {}).get("rollingInstabilityPenalty")
                ),
            },
        },
    }
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))
    logger.info(
        "Generated drone logistics: %d UGVs, %d UAVs, %d delivery variants, %d hubs -> %s",
        len(ugvs),
        len(uavs),
        len(orders),
        len(hubs),
        out_dir,
    )
    return out_dir


def generate_drone_logistics_domain(request: Any) -> pathlib.Path:
    """Registry adapter for the drone-logistics generator."""
    return run_generate_drone_logistics(
        n_vehicles=request.vehicles,
        n_modules=request.implements,
        n_orders=request.orders,
        n_hubs=request.depots,
        seed=request.seed,
        fmt=request.fmt,
    )

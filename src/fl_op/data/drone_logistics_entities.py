"""Synthetic generator for the drone-logistics domain pack."""

from __future__ import annotations

import json
import logging
import math
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION, DEFAULT_DATA_FORMAT
from fl_op.core.paths import DATA_ROOT
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
    lat1 = math.radians(float(a["lat"]))
    lat2 = math.radians(float(b["lat"]))
    dlat = lat2 - lat1
    dlon = math.radians(float(b["lon"]) - float(a["lon"]))
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(max(0.0, h)))


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
            }
        )
    return hubs


def _generate_ugvs(
    rng: np.random.Generator,
    n_ugv: int,
    hubs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for i in range(max(1, n_ugv)):
        hub = hubs[i % len(hubs)]
        rows.append(
            {
                "ugv_id": f"UGV_{i:04d}",
                "name": f"UGV cargo unit {i + 1}",
                "vehicle_class": "UGV",
                "rated_power_kw": float(rng.uniform(35, 90)),
                "energy_capacity_l_equiv": float(rng.uniform(65, 180)),
                "energy_use_l_per_h": float(rng.uniform(4.5, 9.5)),
                "current_lat": float(hub["lat"]) + float(rng.normal(0, 0.004)),
                "current_lon": float(hub["lon"]) + float(rng.normal(0, 0.004)),
                "hub_id": hub["hub_id"],
                "travel_speed_kmh": float(rng.uniform(18, 32)),
                "payload_capacity_kg": float(rng.uniform(120, 520)),
                "compatible_operations": ["UGV_DELIVERY"],
            }
        )
    return rows


def _generate_uavs(
    rng: np.random.Generator,
    n_uav: int,
    hubs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for i in range(max(1, n_uav)):
        hub = hubs[i % len(hubs)]
        rows.append(
            {
                "uav_id": f"UAV_{i:04d}",
                "name": f"UAV courier {i + 1}",
                "vehicle_class": "UAV",
                "rated_power_kw": float(rng.uniform(6, 18)),
                "energy_capacity_l_equiv": float(rng.uniform(8, 24)),
                "energy_use_l_per_h": float(rng.uniform(0.7, 1.8)),
                "current_lat": float(hub["lat"]) + float(rng.normal(0, 0.002)),
                "current_lon": float(hub["lon"]) + float(rng.normal(0, 0.002)),
                "hub_id": hub["hub_id"],
                "travel_speed_kmh": float(rng.uniform(60, 95)),
                "payload_capacity_kg": float(rng.uniform(3, 9)),
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
            modes = (
                ["UGV_DELIVERY", "UAV_DELIVERY"]
                if payload <= 7.5 and i % 12 == 10
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
                    "penalty_per_day_eur": float(2000 if customer == "restaurant" else 700),
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
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    loc_map = {loc["location_id"]: loc for loc in locations}
    for loc in locations:
        for hub in _nearest_hubs(loc, hubs, k=3):
            km = _distance_km(loc, hub)
            road_km = km * 1.35
            for mode, speed in (("road", 26.0), ("air", 78.0)):
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
        for mode, speed in (("road", 24.0), ("air", 82.0)):
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


def _generate_prices(now: datetime) -> list[dict[str, Any]]:
    return [
        {
            "rate_id": "drone_energy_fuel_equiv",
            "rate_type": "fuel",
            "unit_price_eur": 1.4,
            "per_unit": "L",
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_to": (now + timedelta(days=7)).isoformat(),
        },
        {
            "rate_id": "parcel_material_placeholder",
            "rate_type": "fertilizer",
            "unit_price_eur": 0.0,
            "per_unit": "kg",
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_to": (now + timedelta(days=7)).isoformat(),
        },
    ]


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

    n_vehicles = max(2, n_vehicles)
    n_ugv = max(1, int(round(n_vehicles * 0.6)))
    n_uav = max(1, n_vehicles - n_ugv)
    hubs = _generate_hubs(rng, n_hubs)
    ugvs = _generate_ugvs(rng, n_ugv, hubs)
    uavs = _generate_uavs(rng, n_uav, hubs)
    modules = _generate_modules(rng, n_modules, hubs)
    operators = _generate_operators(max(n_vehicles, len(hubs) * 2), hubs)
    locations = _generate_locations(rng, n_orders)
    restricted_zones = _generate_restricted_zones(locations, now)
    orders = _generate_orders(rng, n_orders, locations, now)
    travel_links = _generate_travel_links(hubs, locations, orders)
    weather = _generate_weather(hubs, now)
    prices = _generate_prices(now)

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

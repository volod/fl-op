"""Synthetic fleet dataset generator.

Generates vehicles, implements, operators, depots, fields, orders, contracts,
and weather windows. All sampling is NumPy-vectorised; geographic placement
uses scipy.spatial.BallTree with haversine metric.

Output directory: .data/generate-data/<ISO-timestamp>/
"""

import csv
import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sklearn.neighbors import BallTree

from fl_op.core.constants import (
    ARTIFACT_SCHEMA_VERSION,
    EARTH_RADIUS_KM,
    FUEL_COST_EUR_PER_L,
)
from fl_op.models.enums import (
    ImplementType,
    OperationType,
    OrderStatus,
    VehicleType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal constants for synthetic distributions
# ---------------------------------------------------------------------------

_REGION_CENTER_LAT = 48.5  # Central Ukraine approximate centroid
_REGION_CENTER_LON = 32.0
_REGION_RADIUS_KM = 400.0  # Bounding radius for all synthetic entities

_VEHICLE_POWER_MEAN_KW = 150.0
_VEHICLE_POWER_SIGMA_LOG = 0.3  # lognormal sigma for power
_FUEL_TANK_MEAN_L = 350.0
_FUEL_CONSUMPTION_MEAN_L_PER_H = 18.0

_IMPLEMENT_POWER_MEAN_KW = 100.0
_IMPLEMENT_POWER_SIGMA_LOG = 0.35

_ORDER_AREA_MIN_HA = 10.0
_ORDER_AREA_MAX_HA = 800.0
_ORDER_PENALTY_MIN_EUR_PER_DAY = 50.0
_ORDER_PENALTY_MAX_EUR_PER_DAY = 2000.0
_ORDER_REVENUE_MIN_EUR = 500.0
_ORDER_REVENUE_MAX_EUR = 40000.0
_ORDER_DEADLINE_DAYS_MIN = 3
_ORDER_DEADLINE_DAYS_MAX = 30

_CONTRACT_DURATION_DAYS_MIN = 30
_CONTRACT_DURATION_DAYS_MAX = 365

_IMPLEMENT_TYPES = list(ImplementType)
_VEHICLE_TYPES = list(VehicleType)
_OPERATION_TYPES = list(OperationType)

# Which implement types are compatible with which operation types
_IMPLEMENT_OPERATION_MAP: dict[ImplementType, list[OperationType]] = {
    ImplementType.SPRAYER: [OperationType.SPRAYING],
    ImplementType.PLOW: [OperationType.TILLAGE],
    ImplementType.DISK_HARROW: [OperationType.TILLAGE, OperationType.SEEDING],
    ImplementType.SEEDER: [OperationType.SEEDING],
    ImplementType.COMBINE_HEADER: [OperationType.HARVESTING],
    ImplementType.FERTILIZER_SPREADER: [OperationType.FERTILIZING],
}


# ---------------------------------------------------------------------------
# Geographic helpers
# ---------------------------------------------------------------------------


def _random_points_in_circle(
    rng: np.random.Generator,
    n: int,
    center_lat: float,
    center_lon: float,
    radius_km: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (lats, lons) arrays of n points uniformly sampled inside a circle.

    Uses BallTree haversine to stay on the sphere surface.
    """
    # Sample uniformly in a disk, then convert radial offset to lat/lon delta
    r = radius_km * np.sqrt(rng.uniform(0, 1, n))
    theta = rng.uniform(0, 2 * np.pi, n)
    # Approximate flat-earth offset in degrees (good for < 500 km)
    d_lat = np.degrees(r / EARTH_RADIUS_KM) * np.cos(theta)
    d_lon = np.degrees(r / EARTH_RADIUS_KM) * np.sin(theta) / np.cos(
        np.radians(center_lat)
    )
    return center_lat + d_lat, center_lon + d_lon


def _nearest_depot_ids(
    field_lats: np.ndarray,
    field_lons: np.ndarray,
    depot_lats: np.ndarray,
    depot_lons: np.ndarray,
    depot_ids: list[str],
) -> list[str]:
    """Return the nearest depot_id for each field centroid using haversine BallTree."""
    depot_coords = np.radians(np.column_stack([depot_lats, depot_lons]))
    field_coords = np.radians(np.column_stack([field_lats, field_lons]))
    tree = BallTree(depot_coords, metric="haversine")
    _, indices = tree.query(field_coords, k=1)
    return [depot_ids[idx[0]] for idx in indices]


# ---------------------------------------------------------------------------
# Entity generators
# ---------------------------------------------------------------------------


def _generate_depots(
    rng: np.random.Generator, n: int
) -> list[dict[str, Any]]:
    lats, lons = _random_points_in_circle(
        rng, n, _REGION_CENTER_LAT, _REGION_CENTER_LON, _REGION_RADIUS_KM
    )
    depots = []
    for i in range(n):
        depots.append(
            {
                "depot_id": f"depot_{i:04d}",
                "name": f"Depot {i:04d}",
                "lat": round(float(lats[i]), 6),
                "lon": round(float(lons[i]), 6),
                "fuel_available_l": round(float(rng.uniform(5000, 50000)), 1),
                "fertilizer_available_kg": round(float(rng.uniform(0, 20000)), 1),
            }
        )
    return depots


def _generate_vehicles(
    rng: np.random.Generator,
    n: int,
    depots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    depot_ids = [d["depot_id"] for d in depots]
    depot_lats = np.array([d["lat"] for d in depots])
    depot_lons = np.array([d["lon"] for d in depots])

    # Assign each vehicle to a depot uniformly at random
    assigned_depot_idxs = rng.integers(0, len(depots), size=n)
    # Small jitter around depot for current position
    jitter_km = 5.0
    jitter_lat = np.degrees(rng.uniform(-jitter_km, jitter_km, n) / EARTH_RADIUS_KM)
    jitter_lon = np.degrees(
        rng.uniform(-jitter_km, jitter_km, n) / EARTH_RADIUS_KM
    ) / np.cos(np.radians(depot_lats[assigned_depot_idxs]))

    vtypes = rng.choice([vt.value for vt in _VEHICLE_TYPES], size=n)
    powers = rng.lognormal(
        np.log(_VEHICLE_POWER_MEAN_KW), _VEHICLE_POWER_SIGMA_LOG, n
    ).clip(60, 500)
    tanks = rng.lognormal(np.log(_FUEL_TANK_MEAN_L), 0.25, n).clip(100, 1200)
    consumptions = rng.lognormal(np.log(_FUEL_CONSUMPTION_MEAN_L_PER_H), 0.2, n).clip(5, 60)
    speeds = rng.uniform(10, 25, n)

    vehicles = []
    for i in range(n):
        didx = int(assigned_depot_idxs[i])
        vehicles.append(
            {
                "vehicle_id": f"vehicle_{i:05d}",
                "vehicle_type": vtypes[i],
                "rated_power_kw": round(float(powers[i]), 1),
                "fuel_tank_l": round(float(tanks[i]), 1),
                "fuel_consumption_l_per_h": round(float(consumptions[i]), 2),
                "current_lat": round(float(depot_lats[didx] + jitter_lat[i]), 6),
                "current_lon": round(float(depot_lons[didx] + jitter_lon[i]), 6),
                "depot_id": depot_ids[didx],
                "travel_speed_kmh": round(float(speeds[i]), 1),
            }
        )
    return vehicles


def _generate_implements(
    rng: np.random.Generator,
    n: int,
    depots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    depot_ids = [d["depot_id"] for d in depots]
    assigned_depot_idxs = rng.integers(0, len(depots), size=n)

    itypes = rng.choice([it.value for it in _IMPLEMENT_TYPES], size=n)
    powers = rng.lognormal(
        np.log(_IMPLEMENT_POWER_MEAN_KW), _IMPLEMENT_POWER_SIGMA_LOG, n
    ).clip(30, 400)
    widths = rng.uniform(4, 36, n)
    min_speeds = rng.uniform(3, 7, n)
    max_speeds = min_speeds + rng.uniform(4, 10, n)
    fert_caps = np.where(
        np.isin(itypes, [ImplementType.FERTILIZER_SPREADER.value]),
        rng.uniform(500, 3000, n),
        0.0,
    )

    implements = []
    for i in range(n):
        itype = ImplementType(itypes[i])
        compat_ops = [op.value for op in _IMPLEMENT_OPERATION_MAP[itype]]
        implements.append(
            {
                "implement_id": f"implement_{i:06d}",
                "implement_type": itypes[i],
                "compatible_operations": compat_ops,
                "required_power_kw": round(float(powers[i]), 1),
                "working_width_m": round(float(widths[i]), 1),
                "min_speed_kmh": round(float(min_speeds[i]), 1),
                "max_speed_kmh": round(float(max_speeds[i]), 1),
                "fertilizer_capacity_kg": round(float(fert_caps[i]), 1),
                "depot_id": depot_ids[int(assigned_depot_idxs[i])],
            }
        )
    return implements


def _generate_operators(
    rng: np.random.Generator,
    n_vehicles: int,
    depots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One operator per vehicle (upper bound; real fleets may share operators)."""
    n = n_vehicles
    depot_ids = [d["depot_id"] for d in depots]
    assigned_depot_idxs = rng.integers(0, len(depots), size=n)
    # Shift start: 6-8 AM; shift end: shift_start + 8-10 hours
    shift_starts = rng.integers(6 * 3600, 8 * 3600, size=n)
    shift_lengths = rng.integers(8 * 3600, 10 * 3600, size=n)
    shift_ends = shift_starts + shift_lengths

    all_ops = [op.value for op in OperationType]
    operators = []
    for i in range(n):
        n_cert = rng.integers(1, len(all_ops) + 1)
        certified = rng.choice(all_ops, size=n_cert, replace=False).tolist()
        operators.append(
            {
                "operator_id": f"operator_{i:05d}",
                "name": f"Operator {i:05d}",
                "shift_start_s": int(shift_starts[i]),
                "shift_end_s": int(shift_ends[i]),
                "certified_operations": certified,
                "depot_id": depot_ids[int(assigned_depot_idxs[i])],
            }
        )
    return operators


def _generate_fields(
    rng: np.random.Generator,
    n_orders: int,
    depots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate one field per order for simplicity; real data may share fields."""
    n = n_orders
    depot_lats = np.array([d["lat"] for d in depots])
    depot_lons = np.array([d["lon"] for d in depots])
    depot_ids = [d["depot_id"] for d in depots]

    lats, lons = _random_points_in_circle(
        rng, n, _REGION_CENTER_LAT, _REGION_CENTER_LON, _REGION_RADIUS_KM
    )
    areas = rng.uniform(_ORDER_AREA_MIN_HA, _ORDER_AREA_MAX_HA, n)
    soil_types = rng.choice(["clay", "loam", "sandy_loam", "silt"], size=n)

    nearest = _nearest_depot_ids(
        lats, lons, depot_lats, depot_lons, depot_ids
    )

    fields = []
    for i in range(n):
        fields.append(
            {
                "field_id": f"field_{i:06d}",
                "name": f"Field {i:06d}",
                "area_ha": round(float(areas[i]), 2),
                "polygon": [],  # empty polygon; real import fills this
                "centroid_lat": round(float(lats[i]), 6),
                "centroid_lon": round(float(lons[i]), 6),
                "soil_type": str(soil_types[i]),
                "nearest_depot_id": nearest[i],
            }
        )
    return fields


def _generate_orders_and_contracts(
    rng: np.random.Generator,
    n_orders: int,
    fields: list[dict[str, Any]],
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    n = n_orders
    ops = rng.choice([op.value for op in OperationType], size=n)
    deadlines_days = rng.integers(_ORDER_DEADLINE_DAYS_MIN, _ORDER_DEADLINE_DAYS_MAX + 1, size=n)
    penalties = rng.uniform(_ORDER_PENALTY_MIN_EUR_PER_DAY, _ORDER_PENALTY_MAX_EUR_PER_DAY, n)
    revenues = rng.uniform(_ORDER_REVENUE_MIN_EUR, _ORDER_REVENUE_MAX_EUR, n)
    priorities = rng.integers(1, 11, size=n)

    # Group orders into contracts (roughly 5-20 orders per contract)
    contract_size = rng.integers(5, 21, size=n // 5 + 1)
    contracts: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []

    order_idx = 0
    contract_idx = 0
    while order_idx < n:
        c_size = int(min(contract_size[contract_idx % len(contract_size)], n - order_idx))
        c_id = f"contract_{contract_idx:05d}"
        c_start = now
        c_end_days = _CONTRACT_DURATION_DAYS_MIN + rng.integers(
            0, _CONTRACT_DURATION_DAYS_MAX - _CONTRACT_DURATION_DAYS_MIN
        )
        c_end = datetime(
            now.year,
            now.month,
            now.day,
            tzinfo=timezone.utc,
        )
        from datetime import timedelta

        c_end = c_end + timedelta(days=int(c_end_days))

        c_orders = []
        for j in range(c_size):
            oi = order_idx + j
            deadline = datetime(
                now.year, now.month, now.day, tzinfo=timezone.utc
            ) + timedelta(days=int(deadlines_days[oi]))
            o = {
                "order_id": f"order_{oi:06d}",
                "contract_id": c_id,
                "field_id": fields[oi]["field_id"],
                "operation_type": ops[oi],
                "area_ha": fields[oi]["area_ha"],
                "deadline": deadline.isoformat(),
                "penalty_per_day_eur": round(float(penalties[oi]), 2),
                "priority": int(priorities[oi]),
                "status": OrderStatus.PENDING.value,
                "estimated_revenue_eur": round(float(revenues[oi]), 2),
                "contract_id_ref": c_id,
            }
            c_orders.append(o["order_id"])
            orders.append(o)

        contracts.append(
            {
                "contract_id": c_id,
                "client_name": f"Client {contract_idx:05d}",
                "start_date": c_start.isoformat(),
                "end_date": c_end.isoformat(),
                "total_value_eur": round(float(revenues[order_idx : order_idx + c_size].sum()), 2),
                "default_penalty_per_day_eur": round(
                    float(penalties[order_idx : order_idx + c_size].mean()), 2
                ),
                "order_ids": c_orders,
            }
        )
        order_idx += c_size
        contract_idx += 1

    return orders, contracts


def _generate_weather(
    rng: np.random.Generator,
    depots: list[dict[str, Any]],
    now: datetime,
    n_days: int = 30,
) -> list[dict[str, Any]]:
    """Generate 6-hourly weather windows for each depot over n_days."""
    from datetime import timedelta

    windows: list[dict[str, Any]] = []
    wid = 0
    for depot in depots:
        for day in range(n_days):
            for hour in [0, 6, 12, 18]:
                valid_from = (
                    datetime(now.year, now.month, now.day, hour, tzinfo=timezone.utc)
                    + timedelta(days=day)
                )
                valid_to = valid_from + timedelta(hours=6)
                wind = float(rng.exponential(4.0))
                rain = float(rng.exponential(0.5))
                soil = float(rng.uniform(30, 90))
                windows.append(
                    {
                        "window_id": f"weather_{wid:08d}",
                        "valid_from": valid_from.isoformat(),
                        "valid_to": valid_to.isoformat(),
                        "wind_ms": round(wind, 2),
                        "rain_mm_per_h": round(rain, 2),
                        "soil_moisture_pct": round(soil, 1),
                        "lat": depot["lat"],
                        "lon": depot["lon"],
                    }
                )
                wid += 1
    return windows


# ---------------------------------------------------------------------------
# CSV / JSON writers
# ---------------------------------------------------------------------------


def _write_csv(records: list[dict[str, Any]], path: pathlib.Path) -> None:
    if not records:
        path.write_text("")
        return
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


def _write_json(obj: Any, path: pathlib.Path) -> None:
    with path.open("w") as fh:
        json.dump(obj, fh, indent=2, default=str)


# ---------------------------------------------------------------------------
# CSV import (--data-path)
# ---------------------------------------------------------------------------


def _load_csv_or_empty(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


def _merge_real_into_synthetic(
    real: list[dict[str, Any]],
    synthetic: list[dict[str, Any]],
    id_key: str,
) -> list[dict[str, Any]]:
    """Return real records merged with synthetic; real takes priority by id_key."""
    real_ids = {r[id_key] for r in real}
    filtered_synthetic = [s for s in synthetic if s[id_key] not in real_ids]
    return real + filtered_synthetic


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_generate(
    n_vehicles: int,
    n_implements: int,
    n_orders: int,
    n_depots: int,
    seed: int | None,
    data_path: str | None,
) -> None:
    import pathlib
    from pathlib import Path

    rng = np.random.default_rng(seed)
    now = datetime.now(tz=timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S")

    out_dir = Path(".data") / "generate-data" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Writing dataset to %s", out_dir)

    # Generate synthetic base
    depots = _generate_depots(rng, n_depots)
    vehicles = _generate_vehicles(rng, n_vehicles, depots)
    implements = _generate_implements(rng, n_implements, depots)
    operators = _generate_operators(rng, n_vehicles, depots)
    fields = _generate_fields(rng, n_orders, depots)
    orders, contracts = _generate_orders_and_contracts(rng, n_orders, fields, now)
    weather = _generate_weather(rng, depots, now)

    # Merge with real data if --data-path provided
    if data_path is not None:
        real_dir = pathlib.Path(data_path)
        logger.info("Merging real fleet data from %s", real_dir)
        real_vehicles = _load_csv_or_empty(real_dir / "vehicles.csv")
        real_implements = _load_csv_or_empty(real_dir / "implements.csv")
        real_orders = _load_csv_or_empty(real_dir / "orders.csv")
        real_depots = _load_csv_or_empty(real_dir / "depots.csv")

        if real_depots:
            depots = _merge_real_into_synthetic(real_depots, depots, "depot_id")
        if real_vehicles:
            vehicles = _merge_real_into_synthetic(real_vehicles, vehicles, "vehicle_id")
        if real_implements:
            implements = _merge_real_into_synthetic(
                real_implements, implements, "implement_id"
            )
        if real_orders:
            orders = _merge_real_into_synthetic(real_orders, orders, "order_id")

    # Write all entities as CSV
    _write_csv(depots, out_dir / "depots.csv")
    _write_csv(vehicles, out_dir / "vehicles.csv")
    _write_csv(implements, out_dir / "implements.csv")
    _write_csv(operators, out_dir / "operators.csv")
    _write_csv(fields, out_dir / "fields.csv")
    _write_csv(orders, out_dir / "orders.csv")

    # Write contracts and weather as JSON (nested structure)
    _write_json(contracts, out_dir / "contracts.json")
    _write_json(weather, out_dir / "weather.json")

    # Write run metadata
    metadata = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_metadata": {
            "timestamp": ts,
            "seed": seed,
            "n_vehicles": len(vehicles),
            "n_implements": len(implements),
            "n_orders": len(orders),
            "n_depots": len(depots),
            "n_operators": len(operators),
            "n_fields": len(fields),
            "n_contracts": len(contracts),
            "data_path": data_path,
        },
    }
    _write_json(metadata, out_dir / "metadata.json")

    logger.info(
        "Generated: %d vehicles, %d implements, %d orders, %d depots -> %s",
        len(vehicles),
        len(implements),
        len(orders),
        len(depots),
        out_dir,
    )

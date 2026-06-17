"""Synthetic generators for fields, orders, contracts, and weather windows."""

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from fl_op.canonical.enums import TaskStatus
from fl_op.data.agri_enums import OperationType
from fl_op.data.geo import _REGION_CENTER_LAT, _REGION_CENTER_LON, _REGION_RADIUS_KM, _nearest_depot_ids, _random_points_in_circle

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

_CONTRACT_ORDERS_MIN = 5
_CONTRACT_ORDERS_MAX = 20

# Share of orders that form a two-stage sequence with the preceding order of
# their contract (multi-stage work on the same field, e.g. tillage -> seeding).
_ORDER_CHAIN_SHARE = 0.15
# Days added to a dependent order's deadline so the sequence stays feasible.
_ORDER_CHAIN_DEADLINE_LAG_DAYS = 3

# Share of (non-chained) orders carrying explicit workable time windows.
_ORDER_WINDOWED_SHARE = 0.2

# Mass of delivered material per worked hectare for load-demanding operations.
_ORDER_LOAD_KG_PER_HA = 4.0
# Operations whose orders carry a material load to the field.
_LOAD_DEMANDING_OPERATIONS = (
    OperationType.FERTILIZING.value,
    OperationType.SEEDING.value,
)

# Share of fields declaring a restricted zone (one prohibited operation type)
# and a time-restricted window, respectively.
_FIELD_RESTRICTED_OP_SHARE = 0.05
_FIELD_RESTRICTED_WINDOW_SHARE = 0.1
# Placement of the restriction window: starts this many days out, lasts this
# long. Short relative to order deadlines so most restricted fields stay
# schedulable around the window.
_FIELD_RESTRICTION_START_DAYS = 1.0
_FIELD_RESTRICTION_LENGTH_DAYS = 2.0
# How many windows a windowed order declares.
_ORDER_WINDOWS_MIN = 1
_ORDER_WINDOWS_MAX = 2
# Window placement as fractions of the order's deadline horizon, and the share
# of each window slot that is actually workable (the rest is the gap).
_ORDER_WINDOW_START_FRACTION = 0.1
_ORDER_WINDOW_END_FRACTION = 0.6
_ORDER_WINDOW_FILL = 0.8

_WEATHER_WINDOW_HOURS = 6
_WEATHER_FORECAST_DAYS = 30
_WEATHER_WIND_MEAN_MS = 4.0
_WEATHER_RAIN_MEAN_MM_PER_H = 0.5


def _generate_fields(
    rng: np.random.Generator,
    n_orders: int,
    depots: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Generate one field per order; real data may share fields."""
    n = n_orders
    depot_lats = np.array([d["lat"] for d in depots])
    depot_lons = np.array([d["lon"] for d in depots])
    depot_ids = [d["depot_id"] for d in depots]

    lats, lons = _random_points_in_circle(
        rng, n, _REGION_CENTER_LAT, _REGION_CENTER_LON, _REGION_RADIUS_KM
    )
    areas = rng.uniform(_ORDER_AREA_MIN_HA, _ORDER_AREA_MAX_HA, n)
    soil_types = rng.choice(["clay", "loam", "sandy_loam", "silt"], size=n)
    restriction_draws = rng.uniform(0.0, 1.0, n)
    window_draws = rng.uniform(0.0, 1.0, n)
    prohibited_ops = rng.choice([op.value for op in OperationType], size=n)

    nearest = _nearest_depot_ids(lats, lons, depot_lats, depot_lons, depot_ids)

    fields = []
    for i in range(n):
        fields.append(
            {
                "field_id": f"field_{i:06d}",
                "name": f"Field {i:06d}",
                "area_ha": round(float(areas[i]), 2),
                "polygon": [],
                "centroid_lat": round(float(lats[i]), 6),
                "centroid_lon": round(float(lons[i]), 6),
                "soil_type": str(soil_types[i]),
                "nearest_depot_id": nearest[i],
                "restricted_operations": (
                    str([str(prohibited_ops[i])])
                    if restriction_draws[i] < _FIELD_RESTRICTED_OP_SHARE
                    else "[]"
                ),
                "restricted_windows": (
                    _restriction_window(now)
                    if window_draws[i] < _FIELD_RESTRICTED_WINDOW_SHARE
                    else "[]"
                ),
            }
        )
    return fields


def _restriction_window(now: datetime) -> str:
    """One stringified time-restricted interval in the near planning horizon."""
    today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    start = today + timedelta(days=_FIELD_RESTRICTION_START_DAYS)
    end = start + timedelta(days=_FIELD_RESTRICTION_LENGTH_DAYS)
    return str([f"{start.isoformat()}/{end.isoformat()}"])


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

    contract_size = rng.integers(_CONTRACT_ORDERS_MIN, _CONTRACT_ORDERS_MAX + 1, size=n // _CONTRACT_ORDERS_MIN + 1)
    chain_draws = rng.uniform(0.0, 1.0, n)
    window_draws = rng.uniform(0.0, 1.0, n)
    window_counts = rng.integers(_ORDER_WINDOWS_MIN, _ORDER_WINDOWS_MAX + 1, size=n)
    contracts: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []

    order_idx = 0
    contract_idx = 0
    today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    while order_idx < n:
        c_size = int(min(contract_size[contract_idx % len(contract_size)], n - order_idx))
        c_id = f"contract_{contract_idx:05d}"
        c_end_days = _CONTRACT_DURATION_DAYS_MIN + int(
            rng.integers(0, _CONTRACT_DURATION_DAYS_MAX - _CONTRACT_DURATION_DAYS_MIN)
        )
        c_end = today + timedelta(days=c_end_days)

        c_orders = []
        for j in range(c_size):
            oi = order_idx + j
            deadline = today + timedelta(days=int(deadlines_days[oi]))
            o = {
                "order_id": f"order_{oi:06d}",
                "contract_id": c_id,
                "field_id": fields[oi]["field_id"],
                "operation_type": ops[oi],
                "area_ha": fields[oi]["area_ha"],
                "deadline": deadline.isoformat(),
                "penalty_per_day_eur": round(float(penalties[oi]), 2),
                "priority": int(priorities[oi]),
                "status": TaskStatus.PENDING.value,
                "estimated_revenue_eur": round(float(revenues[oi]), 2),
                "contract_id_ref": c_id,
                "depends_on_order_id": "",
                "workable_windows": "[]",
                "service_duration_minutes": 0.0,
                "material_load_kg": (
                    round(float(fields[oi]["area_ha"]) * _ORDER_LOAD_KG_PER_HA, 1)
                    if ops[oi] in _LOAD_DEMANDING_OPERATIONS
                    else 0.0
                ),
                # A share of material-carrying orders pick the load up at the
                # field's nearest yard first (paired pickup-and-delivery); the
                # rest carry the load from the cluster depot (empty ref).
                "pickup_location_ref": (
                    fields[oi]["nearest_depot_id"]
                    if ops[oi] in _LOAD_DEMANDING_OPERATIONS and oi % 5 == 0
                    else ""
                ),
            }
            if j > 0 and chain_draws[oi] < _ORDER_CHAIN_SHARE:
                _chain_to_predecessor(o, orders[-1])
            elif window_draws[oi] < _ORDER_WINDOWED_SHARE:
                o["workable_windows"] = _workable_windows(
                    today, int(deadlines_days[oi]), int(window_counts[oi])
                )
            c_orders.append(o["order_id"])
            orders.append(o)

        contracts.append(
            {
                "contract_id": c_id,
                "client_name": f"Client {contract_idx:05d}",
                "start_date": now.isoformat(),
                "end_date": c_end.isoformat(),
                "total_value_eur": round(float(revenues[order_idx: order_idx + c_size].sum()), 2),
                "default_penalty_per_day_eur": round(
                    float(penalties[order_idx: order_idx + c_size].mean()), 2
                ),
                "order_ids": c_orders,
            }
        )
        order_idx += c_size
        contract_idx += 1

    return orders, contracts


def _chain_to_predecessor(order: dict[str, Any], predecessor: dict[str, Any]) -> None:
    """Turn an order into the second stage of its predecessor's field work."""
    order["depends_on_order_id"] = predecessor["order_id"]
    order["field_id"] = predecessor["field_id"]
    order["area_ha"] = predecessor["area_ha"]
    pred_deadline = datetime.fromisoformat(predecessor["deadline"])
    order["deadline"] = (
        pred_deadline + timedelta(days=_ORDER_CHAIN_DEADLINE_LAG_DAYS)
    ).isoformat()


def _workable_windows(today: datetime, deadline_days: int, n_windows: int) -> str:
    """Build a stringified list of ISO-8601 "from/to" windows inside the deadline."""
    horizon = max(1.0, float(deadline_days))
    slot = (_ORDER_WINDOW_END_FRACTION - _ORDER_WINDOW_START_FRACTION) / n_windows
    windows = []
    for k in range(n_windows):
        start_fraction = _ORDER_WINDOW_START_FRACTION + k * slot
        start = today + timedelta(days=horizon * start_fraction)
        end = today + timedelta(days=horizon * (start_fraction + slot * _ORDER_WINDOW_FILL))
        windows.append(f"{start.isoformat()}/{end.isoformat()}")
    return str(windows)


def _generate_weather(
    rng: np.random.Generator,
    depots: list[dict[str, Any]],
    now: datetime,
    n_days: int = _WEATHER_FORECAST_DAYS,
) -> list[dict[str, Any]]:
    """Generate 6-hourly weather windows for each depot over n_days."""
    windows: list[dict[str, Any]] = []
    wid = 0
    for depot in depots:
        for day in range(n_days):
            for hour in range(0, 24, _WEATHER_WINDOW_HOURS):
                valid_from = (
                    datetime(now.year, now.month, now.day, hour, tzinfo=timezone.utc)
                    + timedelta(days=day)
                )
                valid_to = valid_from + timedelta(hours=_WEATHER_WINDOW_HOURS)
                windows.append(
                    {
                        "window_id": f"weather_{wid:08d}",
                        "valid_from": valid_from.isoformat(),
                        "valid_to": valid_to.isoformat(),
                        "wind_ms": round(float(rng.exponential(_WEATHER_WIND_MEAN_MS)), 2),
                        "rain_mm_per_h": round(float(rng.exponential(_WEATHER_RAIN_MEAN_MM_PER_H)), 2),
                        "soil_moisture_pct": round(float(rng.uniform(30, 90)), 1),
                        "lat": depot["lat"],
                        "lon": depot["lon"],
                    }
                )
                wid += 1
    return windows

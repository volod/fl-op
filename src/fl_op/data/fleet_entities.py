"""Synthetic generators for fleet entities: depots, vehicles, implements, operators."""

from typing import Any

import numpy as np

from fl_op.core.constants import EARTH_RADIUS_KM
from fl_op.data.agri_enums import ImplementType, OperationType, VehicleType
from fl_op.data.geo import _REGION_CENTER_LAT, _REGION_CENTER_LON, _REGION_RADIUS_KM, _random_points_in_circle

_VEHICLE_POWER_MEAN_KW = 150.0
_VEHICLE_POWER_SIGMA_LOG = 0.3
_FUEL_TANK_MEAN_L = 350.0
_FUEL_CONSUMPTION_MEAN_L_PER_H = 18.0

_IMPLEMENT_POWER_MEAN_KW = 100.0
_IMPLEMENT_POWER_SIGMA_LOG = 0.35

_VEHICLE_POSITION_JITTER_KM = 5.0

_IMPLEMENT_TYPES = list(ImplementType)
_VEHICLE_TYPES = list(VehicleType)

_IMPLEMENT_OPERATION_MAP: dict[ImplementType, list[OperationType]] = {
    ImplementType.SPRAYER: [OperationType.SPRAYING],
    ImplementType.PLOW: [OperationType.TILLAGE],
    ImplementType.DISK_HARROW: [OperationType.TILLAGE, OperationType.SEEDING],
    ImplementType.SEEDER: [OperationType.SEEDING],
    ImplementType.COMBINE_HEADER: [OperationType.HARVESTING],
    ImplementType.FERTILIZER_SPREADER: [OperationType.FERTILIZING],
}


def _generate_depots(rng: np.random.Generator, n: int) -> list[dict[str, Any]]:
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

    assigned_depot_idxs = rng.integers(0, len(depots), size=n)
    jitter_lat = np.degrees(
        rng.uniform(-_VEHICLE_POSITION_JITTER_KM, _VEHICLE_POSITION_JITTER_KM, n) / EARTH_RADIUS_KM
    )
    jitter_lon = np.degrees(
        rng.uniform(-_VEHICLE_POSITION_JITTER_KM, _VEHICLE_POSITION_JITTER_KM, n) / EARTH_RADIUS_KM
    ) / np.cos(np.radians(depot_lats[assigned_depot_idxs]))

    vtypes = rng.choice([vt.value for vt in _VEHICLE_TYPES], size=n)
    powers = rng.lognormal(np.log(_VEHICLE_POWER_MEAN_KW), _VEHICLE_POWER_SIGMA_LOG, n).clip(60, 500)
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
                # Extra real-data fields retained for analysis, not used by the
                # optimizer (no canonical mapping). Derived deterministically so
                # they do not perturb the optimization fields' values.
                "manufacture_year": 2010 + (i % 15),
                "telematics_unit_id": f"TEL-{i:05d}",
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
    powers = rng.lognormal(np.log(_IMPLEMENT_POWER_MEAN_KW), _IMPLEMENT_POWER_SIGMA_LOG, n).clip(30, 400)
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

    _SHIFT_START_MIN_S = 6 * 3600
    _SHIFT_START_MAX_S = 8 * 3600
    _SHIFT_LENGTH_MIN_S = 8 * 3600
    _SHIFT_LENGTH_MAX_S = 10 * 3600

    shift_starts = rng.integers(_SHIFT_START_MIN_S, _SHIFT_START_MAX_S, size=n)
    shift_lengths = rng.integers(_SHIFT_LENGTH_MIN_S, _SHIFT_LENGTH_MAX_S, size=n)
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

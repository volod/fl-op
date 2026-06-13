"""Synthetic generators for construction-earthworks entities.

Produces datasets conforming to the construction domain pack's physical ODCS
schemas (contracts/domains/construction/odcs): machines, attachments,
operators, yards, sites, and jobs. The vocabulary is physical (machine
classes, work types); the mapping pack projects it onto the canonical model.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from fl_op.canonical.enums import TaskStatus
from fl_op.data.geo import (
    _REGION_CENTER_LAT,
    _REGION_CENTER_LON,
    _REGION_RADIUS_KM,
    _random_points_in_circle,
)

# Physical work-type vocabulary; attachments declare which they support and
# jobs demand exactly one.
_WORK_TYPES = ("EXCAVATION", "GRADING", "TRENCHING", "COMPACTION", "HAULING")

_MACHINE_CLASSES = ("excavator", "wheel_loader", "dozer", "grader", "compactor")

# Attachment class -> supported work types.
_ATTACHMENT_OPERATION_MAP: dict[str, list[str]] = {
    "bucket": ["EXCAVATION"],
    "blade": ["GRADING"],
    "ripper": ["EXCAVATION", "TRENCHING"],
    "trench_cutter": ["TRENCHING"],
    "drum_roller": ["COMPACTION"],
    "dump_bed": ["HAULING"],
}

# Work types whose native demand is excavated/moved volume (m3); the others
# are area-shaped (ha) and keep the plot area as their quantity.
_VOLUME_WORK_TYPES = ("EXCAVATION", "TRENCHING", "HAULING")
_QUANTITY_UNIT_VOLUME = "m3"
_QUANTITY_UNIT_AREA = "ha"

# Native volume demand per job (m3) for volume-shaped work types.
_JOB_QUANTITY_MIN_M3 = 100.0
_JOB_QUANTITY_MAX_M3 = 5000.0

# Attachment classes that move volume declare an m3-per-hour work rate;
# area-shaped classes rely on the width-times-speed coverage model instead.
_VOLUME_RATE_ATTACHMENT_CLASSES = ("bucket", "ripper", "trench_cutter", "dump_bed")
_WORK_RATE_MIN_M3_PER_H = 40.0
_WORK_RATE_MAX_M3_PER_H = 220.0

_MACHINE_POWER_MEAN_KW = 180.0
_MACHINE_POWER_SIGMA_LOG = 0.35
_MACHINE_TANK_MEAN_L = 400.0
_MACHINE_BURN_MEAN_L_PER_H = 22.0
_MACHINE_POSITION_JITTER_KM = 3.0

_ATTACHMENT_POWER_MEAN_KW = 110.0
_ATTACHMENT_POWER_SIGMA_LOG = 0.35

_SITE_PLOT_MIN_HA = 0.5
_SITE_PLOT_MAX_HA = 40.0
_GROUND_CLASSES = ("rock", "gravel", "clay", "sand")

_JOB_DEADLINE_DAYS_MIN = 3
_JOB_DEADLINE_DAYS_MAX = 30
_JOB_PENALTY_MIN_EUR_PER_DAY = 100.0
_JOB_PENALTY_MAX_EUR_PER_DAY = 3000.0
_JOB_REVENUE_MIN_EUR = 1000.0
_JOB_REVENUE_MAX_EUR = 60000.0

_SHIFT_START_MIN_S = 6 * 3600
_SHIFT_START_MAX_S = 8 * 3600
_SHIFT_LENGTH_MIN_S = 8 * 3600
_SHIFT_LENGTH_MAX_S = 11 * 3600


def _generate_yards(rng: np.random.Generator, n: int) -> list[dict[str, Any]]:
    lats, lons = _random_points_in_circle(
        rng, n, _REGION_CENTER_LAT, _REGION_CENTER_LON, _REGION_RADIUS_KM
    )
    return [
        {
            "yard_id": f"yard_{i:04d}",
            "label": f"Equipment Yard {i:04d}",
            "lat": round(float(lats[i]), 6),
            "lon": round(float(lons[i]), 6),
            "diesel_litres": round(float(rng.uniform(5000, 60000)), 1),
        }
        for i in range(n)
    ]


def _generate_machines(
    rng: np.random.Generator,
    n: int,
    yards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    yard_ids = [y["yard_id"] for y in yards]
    yard_lats = np.array([y["lat"] for y in yards])
    yard_lons = np.array([y["lon"] for y in yards])
    assigned = rng.integers(0, len(yards), size=n)
    from fl_op.core.constants import EARTH_RADIUS_KM

    jitter_lat = np.degrees(
        rng.uniform(-_MACHINE_POSITION_JITTER_KM, _MACHINE_POSITION_JITTER_KM, n)
        / EARTH_RADIUS_KM
    )
    jitter_lon = np.degrees(
        rng.uniform(-_MACHINE_POSITION_JITTER_KM, _MACHINE_POSITION_JITTER_KM, n)
        / EARTH_RADIUS_KM
    ) / np.cos(np.radians(yard_lats[assigned]))

    classes = rng.choice(_MACHINE_CLASSES, size=n)
    powers = rng.lognormal(np.log(_MACHINE_POWER_MEAN_KW), _MACHINE_POWER_SIGMA_LOG, n).clip(60, 600)
    tanks = rng.lognormal(np.log(_MACHINE_TANK_MEAN_L), 0.25, n).clip(120, 1500)
    burns = rng.lognormal(np.log(_MACHINE_BURN_MEAN_L_PER_H), 0.2, n).clip(8, 80)
    speeds = rng.uniform(8, 40, n)

    machines = []
    for i in range(n):
        yidx = int(assigned[i])
        machines.append(
            {
                "machine_id": f"machine_{i:05d}",
                "machine_class": str(classes[i]),
                "engine_power_kw": round(float(powers[i]), 1),
                "tank_litres": round(float(tanks[i]), 1),
                "burn_l_per_h": round(float(burns[i]), 2),
                "current_lat": round(float(yard_lats[yidx] + jitter_lat[i]), 6),
                "current_lon": round(float(yard_lons[yidx] + jitter_lon[i]), 6),
                "yard_id": yard_ids[yidx],
                "transit_speed_kmh": round(float(speeds[i]), 1),
                # Analytical only; no canonical mapping.
                "telematics_hours": round(float(rng.uniform(500, 20000)), 1),
            }
        )
    return machines


def _generate_attachments(
    rng: np.random.Generator,
    n: int,
    yards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    yard_ids = [y["yard_id"] for y in yards]
    assigned = rng.integers(0, len(yards), size=n)
    classes = rng.choice(list(_ATTACHMENT_OPERATION_MAP), size=n)
    powers = rng.lognormal(
        np.log(_ATTACHMENT_POWER_MEAN_KW), _ATTACHMENT_POWER_SIGMA_LOG, n
    ).clip(20, 450)
    widths = rng.uniform(0.6, 6.0, n)
    min_speeds = rng.uniform(1.0, 3.0, n)
    max_speeds = min_speeds + rng.uniform(2.0, 8.0, n)

    volume_rates = rng.uniform(_WORK_RATE_MIN_M3_PER_H, _WORK_RATE_MAX_M3_PER_H, n)

    attachments = []
    for i in range(n):
        a_class = str(classes[i])
        rates: dict[str, float] = {}
        if a_class in _VOLUME_RATE_ATTACHMENT_CLASSES:
            rates[_QUANTITY_UNIT_VOLUME] = round(float(volume_rates[i]), 1)
        attachments.append(
            {
                "attachment_id": f"attachment_{i:06d}",
                "attachment_class": a_class,
                "supported_operations": list(_ATTACHMENT_OPERATION_MAP[a_class]),
                "draw_power_kw": round(float(powers[i]), 1),
                "cut_width_m": round(float(widths[i]), 2),
                "min_speed_kmh": round(float(min_speeds[i]), 1),
                "max_speed_kmh": round(float(max_speeds[i]), 1),
                "yard_id": yard_ids[int(assigned[i])],
                "work_rates": json.dumps(rates),
            }
        )
    return attachments


def _generate_construction_operators(
    rng: np.random.Generator,
    n_machines: int,
    yards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One licensed operator per machine (upper bound)."""
    n = n_machines
    yard_ids = [y["yard_id"] for y in yards]
    assigned = rng.integers(0, len(yards), size=n)
    shift_starts = rng.integers(_SHIFT_START_MIN_S, _SHIFT_START_MAX_S, size=n)
    shift_lengths = rng.integers(_SHIFT_LENGTH_MIN_S, _SHIFT_LENGTH_MAX_S, size=n)

    operators = []
    for i in range(n):
        n_licensed = int(rng.integers(1, len(_WORK_TYPES) + 1))
        licensed = rng.choice(_WORK_TYPES, size=n_licensed, replace=False).tolist()
        operators.append(
            {
                "operator_id": f"c_operator_{i:05d}",
                "full_name": f"Crew Operator {i:05d}",
                "shift_start_s": int(shift_starts[i]),
                "shift_end_s": int(shift_starts[i] + shift_lengths[i]),
                "licensed_operations": licensed,
                "yard_id": yard_ids[int(assigned[i])],
            }
        )
    return operators


def _generate_sites(
    rng: np.random.Generator,
    n_jobs: int,
) -> list[dict[str, Any]]:
    """One site per job; real projects may run several jobs on one site."""
    n = n_jobs
    lats, lons = _random_points_in_circle(
        rng, n, _REGION_CENTER_LAT, _REGION_CENTER_LON, _REGION_RADIUS_KM
    )
    plots = rng.uniform(_SITE_PLOT_MIN_HA, _SITE_PLOT_MAX_HA, n)
    grounds = rng.choice(_GROUND_CLASSES, size=n)

    return [
        {
            "site_id": f"site_{i:06d}",
            "label": f"Work Site {i:06d}",
            "plot_ha": round(float(plots[i]), 2),
            "entry_lat": round(float(lats[i]), 6),
            "entry_lon": round(float(lons[i]), 6),
            "ground_class": str(grounds[i]),
        }
        for i in range(n)
    ]


def _generate_jobs(
    rng: np.random.Generator,
    n: int,
    sites: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    work_types = rng.choice(_WORK_TYPES, size=n)
    deadline_days = rng.integers(_JOB_DEADLINE_DAYS_MIN, _JOB_DEADLINE_DAYS_MAX + 1, size=n)
    penalties = rng.uniform(_JOB_PENALTY_MIN_EUR_PER_DAY, _JOB_PENALTY_MAX_EUR_PER_DAY, n)
    revenues = rng.uniform(_JOB_REVENUE_MIN_EUR, _JOB_REVENUE_MAX_EUR, n)
    priorities = rng.integers(1, 11, size=n)
    volumes = rng.uniform(_JOB_QUANTITY_MIN_M3, _JOB_QUANTITY_MAX_M3, n)

    jobs = []
    for i in range(n):
        work_type = str(work_types[i])
        if work_type in _VOLUME_WORK_TYPES:
            quantity_value = round(float(volumes[i]), 1)
            quantity_unit = _QUANTITY_UNIT_VOLUME
        else:
            quantity_value = sites[i]["plot_ha"]
            quantity_unit = _QUANTITY_UNIT_AREA
        jobs.append(
            {
                "job_id": f"job_{i:06d}",
                "contract_id": f"project_{i // 5:05d}",
                "site_id": sites[i]["site_id"],
                "work_type": work_type,
                "plot_ha": sites[i]["plot_ha"],
                "deadline": (today + timedelta(days=int(deadline_days[i]))).isoformat(),
                "penalty_per_day_eur": round(float(penalties[i]), 2),
                "priority": int(priorities[i]),
                "status": TaskStatus.PENDING.value,
                "revenue_eur": round(float(revenues[i]), 2),
                "quantity_value": quantity_value,
                "quantity_unit": quantity_unit,
            }
        )
    return jobs

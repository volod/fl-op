"""Synthetic generators for stationary sensor stations and their readings.

Stations are anchored to fields and never move; their readings (battery level,
soil moisture, health status) feed the canonical Observation entity, and the
engine's monitoring policy derives EQUIPMENT_SERVICE tasks from them.
"""

from datetime import datetime, timedelta
from typing import Any

import numpy as np

from fl_op.canonical.enums import AssetMobility, HealthStatus
from fl_op.core.constants import BATTERY_LOW_THRESHOLD_PCT
from fl_op.data.agri_enums import SensorType

# One station is installed per this many fields.
_FIELDS_PER_SENSOR = 3

# Station placement jitter around the field centroid (degrees, ~100 m).
_SENSOR_POSITION_JITTER_DEG = 0.001

# Maintenance plan ranges.
_SERVICE_INTERVAL_DAYS_MIN = 90.0
_SERVICE_INTERVAL_DAYS_MAX = 365.0
_LAST_SERVICE_AGE_DAYS_MIN = 5.0
_LAST_SERVICE_AGE_DAYS_MAX = 400.0

# Reading history shape.
_READING_HISTORY_DAYS = 7
_READINGS_PER_DAY = 2

# Battery simulation: healthy stations sit well above the service threshold;
# a share of the fleet is generated already depleted or degraded so that the
# monitoring policy has work to do.
_BATTERY_HEALTHY_MIN_PCT = 45.0
_BATTERY_HEALTHY_MAX_PCT = 100.0
_BATTERY_DAILY_DRAIN_PCT = 0.6
_LOW_BATTERY_SHARE = 0.15
_DEGRADED_HEALTH_SHARE = 0.10

# Raw station metric codes as emitted by the field hardware. The mapping pack
# normalizes them to canonical codes via its metricCodes table.
_RAW_METRIC_BATTERY = "battery_pct"
_RAW_METRIC_HEALTH = "health_state"
_RAW_METRIC_SOIL_MOISTURE = "soil_moisture_pct"

# Soil-moisture readings (analytical payload; the engine does not act on them).
_SOIL_MOISTURE_MIN_PCT = 10.0
_SOIL_MOISTURE_MAX_PCT = 90.0

_BATTERY_UNIT = "%"
_MOISTURE_UNIT = "%"


def _generate_sensors(
    rng: np.random.Generator,
    fields: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Generate one stationary sensor station per _FIELDS_PER_SENSOR fields."""
    sensor_fields = fields[::_FIELDS_PER_SENSOR]
    n = len(sensor_fields)
    stypes = rng.choice([st.value for st in SensorType], size=max(n, 1))
    service_ages = rng.uniform(_LAST_SERVICE_AGE_DAYS_MIN, _LAST_SERVICE_AGE_DAYS_MAX, max(n, 1))
    intervals = rng.uniform(_SERVICE_INTERVAL_DAYS_MIN, _SERVICE_INTERVAL_DAYS_MAX, max(n, 1))

    sensors = []
    for i, fld in enumerate(sensor_fields):
        last_service = now - timedelta(days=float(service_ages[i]))
        jitter = rng.uniform(-_SENSOR_POSITION_JITTER_DEG, _SENSOR_POSITION_JITTER_DEG, 2)
        sensors.append(
            {
                "sensor_id": f"sensor_{i:05d}",
                "sensor_type": str(stypes[i]),
                "field_id": fld["field_id"],
                "lat": round(float(fld["centroid_lat"]) + float(jitter[0]), 6),
                "lon": round(float(fld["centroid_lon"]) + float(jitter[1]), 6),
                "mobility": AssetMobility.STATIONARY.value,
                "last_service_at": last_service.isoformat(),
                "service_interval_days": round(float(intervals[i]), 1),
                # Extra real-data fields retained for analysis, not mapped.
                "install_date": (last_service - timedelta(days=30)).date().isoformat(),
                "firmware_version": f"fw-{1 + (i % 4)}.{i % 10}",
            }
        )
    return sensors


def _generate_sensor_readings(
    rng: np.random.Generator,
    sensors: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Generate a short reading history per station (battery, moisture, health)."""
    readings: list[dict[str, Any]] = []
    n_steps = _READING_HISTORY_DAYS * _READINGS_PER_DAY
    step = timedelta(days=1.0 / _READINGS_PER_DAY)
    seq = 0

    for s_idx, sensor in enumerate(sensors):
        low_battery = rng.random() < _LOW_BATTERY_SHARE
        degraded = rng.random() < _DEGRADED_HEALTH_SHARE
        if low_battery:
            start_battery = rng.uniform(0.0, BATTERY_LOW_THRESHOLD_PCT)
        else:
            start_battery = rng.uniform(_BATTERY_HEALTHY_MIN_PCT, _BATTERY_HEALTHY_MAX_PCT)
        drain_per_step = _BATTERY_DAILY_DRAIN_PCT / _READINGS_PER_DAY

        for t in range(n_steps):
            observed_at = now - (n_steps - 1 - t) * step
            battery = max(0.0, start_battery - t * drain_per_step)
            readings.append(
                {
                    "reading_id": f"reading_{seq:07d}",
                    "sensor_id": sensor["sensor_id"],
                    "metric": _RAW_METRIC_BATTERY,
                    "value": round(float(battery), 1),
                    "state_value": "",
                    "unit": _BATTERY_UNIT,
                    "observed_at": observed_at.isoformat(),
                    "quality_flag": "ok",
                }
            )
            seq += 1
            readings.append(
                {
                    "reading_id": f"reading_{seq:07d}",
                    "sensor_id": sensor["sensor_id"],
                    "metric": _RAW_METRIC_SOIL_MOISTURE,
                    "value": round(float(rng.uniform(_SOIL_MOISTURE_MIN_PCT, _SOIL_MOISTURE_MAX_PCT)), 1),
                    "state_value": "",
                    "unit": _MOISTURE_UNIT,
                    "observed_at": observed_at.isoformat(),
                    "quality_flag": "ok",
                }
            )
            seq += 1

        health = HealthStatus.DEGRADED.value if degraded else HealthStatus.HEALTHY.value
        readings.append(
            {
                "reading_id": f"reading_{seq:07d}",
                "sensor_id": sensor["sensor_id"],
                "metric": _RAW_METRIC_HEALTH,
                "value": None,
                "state_value": health,
                "unit": "",
                "observed_at": now.isoformat(),
                "quality_flag": "ok",
            }
        )
        seq += 1

    return readings

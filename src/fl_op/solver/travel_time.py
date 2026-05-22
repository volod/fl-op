"""Travel time and field service duration calculations for OR-Tools routing."""

import math
from typing import Any

from fl_op.core.constants import EARTH_RADIUS_KM

_SECONDS_PER_KM = 240  # ~15 km/h average field travel speed -> 240 s/km


def _haversine_s(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Travel time in integer seconds between two lat/lon points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    km = 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(max(0.0, a)))
    return max(1, int(km * _SECONDS_PER_KM))


def _estimate_operation_seconds(order: dict[str, Any], implement: dict[str, Any]) -> int:
    """Estimate field service duration for one order and implement."""
    _OP_HOURS_MIN = 0.5
    _OP_HOURS_MAX = 24.0
    _OP_HOURS_FALLBACK = 1.0

    area = float(order.get("area_ha", 0))
    working_width = float(implement.get("working_width_m", 12))
    op_speed = float(implement.get("max_speed_kmh", 8))
    if working_width > 0 and op_speed > 0:
        op_hours = area / (working_width / 1000 * op_speed * 10)
        op_hours = max(_OP_HOURS_MIN, min(op_hours, _OP_HOURS_MAX))
    else:
        op_hours = _OP_HOURS_FALLBACK
    return int(op_hours * 3600)

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


def _estimate_operation_seconds(order: Any, implement: Any) -> int:
    """Estimate service duration for one order and implement.

    Precedence: an explicit service-duration override wins; otherwise the
    generic work quantity drives the estimate (area is its legacy alias).
    Area-like quantities (unit empty or "ha") use the width-times-speed
    coverage model; other units have no work-rate capability surface yet and
    fall back to the nominal effort.
    """
    _OP_HOURS_MIN = 0.5
    _OP_HOURS_MAX = 24.0
    _OP_HOURS_FALLBACK = 1.0
    _AREA_LIKE_UNITS = ("", "ha")

    explicit_minutes = _nonnegative(order.service_duration_min)
    if explicit_minutes > 0:
        return int(explicit_minutes * 60)

    quantity = _nonnegative(order.work_quantity)
    unit = str(order.work_quantity_unit or "")
    if quantity <= 0:
        quantity = _nonnegative(order.area)
        unit = ""
    if unit not in _AREA_LIKE_UNITS:
        return int(_OP_HOURS_FALLBACK * 3600)

    working_width = _nonnegative(implement.working_width)
    op_speed = _nonnegative(implement.max_speed)
    if working_width > 0 and op_speed > 0:
        op_hours = quantity / (working_width / 1000 * op_speed * 10)
        op_hours = max(_OP_HOURS_MIN, min(op_hours, _OP_HOURS_MAX))
    else:
        op_hours = _OP_HOURS_FALLBACK
    return int(op_hours * 3600)


def _nonnegative(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0

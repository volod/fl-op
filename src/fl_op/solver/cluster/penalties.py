"""Business-weighted optional-order penalties for OR-Tools routing."""

from typing import Any

_MIN_DROP_PENALTY_S = 3 * 24 * 3600
_EUR_TO_DROP_PENALTY_SECONDS = 600
_LATE_PENALTY_EXPOSURE_DAYS = 3


def order_drop_penalty_s(order: dict[str, Any]) -> int:
    """Return the routing penalty, in seconds, for leaving an order unserved."""
    revenue = _nonnegative_float(order.get("estimated_revenue_eur", 0.0))
    late_exposure = _nonnegative_float(order.get("penalty_per_day_eur", 0.0))
    business_value = revenue + late_exposure * _LATE_PENALTY_EXPOSURE_DAYS
    return max(_MIN_DROP_PENALTY_S, int(business_value * _EUR_TO_DROP_PENALTY_SECONDS))


def _nonnegative_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0

"""Business-weighted optional-order penalties for OR-Tools routing.

The routing objective currency is the "penalty second": one EUR of business
value converts to EUR_TO_DROP_PENALTY_SECONDS objective units. Drop penalties
and the fuel-priced arc costs share this conversion, so dropping an order is
weighed against the money cost of driving to serve it on one scale.
"""

from typing import Any

_MIN_DROP_PENALTY_S = 3 * 24 * 3600
EUR_TO_DROP_PENALTY_SECONDS = 600
_LATE_PENALTY_EXPOSURE_DAYS = 3


def order_drop_penalty_s(order: Any) -> int:
    """Return the routing penalty, in objective units, for an unserved order."""
    revenue = _nonnegative_float(order.revenue)
    late_exposure = _nonnegative_float(order.penalty_per_day)
    business_value = revenue + late_exposure * _LATE_PENALTY_EXPOSURE_DAYS
    return max(_MIN_DROP_PENALTY_S, int(business_value * EUR_TO_DROP_PENALTY_SECONDS))


def _nonnegative_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0

"""Resolve effective resource prices from canonical cost-rate rows.

Cost rates are data entities: when the snapshot carries a rate valid at the
planning time for a resource code, that rate wins; the engine cost constants
are the fallback for unpriced resources.
"""

import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _parse_ts(raw: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def resolve_unit_price(
    cost_rates: list[Any],
    rate_type: str,
    at: datetime,
    default: float,
) -> float:
    """Effective unit price of one resource at a point in time.

    Of the rates whose validity window contains ``at`` (absent bounds are
    open), the one with the latest valid-from wins; without any applicable
    rate the engine constant ``default`` applies.
    """
    best_price: Optional[float] = None
    best_from: Optional[datetime] = None
    for rate in cost_rates:
        if str(rate.rate_type) != rate_type:
            continue
        valid_from = _parse_ts(rate.valid_from)
        valid_to = _parse_ts(rate.valid_to)
        if valid_from is not None and at < valid_from:
            continue
        if valid_to is not None and at >= valid_to:
            continue
        if best_price is None or (valid_from or datetime.min.replace(tzinfo=at.tzinfo)) >= (
            best_from or datetime.min.replace(tzinfo=at.tzinfo)
        ):
            best_price = float(rate.unit_price)
            best_from = valid_from
    if best_price is None:
        return default
    logger.debug("Resolved %s price %.4f from cost-rate data", rate_type, best_price)
    return best_price

"""Resolve effective resource prices from canonical cost-rate rows.

Cost rates are data entities: when the snapshot carries a rate valid at the
planning time for a resource code, that rate wins; the engine cost constants
are the fallback for unpriced resources.
"""

import dataclasses
import logging
from datetime import datetime
from typing import Any, Optional

from fl_op.core.constants import (
    ELECTRICITY_COST_EUR_PER_KWH,
    FERTILIZER_COST_EUR_PER_KG,
    FUEL_COST_EUR_PER_L,
    RATE_TYPE_ELECTRICITY,
    RATE_TYPE_FUEL,
    RATE_TYPE_MATERIAL,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ResourcePrices:
    """Resolved per-run resource prices, picklable across the worker pool.

    Defaults are the engine cost constants; the chain overrides them with the
    prices resolved from the snapshot's cost-rate entities, so routing arc
    costs and dispatch margins are priced from the same data as KPIs.
    """

    fuel_eur_per_l: float = FUEL_COST_EUR_PER_L
    material_eur_per_kg: float = FERTILIZER_COST_EUR_PER_KG
    electricity_eur_per_kwh: float = ELECTRICITY_COST_EUR_PER_KWH

    def price_for(self, rate_type: str) -> float:
        """Return the resolved unit price for a resource code."""
        normalized = str(rate_type or RATE_TYPE_FUEL)
        if normalized == RATE_TYPE_ELECTRICITY:
            return self.electricity_eur_per_kwh
        if normalized == RATE_TYPE_MATERIAL:
            return self.material_eur_per_kg
        return self.fuel_eur_per_l


def vehicle_energy_resource_type(vehicle: Any) -> str:
    """Resource code consumed by a prime mover, defaulting to legacy fuel."""
    return str(getattr(vehicle, "energy_resource_type", "") or RATE_TYPE_FUEL)


def vehicle_energy_unit(vehicle: Any) -> str:
    """Display/unit code for the prime mover's energy quantity."""
    return str(
        getattr(vehicle, "energy_unit", "")
        or ("kWh" if vehicle_energy_resource_type(vehicle) == RATE_TYPE_ELECTRICITY else "L")
    )


def vehicle_energy_consumption_rate(vehicle: Any) -> float:
    """Energy units consumed per operating hour, with legacy fuel fallback."""
    explicit = getattr(vehicle, "energy_consumption_rate", 0.0)
    try:
        explicit_f = float(explicit or 0.0)
    except (TypeError, ValueError):
        explicit_f = 0.0
    if explicit_f > 0:
        return explicit_f
    try:
        return float(getattr(vehicle, "fuel_consumption_rate", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


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

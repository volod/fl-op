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
    LABOR_COST_EUR_PER_H,
    MACHINE_WEAR_COST_EUR_PER_H,
    RATE_TYPE_ELECTRICITY,
    RATE_TYPE_FUEL,
    RATE_TYPE_MATERIAL,
    SERVICE_FEE_EUR_PER_VISIT,
    TOLL_COST_EUR_PER_KM,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ResourcePrices:
    """Resolved per-run resource prices, picklable across the worker pool.

    Defaults are the engine cost constants; the chain overrides them with the
    prices resolved from the snapshot's cost-rate entities, so routing arc
    costs and dispatch margins are priced from the same data as KPIs.

    ``fuel``/``material``/``electricity`` price a consumed quantity; the
    operating rates price the dispatch itself. ``labor`` and ``machine_wear``
    are EUR per operating hour (travel plus on-task service time) and ``toll``
    is EUR per kilometre travelled. The operating rates default to zero so the
    extra arc-cost terms vanish unless cost-rate data prices them.
    """

    fuel_eur_per_l: float = FUEL_COST_EUR_PER_L
    material_eur_per_kg: float = FERTILIZER_COST_EUR_PER_KG
    electricity_eur_per_kwh: float = ELECTRICITY_COST_EUR_PER_KWH
    labor_eur_per_h: float = LABOR_COST_EUR_PER_H
    machine_wear_eur_per_h: float = MACHINE_WEAR_COST_EUR_PER_H
    toll_eur_per_km: float = TOLL_COST_EUR_PER_KM
    # Fixed fee charged once per served task, independent of service duration.
    service_fee_eur_per_visit: float = SERVICE_FEE_EUR_PER_VISIT

    def price_for(self, rate_type: str) -> float:
        """Return the resolved unit price for a consumed-resource code."""
        normalized = str(rate_type or RATE_TYPE_FUEL)
        if normalized == RATE_TYPE_ELECTRICITY:
            return self.electricity_eur_per_kwh
        if normalized == RATE_TYPE_MATERIAL:
            return self.material_eur_per_kg
        return self.fuel_eur_per_l

    @property
    def operating_eur_per_h(self) -> float:
        """Time-based operating surcharge per hour (driver labour plus wear).

        Charged over both travel and on-task service hours, so a bundle that
        finishes faster saves wages and wear, not just energy.
        """
        return self.labor_eur_per_h + self.machine_wear_eur_per_h


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


def _positive_rate(value: Any) -> Optional[float]:
    """Coerce a declared per-asset rate, returning None when absent/invalid."""
    try:
        rate = float(value or 0.0)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def vehicle_machine_wear_eur_per_h(vehicle: Any, fleet_fallback: float) -> float:
    """Per-vehicle machine-wear rate, falling back to the fleet wear rate."""
    explicit = _positive_rate(getattr(vehicle, "machine_wear_eur_per_h", 0.0))
    return explicit if explicit is not None else fleet_fallback


def operator_wage_eur_per_h(operator: Any, fleet_fallback: float) -> float:
    """Per-operator wage, falling back to the fleet labour rate."""
    if operator is None:
        return fleet_fallback
    explicit = _positive_rate(getattr(operator, "wage_eur_per_h", 0.0))
    return explicit if explicit is not None else fleet_fallback


def vehicle_operating_eur_per_h(
    vehicle: Any,
    operator_wage: Optional[float],
    prices: "ResourcePrices",
) -> float:
    """Operating rate (driver wage plus machine wear) for one vehicle/operator.

    Machine wear resolves from the prime mover, the wage from the assigned
    operator; either falls back to the fleet rate in ``prices`` when the asset
    declares none. ``operator_wage`` is the already-resolved operator wage for
    the cluster (None to use the fleet labour rate).
    """
    wear = vehicle_machine_wear_eur_per_h(vehicle, prices.machine_wear_eur_per_h)
    wage = operator_wage if operator_wage is not None else prices.labor_eur_per_h
    return wear + wage


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

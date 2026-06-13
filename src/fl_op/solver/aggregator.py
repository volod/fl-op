"""Result aggregation: KPI computation and schedule report writing."""

import json
import logging
import pathlib
from typing import Any, Optional

from fl_op.core.constants import (
    FERTILIZER_COST_EUR_PER_KG,
    FUEL_COST_EUR_PER_L,
    RELATED_MATERIAL_FILL_RATIO,
)
from fl_op.solver.greedy import _estimate_repositioning_cost
from fl_op.solver.travel_time import (
    TravelLookup,
    _estimate_operation_seconds,
    network_seconds,
    travel_mode_for_vehicle,
)

logger = logging.getLogger(__name__)


def _compute_kpis(
    dispatch_packages: list[dict[str, Any]],
    infeasible_orders: list[dict[str, Any]],
    orders: list[Any],
    greedy_assignment: dict[str, tuple[int, int]],
    fuel_price_eur_per_l: Optional[float] = None,
    material_price_eur_per_kg: Optional[float] = None,
    vehicles: Optional[list[Any]] = None,
    implements: Optional[list[Any]] = None,
    fields: Optional[list[Any]] = None,
    travel_lookup: Optional[TravelLookup] = None,
) -> dict[str, Any]:
    """Aggregate schedule KPIs.

    Prices come from resolved cost-rate data when supplied; the engine cost
    constants are the fallback.
    """
    fuel_price = (
        fuel_price_eur_per_l if fuel_price_eur_per_l is not None else FUEL_COST_EUR_PER_L
    )
    material_price = (
        material_price_eur_per_kg
        if material_price_eur_per_kg is not None
        else FERTILIZER_COST_EUR_PER_KG
    )
    total_margin = sum(d.get("estimated_margin_eur", 0) for d in dispatch_packages)
    total_fuel = sum(d.get("estimated_fuel_l", 0) for d in dispatch_packages)
    total_fertilizer = sum(d.get("estimated_fertilizer_kg", 0) for d in dispatch_packages)

    greedy_baseline = _compute_greedy_baseline_margin(
        orders,
        greedy_assignment,
        fuel_price,
        material_price,
        vehicles=vehicles,
        implements=implements,
        fields=fields,
        travel_lookup=travel_lookup,
    )

    infeasibility_reasons: dict[str, int] = {}
    for inf in infeasible_orders:
        r = inf.get("reason_code", "UNKNOWN")
        infeasibility_reasons[r] = infeasibility_reasons.get(r, 0) + 1

    return {
        "n_dispatched": len(dispatch_packages),
        "n_infeasible": len(infeasible_orders),
        "total_estimated_margin_eur": round(total_margin, 2),
        "greedy_baseline_margin_eur": round(greedy_baseline, 2),
        "solver_improvement_eur": round(total_margin - greedy_baseline, 2),
        "total_fuel_l": round(total_fuel, 2),
        "total_fuel_cost_eur": round(total_fuel * fuel_price, 2),
        "total_fertilizer_kg": round(total_fertilizer, 2),
        "total_material_cost_eur": round(total_fertilizer * material_price, 2),
        "infeasibility_reasons": infeasibility_reasons,
    }


def _compute_greedy_baseline_margin(
    orders: list[Any],
    greedy_assignment: dict[str, tuple[int, int]],
    fuel_price: float,
    material_price: float,
    vehicles: Optional[list[Any]] = None,
    implements: Optional[list[Any]] = None,
    fields: Optional[list[Any]] = None,
    travel_lookup: Optional[TravelLookup] = None,
) -> float:
    """Estimate the no-routing greedy baseline with dispatch-like net costs."""
    order_map = {o.task_id: o for o in orders}
    if vehicles is None or implements is None or fields is None:
        return sum(
            float(order_map[oid].revenue)
            - float(order_map[oid].area) * fuel_price
            for oid in greedy_assignment
            if oid in order_map
        )

    field_map = {f.location_id: f for f in fields}
    baseline = 0.0
    for oid, (v_idx, i_idx) in greedy_assignment.items():
        order = order_map.get(oid)
        if order is None:
            continue
        try:
            vehicle = vehicles[v_idx]
            implement = implements[i_idx]
        except (IndexError, TypeError):
            continue

        service_fuel_cost = (
            _estimate_operation_seconds(order, implement)
            / 3600.0
            * float(vehicle.fuel_consumption_rate)
            * fuel_price
        )
        material_cost = (
            float(implement.material_capacity)
            * RELATED_MATERIAL_FILL_RATIO
            * material_price
        )
        repositioning_cost = _greedy_repositioning_cost(
            order, vehicle, field_map.get(order.location_ref), fuel_price, travel_lookup
        )
        baseline += (
            float(order.revenue)
            - service_fuel_cost
            - material_cost
            - repositioning_cost
        )
    return baseline


def _greedy_repositioning_cost(
    order: Any,
    vehicle: Any,
    field: Any,
    fuel_price: float,
    travel_lookup: Optional[TravelLookup] = None,
) -> float:
    if field is None:
        return 0.0
    home_ref = str(getattr(vehicle, "home_depot_ref", "") or "")
    location_ref = str(getattr(order, "location_ref", "") or "")
    if travel_lookup and home_ref and location_ref and home_ref != location_ref:
        mode = travel_mode_for_vehicle(vehicle)
        seconds = network_seconds(
            travel_lookup, home_ref, location_ref, mode
        ) or network_seconds(
            travel_lookup, location_ref, home_ref, mode
        )
        if seconds:
            return (
                float(seconds)
                / 3600.0
                * float(vehicle.fuel_consumption_rate)
                * fuel_price
            )
    return _estimate_repositioning_cost(vehicle, field, fuel_price)


def _write_json(obj: Any, path: pathlib.Path) -> None:
    with path.open("w") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _write_report(
    dispatch_packages: list[dict[str, Any]],
    infeasible_orders: list[dict[str, Any]],
    kpis: dict[str, Any],
    path: pathlib.Path,
) -> None:
    lines = [
        "Fleet Optimization Schedule Report",
        "=" * 40,
        f"Dispatched:   {kpis['n_dispatched']}",
        f"Infeasible:   {kpis['n_infeasible']}",
        f"Total margin: {kpis['total_estimated_margin_eur']:.2f} EUR",
        f"Greedy base:  {kpis['greedy_baseline_margin_eur']:.2f} EUR",
        f"Margin delta: {kpis['solver_improvement_eur']:.2f} EUR",
        f"Total fuel:   {kpis['total_fuel_l']:.1f} L",
        "",
        "Infeasibility reasons:",
    ]
    for reason, count in sorted(kpis["infeasibility_reasons"].items()):
        lines.append(f"  {reason}: {count}")

    if infeasible_orders:
        lines.append("")
        lines.append("Infeasible orders (first 20):")
        for inf in infeasible_orders[:20]:
            lines.append(f"  {inf['task_id']}: {inf['reason_code']} - {inf['detail']}")

    path.write_text("\n".join(lines) + "\n")

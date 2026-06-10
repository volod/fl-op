"""Result aggregation: KPI computation and schedule report writing."""

import json
import logging
import pathlib
from typing import Any

from fl_op.core.constants import FUEL_COST_EUR_PER_L
logger = logging.getLogger(__name__)


def _compute_kpis(
    dispatch_packages: list[dict[str, Any]],
    infeasible_orders: list[dict[str, Any]],
    orders: list[Any],
    greedy_assignment: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    total_margin = sum(d.get("estimated_margin_eur", 0) for d in dispatch_packages)
    total_fuel = sum(d.get("estimated_fuel_l", 0) for d in dispatch_packages)
    total_fertilizer = sum(d.get("estimated_fertilizer_kg", 0) for d in dispatch_packages)

    order_map = {o.task_id: o for o in orders}
    greedy_baseline = sum(
        float(order_map[oid].revenue)
        - float(order_map[oid].area) * FUEL_COST_EUR_PER_L
        for oid in greedy_assignment
        if oid in order_map
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
        "total_fertilizer_kg": round(total_fertilizer, 2),
        "infeasibility_reasons": infeasibility_reasons,
    }


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
        f"Improvement:  {kpis['solver_improvement_eur']:.2f} EUR",
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

"""Resource-conflict attribution from the routing solution's primal signals.

OR-Tools' CP routing model exposes no LP duals or shadow prices, so there is no
exact marginal value for a binding resource. What the *primal* solution does
expose is how hard each routing dimension is pushed: how close the routes run to
the time horizon, how full the load-capacity dimensions get, and how much of the
fleet is used. When a cluster drops tasks, the dimension running tightest is the
actionable bottleneck to attribute the drop to -- a "which resource, if relaxed,
would most plausibly help" read computed from the solved routes alone, with no
extra solve.

This module is pure: it turns already-measured utilizations into the attribution
record. ``routing.py`` measures the utilizations off the solution and calls in.
The selection is a heuristic over primal utilization, not an exact dual; exact
marginal attribution (a finite-difference re-solve probe, or an LP relaxation
that exposes duals) stays future research.
"""

from typing import Any

from fl_op.core import constants

# binding_resource sentinels for the served / non-binding cases.
BINDING_NONE = "none"          # nothing dropped: no conflict to attribute
BINDING_FLEET = "fleet"        # every vehicle committed, no per-route limit tight
BINDING_TIME = "time"          # routes run up against the scheduling horizon
BINDING_OTHER = "other"        # tasks dropped but no aggregate dimension is tight
BINDING_SOLVE_BUDGET = "solve_budget"   # no solution found within the time limit
BINDING_INFEASIBLE = "model_infeasible"  # no solution and the budget was not hit


def _peak_capacity(capacity_utilization: dict[str, float]) -> tuple[str, float]:
    """The material whose load dimension runs fullest, and its fill fraction."""
    if not capacity_utilization:
        return "", 0.0
    material = max(sorted(capacity_utilization), key=lambda m: capacity_utilization[m])
    return material, capacity_utilization[material]


def build_resource_conflict(
    *,
    n_unserved: int,
    n_vehicles: int,
    n_vehicles_used: int,
    time_utilization: float,
    capacity_utilization: dict[str, float],
    tight_threshold: float = constants.RESOURCE_CONFLICT_TIGHT_UTILIZATION,
) -> dict[str, Any]:
    """Attribute a cluster's dropped tasks to its binding routing dimension.

    ``time_utilization`` is the latest route-end time over the horizon;
    ``capacity_utilization`` maps each load material to its peak fill fraction;
    fleet utilization is ``n_vehicles_used / n_vehicles``. When something was
    dropped the binding resource is chosen by a fixed priority over the physical
    limits first:

    * ``capacity:<material>`` -- a load dimension at/above ``tight_threshold``
      (the hardest physical limit; more capacity or reloads would serve more);
    * ``time`` -- routes run up against the scheduling horizon;
    * ``fleet`` -- every vehicle is committed but no single per-route dimension is
      tight, so more vehicles (or trips) is the lever;
    * ``other`` -- a spare vehicle remains and no dimension is tight, so the drop
      is a time-window/cost trade-off no aggregate dimension captures.

    ``none`` is reported when nothing was dropped. Capacity is ranked above the
    always-saturated fleet count so a single-vehicle cluster's real physical
    limit is not masked. Every utilization is reported regardless, so the signal
    is inspectable even when nothing is dropped.
    """
    vehicle_utilization = (n_vehicles_used / n_vehicles) if n_vehicles else 0.0
    capacity_material, capacity_peak = _peak_capacity(capacity_utilization)

    binding = BINDING_NONE
    binding_utilization = 0.0
    if n_unserved > 0:
        if capacity_peak >= tight_threshold:
            binding = f"capacity:{capacity_material}" if capacity_material else "capacity"
            binding_utilization = capacity_peak
        elif time_utilization >= tight_threshold:
            binding = BINDING_TIME
            binding_utilization = time_utilization
        elif vehicle_utilization >= 1.0:
            binding = BINDING_FLEET
            binding_utilization = vehicle_utilization
        else:
            binding = BINDING_OTHER
            binding_utilization = max(
                capacity_peak, time_utilization, vehicle_utilization
            )

    return {
        "binding_resource": binding,
        "binding_utilization": round(binding_utilization, 3),
        "tight_threshold": tight_threshold,
        "n_unserved": n_unserved,
        "vehicle_utilization": round(vehicle_utilization, 3),
        "time_utilization": round(time_utilization, 3),
        "capacity_utilization": {
            material: round(util, 3)
            for material, util in sorted(capacity_utilization.items())
        },
    }


def no_solution_conflict(*, hit_time_limit: bool, n_unserved: int) -> dict[str, Any]:
    """Attribution for a cluster that produced no solution.

    With no routes to inspect there is no dimension utilization; the binding
    resource is the solve budget (the search ran out of time) or a genuine model
    infeasibility (it stopped without exhausting the budget).
    """
    return {
        "binding_resource": BINDING_SOLVE_BUDGET if hit_time_limit else BINDING_INFEASIBLE,
        "n_unserved": n_unserved,
    }

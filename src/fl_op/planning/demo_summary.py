"""Demo summary rendering for the planning demo command."""

import json
import logging
import pathlib
from typing import Any

from fl_op.planning.demo_analysis import log_implementation_analysis

logger = logging.getLogger(__name__)


def print_demo_summary(periodic_dir: pathlib.Path, rolling_dir: pathlib.Path) -> None:
    """Log statistics and result analysis for the periodic and rolling runs."""
    snapshot = json.loads((periodic_dir / "snapshot.json").read_text())
    plan = json.loads((periodic_dir / "plan.json").read_text())
    revisions = json.loads((rolling_dir / "revisions_summary.json").read_text())["revisions"]

    score = plan.get("score", {})
    assigned = plan.get("assignments", [])
    unassigned = plan.get("unassigned_tasks", [])
    n_tasks = len(assigned) + len(unassigned)
    fill_rate = (len(assigned) / n_tasks * 100.0) if n_tasks else 0.0

    bar = "=" * 64
    logger.info(bar)
    logger.info("DEMO SUMMARY")
    logger.info(bar)

    _log_snapshot(snapshot)
    logger.info("-" * 64)
    _log_periodic_plan(plan, assigned, unassigned, n_tasks, fill_rate, score)
    logger.info("-" * 64)
    _log_rolling_revisions(revisions)
    logger.info("-" * 64)
    logger.info("Implementation analysis")
    log_implementation_analysis(snapshot, plan, revisions)
    logger.info(bar)


def _log_snapshot(snapshot: dict[str, Any]) -> None:
    qs = snapshot.get("quality_summary", {})
    excluded_readings = _excluded_observation_reading_count(snapshot)
    logger.info("Snapshot   : %s", snapshot.get("snapshot_id", "?"))
    logger.info("  hash            : %s", snapshot.get("snapshot_hash", "")[:16])
    logger.info(
        "  canonical       : %d assets, %d locations, %d tasks, %d feasible bundle pairs",
        len(snapshot.get("assets", [])),
        len(snapshot.get("locations", [])),
        len(snapshot.get("tasks", [])),
        (snapshot.get("bundle_summary") or {}).get("n_feasible_pairs", 0),
    )
    if excluded_readings:
        logger.info(
            "  quality         : %d findings, %d entities excluded, "
            "%d readings excluded",
            qs.get("n_findings", 0),
            qs.get("n_entities_excluded", 0),
            excluded_readings,
        )
    else:
        logger.info(
            "  quality         : %d findings, %d entities excluded",
            qs.get("n_findings", 0),
            qs.get("n_entities_excluded", 0),
        )


def _excluded_observation_reading_count(snapshot: dict[str, Any]) -> int:
    """Count observation readings excluded by assessment findings."""
    count = 0
    for finding in snapshot.get("quality_findings", []):
        rule_id = str(finding.get("rule_id", ""))
        action = str(finding.get("action_applied", ""))
        if rule_id == "dq://observation/source-flagged":
            count += 1
        elif rule_id.startswith("dq://observation/") and "excluded" in action:
            count += 1
    return count


def _log_periodic_plan(
    plan: dict[str, Any],
    assigned: list[dict[str, Any]],
    unassigned: list[dict[str, Any]],
    n_tasks: int,
    fill_rate: float,
    score: dict[str, Any],
) -> None:
    logger.info("Periodic (batch) plan: %s", plan.get("plan_id", "?"))
    logger.info(
        "  assigned        : %d / %d tasks (%.1f%% fill rate)",
        len(assigned),
        n_tasks,
        fill_rate,
    )
    logger.info("  unassigned      : %d", len(unassigned))
    logger.info(
        "  margin (est.)   : %.2f EUR  (admitted greedy %.2f, delta %+.2f)",
        score.get("total_estimated_margin_eur", 0.0),
        score.get("greedy_baseline_margin_eur", 0.0),
        score.get("solver_improvement_eur", 0.0),
    )
    logger.info(
        "  resources       : %.1f L fuel, %.1f kg fertilizer",
        score.get("total_fuel_l", 0.0),
        score.get("total_fertilizer_kg", 0.0),
    )
    _log_drone_kpis(score.get("drone_logistics_kpis") or {})
    _log_unassigned_reasons(unassigned)


def _log_rolling_revisions(revisions: list[dict[str, Any]]) -> None:
    logger.info("Rolling (stream) dispatch: %d revisions", len(revisions))
    logger.info(
        "  %-3s %-18s %6s %7s %8s %8s %6s",
        "rev", "trigger", "assign", "frozen", "carried", "changed", "unasn",
    )
    for r in revisions:
        logger.info(
            "  %-3d %-18s %6d %7d %8d %8d %6d",
            r["revision"],
            r["trigger"],
            r["n_assignments"],
            r["n_frozen"],
            r.get("n_carried_forward", 0),
            r["n_changed_after_freeze"],
            r["n_unassigned"],
        )
    total_instability = sum(r.get("plan_instability_penalty", 0) for r in revisions)
    total_changed = sum(r["n_changed_after_freeze"] for r in revisions[1:])
    logger.info(
        "  incremental replanning changed %d assignment(s) across %d event(s); "
        "total plan-instability penalty %d",
        total_changed,
        max(0, len(revisions) - 1),
        total_instability,
    )
    final_drone = (revisions[-1].get("drone_logistics_kpis") if revisions else {}) or {}
    if final_drone:
        logger.info(
            "  drone rolling  : %.1f%% churn, %d changed, %d carried",
            final_drone.get("rolling_churn_pct", 0.0),
            final_drone.get("rolling_changed_assignments", 0),
            final_drone.get("rolling_carried_forward_assignments", 0),
        )


def _log_unassigned_reasons(unassigned: list[dict[str, Any]]) -> None:
    if not unassigned:
        return
    reasons: dict[str, int] = {}
    for u in unassigned:
        reason = u.get("reason_code", "UNKNOWN")
        reasons[reason] = reasons.get(reason, 0) + 1
    for code, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        logger.info("    unassigned reason %-26s %d", code + ":", count)


def _log_drone_kpis(kpis: dict[str, Any]) -> None:
    if not kpis:
        return
    mode_split = kpis.get("mode_split") or {}
    energy = kpis.get("energy_or_fuel_equivalent_usage") or {}
    logger.info(
        "  drone KPIs      : %.1f%% on-time, mode UGV=%d/UAV=%d, "
        "%.2f EUR margin",
        kpis.get("on_time_rate_pct", 0.0),
        mode_split.get("UGV", 0),
        mode_split.get("UAV", 0),
        kpis.get("delivery_margin_eur", 0.0),
    )
    usage_label = _energy_usage_label(energy)
    logger.info(
        "  drone resources : %s, UGV %.1f%%, UAV %.1f%%, support %.1f%%",
        usage_label,
        kpis.get("ugv_utilization_pct", 0.0),
        kpis.get("uav_utilization_pct", 0.0),
        kpis.get("support_team_utilization_pct", 0.0),
    )
    logger.info(
        "  drone blocks    : %d weather-blocked UAV tasks, %d no-fly exclusions",
        kpis.get("weather_blocked_uav_tasks", 0),
        kpis.get("no_fly_exclusion_count", 0),
    )
    _log_airspace(kpis.get("airspace_deconfliction") or {})
    _log_charging(kpis.get("charging_schedule") or {})


def _log_airspace(airspace: dict[str, Any]) -> None:
    if not airspace:
        return
    logger.info(
        "  drone airspace  : %d flights in %d/%d corridors, %d/%d conflict pairs "
        "deconflicted (%d corridor + %d timed, %d residual), peak %d concurrent",
        airspace.get("n_aerial_flights", 0),
        airspace.get("corridors_used", 0),
        airspace.get("corridors_available", 0),
        airspace.get("n_deconflicted_pairs", 0),
        airspace.get("n_conflict_pairs", 0),
        airspace.get("n_corridor_separated_pairs", 0),
        airspace.get("n_time_separated_pairs", 0),
        airspace.get("n_residual_conflict_pairs", 0),
        airspace.get("max_concurrent_flights", 0),
    )
    if airspace.get("n_flights_held", 0):
        logger.info(
            "  drone holds     : %d flights held, max hold %.0f s, total %.0f s",
            airspace.get("n_flights_held", 0),
            airspace.get("max_deconfliction_delay_s", 0.0),
            airspace.get("total_deconfliction_delay_s", 0.0),
        )


def _log_charging(charging: dict[str, Any]) -> None:
    if not charging:
        return
    logger.info(
        "  drone charging  : %d sessions over %d hubs, %.1f kWh, %d queued "
        "(max wait %.0f s, peak depth %d)",
        charging.get("n_charging_sessions", 0),
        charging.get("n_hubs_with_charging", 0),
        charging.get("total_energy_charged_kwh", 0.0),
        charging.get("n_queued_sessions", 0),
        charging.get("max_queue_wait_s", 0.0),
        charging.get("peak_queue_depth", 0),
    )
    logger.info(
        "  drone turnaround: max %.0f s, mean %.0f s, %d at risk",
        charging.get("max_turnaround_s", 0.0),
        charging.get("mean_turnaround_s", 0.0),
        charging.get("n_turnaround_at_risk", 0),
    )


def _energy_usage_label(energy: dict[str, Any]) -> str:
    if energy.get("electricity_kwh", 0.0):
        return f"{float(energy.get('electricity_kwh', 0.0)):.1f} kWh"
    if energy.get("fuel_equivalent_l", 0.0):
        return f"{float(energy.get('fuel_equivalent_l', 0.0)):.1f} fuel-eq"
    by_unit = energy.get("by_unit") or {}
    if by_unit:
        unit, quantity = next(iter(sorted(by_unit.items())))
        return f"{float(quantity):.1f} {unit}"
    return "0.0 energy"

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
    logger.info("Snapshot   : %s", snapshot.get("snapshot_id", "?"))
    logger.info("  hash            : %s", snapshot.get("snapshot_hash", "")[:16])
    logger.info(
        "  canonical       : %d assets, %d locations, %d tasks, %d feasible bundle pairs",
        len(snapshot.get("assets", [])),
        len(snapshot.get("locations", [])),
        len(snapshot.get("tasks", [])),
        (snapshot.get("bundle_summary") or {}).get("n_feasible_pairs", 0),
    )
    logger.info(
        "  quality         : %d findings, %d entities excluded",
        qs.get("n_findings", 0),
        qs.get("n_entities_excluded", 0),
    )


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
        "  margin (est.)   : %.2f EUR  (greedy baseline %.2f, solver %+.2f)",
        score.get("total_estimated_margin_eur", 0.0),
        score.get("greedy_baseline_margin_eur", 0.0),
        score.get("solver_improvement_eur", 0.0),
    )
    logger.info(
        "  resources       : %.1f L fuel, %.1f kg fertilizer",
        score.get("total_fuel_l", 0.0),
        score.get("total_fertilizer_kg", 0.0),
    )
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


def _log_unassigned_reasons(unassigned: list[dict[str, Any]]) -> None:
    if not unassigned:
        return
    reasons: dict[str, int] = {}
    for u in unassigned:
        reason = u.get("reason_code", "UNKNOWN")
        reasons[reason] = reasons.get(reason, 0) + 1
    for code, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        logger.info("    unassigned reason %-26s %d", code + ":", count)

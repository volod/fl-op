"""Demo summary rendering for the planning demo command."""

import json
import logging
import pathlib
from typing import Any

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

    qs = snapshot.get("quality_summary", {})
    logger.info("Snapshot   : %s", snapshot.get("snapshot_id", "?"))
    logger.info("  hash            : %s", snapshot.get("snapshot_hash", "")[:16])
    logger.info(
        "  canonical       : %d assets, %d locations, %d tasks, %d bundles",
        len(snapshot.get("assets", [])),
        len(snapshot.get("locations", [])),
        len(snapshot.get("tasks", [])),
        len(snapshot.get("bundles", [])),
    )
    logger.info(
        "  quality         : %d findings, %d entities excluded",
        qs.get("n_findings", 0),
        qs.get("n_entities_excluded", 0),
    )

    logger.info("-" * 64)
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

    logger.info("-" * 64)
    logger.info("Rolling (stream) dispatch: %d revisions", len(revisions))
    logger.info(
        "  %-3s %-18s %6s %7s %8s %8s %6s",
        "rev",
        "trigger",
        "assign",
        "frozen",
        "carried",
        "changed",
        "unasn",
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

    logger.info("-" * 64)
    logger.info("Implementation analysis")
    _log_demo_implementation_analysis(snapshot, plan, revisions)
    logger.info(bar)


def _log_unassigned_reasons(unassigned: list[dict[str, Any]]) -> None:
    if not unassigned:
        return
    reasons: dict[str, int] = {}
    for u in unassigned:
        reason = u.get("reason_code", "UNKNOWN")
        reasons[reason] = reasons.get(reason, 0) + 1
    for code, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        logger.info("    unassigned reason %-26s %d", code + ":", count)


def _log_demo_implementation_analysis(
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    revisions: list[dict[str, Any]],
) -> None:
    """Append dataflow and optimization diagnostics to the demo log."""
    payload = snapshot.get("solver_payload", {})
    score = plan.get("score", {})
    assignments = plan.get("assignments", [])
    unassigned = plan.get("unassigned_tasks", [])
    version_dimensions = snapshot.get("version_dimensions", {})

    contract_count = len(version_dimensions.get("contract_versions", {}))
    profile_version = version_dimensions.get("optimization_profile_version", "?")
    mapping_versions = version_dimensions.get("mapping_versions", {})
    mapping_version = ", ".join(
        f"{k}={v}" for k, v in sorted(mapping_versions.items())
    ) or "?"

    payload_counts = {
        key: len(value) for key, value in payload.items() if isinstance(value, list)
    }
    logger.info(
        "  dataflow        : %d governed contracts -> %d canonical tasks -> %d solver rows",
        contract_count,
        len(snapshot.get("tasks", [])),
        sum(payload_counts.values()),
    )
    logger.info(
        "  versions        : profile %s, mapping %s",
        profile_version,
        mapping_version,
    )
    logger.info(
        "  solver payload  : %d vehicles, %d implements, %d operators, "
        "%d depots, %d fields, %d orders",
        payload_counts.get("vehicles", 0),
        payload_counts.get("implements", 0),
        payload_counts.get("operators", 0),
        payload_counts.get("depots", 0),
        payload_counts.get("fields", 0),
        payload_counts.get("orders", 0),
    )

    n_clusters = int(score.get("n_clusters", 0) or 0)
    warm_start = int(score.get("n_greedy_warm_start_assignments", 0) or 0)
    assignments_per_cluster = len(assignments) / n_clusters if n_clusters else 0.0
    logger.info(
        "  decomposition   : %d clusters, %d warm-start assignments, "
        "%.1f final assignments/cluster",
        n_clusters,
        warm_start,
        assignments_per_cluster,
    )

    vehicle_ids = {
        aid for a in assignments for aid in a.get("asset_ids", []) if aid.startswith("vehicle")
    }
    implement_ids = {
        aid for a in assignments for aid in a.get("asset_ids", []) if aid.startswith("implement")
    }
    vehicle_util = (
        len(vehicle_ids) / payload_counts.get("vehicles", 1) * 100.0
        if payload_counts.get("vehicles", 0)
        else 0.0
    )
    implement_util = (
        len(implement_ids) / payload_counts.get("implements", 1) * 100.0
        if payload_counts.get("implements", 0)
        else 0.0
    )
    logger.info(
        "  utilization     : %d vehicles used (%.1f%%), %d implements used (%.1f%%)",
        len(vehicle_ids),
        vehicle_util,
        len(implement_ids),
        implement_util,
    )

    if assignments:
        logger.info(
            "  assignment load : %.2f tasks/used vehicle, %.2f tasks/used implement",
            len(assignments) / max(1, len(vehicle_ids)),
            len(assignments) / max(1, len(implement_ids)),
        )

    reasons = _unassigned_reason_counts(unassigned)
    if reasons:
        top_reason, top_count = max(reasons.items(), key=lambda kv: kv[1])
        logger.info(
            "  bottleneck      : %s (%d unassigned task%s)",
            top_reason,
            top_count,
            "" if top_count == 1 else "s",
        )
    else:
        logger.info("  bottleneck      : none observed in periodic plan")

    if revisions:
        baseline_assignments = max(1, revisions[0].get("n_assignments", 0))
        changed_after_baseline = sum(
            r.get("n_changed_after_freeze", 0) for r in revisions[1:]
        )
        carried_forward = sum(r.get("n_carried_forward", 0) for r in revisions[1:])
        churn_pct = changed_after_baseline / baseline_assignments * 100.0
        logger.info(
            "  rolling stability: %.1f%% churn, %d carried-forward assignments after events",
            churn_pct,
            carried_forward,
        )

    logger.info(
        "  objective delta : solver %+.2f EUR vs greedy baseline",
        float(score.get("solver_improvement_eur", 0.0)),
    )


def _unassigned_reason_counts(unassigned: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in unassigned:
        details = item.get("details", {})
        reason = details.get("legacy_reason") or item.get("reason_code", "UNKNOWN")
        counts[reason] = counts.get(reason, 0) + 1
    return counts

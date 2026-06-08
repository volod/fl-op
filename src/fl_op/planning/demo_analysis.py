"""Implementation analysis section of the planning demo summary."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def log_implementation_analysis(
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    revisions: list[dict[str, Any]],
) -> None:
    """Log dataflow, decomposition, utilization, and rolling-stability diagnostics."""
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
        reason = item.get("reason_code") or details.get("reason_code", "UNKNOWN")
        counts[reason] = counts.get(reason, 0) + 1
    return counts

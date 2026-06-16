"""Service-prognosis accuracy feedback.

Every rolling revision records the fate of monitoring-derived service tasks:
how many are active, how many were withdrawn (the prognosis proved a false
positive) and how many were escalated (the asset degraded faster than
forecast, a false negative). The accumulated rates tell the operator how to
tune the per-domain monitoring thresholds; recommendations are logged, not
applied automatically.
"""

import json
import logging
import pathlib
from typing import TYPE_CHECKING, Any, Optional

from fl_op.canonical.enums import CorrectiveActionType
from fl_op.core.constants import (
    PROGNOSIS_FALSE_NEGATIVE_ALERT,
    PROGNOSIS_FALSE_POSITIVE_ALERT,
    PROGNOSIS_LOG_FILENAME,
    QUALITY_TREND_DIRNAME,
)
from fl_op.core.paths import DATA_ROOT

if TYPE_CHECKING:
    from fl_op.canonical.plan import Plan

logger = logging.getLogger(__name__)


def _log_path(path: Optional[pathlib.Path]) -> pathlib.Path:
    return path or (DATA_ROOT / QUALITY_TREND_DIRNAME / PROGNOSIS_LOG_FILENAME)


def _service_task_ids(plan: "Plan") -> set[str]:
    from fl_op.adapters.rolling.corrective import is_service_task_id

    return {
        a.task_id for a in plan.assignments if is_service_task_id(a.task_id)
    } | {
        u.task_id for u in plan.unassigned_tasks if is_service_task_id(u.task_id)
    }


def _action_task_ids(plan: "Plan", action: CorrectiveActionType) -> list[str]:
    return [ca.task_id for ca in plan.corrective_actions if ca.action == action]


def _split_by_asset_type(
    active_ids: set[str],
    withdrawn_ids: list[str],
    escalated_ids: list[str],
    asset_types: dict[str, str],
) -> dict[str, dict[str, int]]:
    """Per-asset-type active/withdrawn/escalated counts for one revision.

    The asset type is resolved from the service task id (``service-<asset>``)
    through the snapshot's asset->type map; tasks whose asset type is unknown
    are left out of the split (they still count in the global totals).
    """
    from fl_op.adapters.rolling.corrective import SERVICE_TASK_PREFIX

    by_type: dict[str, dict[str, int]] = {}
    for ids, key in (
        (active_ids, "active"),
        (withdrawn_ids, "withdrawn"),
        (escalated_ids, "escalated"),
    ):
        for task_id in ids:
            asset_type = asset_types.get(task_id[len(SERVICE_TASK_PREFIX):], "")
            if not asset_type:
                continue
            by_type.setdefault(
                asset_type, {"active": 0, "withdrawn": 0, "escalated": 0}
            )[key] += 1
    return by_type


def record_prognosis_outcomes(
    plan: "Plan",
    path: Optional[pathlib.Path] = None,
    asset_types: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Append one revision's service-task outcome record; return the record.

    ``asset_types`` (asset id -> asset type, from the snapshot) adds a
    per-asset-type breakdown to the record so accuracy can be split by station
    class; without it only the global totals are recorded.
    """
    active_ids = _service_task_ids(plan)
    withdrawn_ids = _action_task_ids(plan, CorrectiveActionType.SERVICE_WITHDRAWN)
    escalated_ids = _action_task_ids(plan, CorrectiveActionType.SERVICE_ESCALATED)
    record: dict[str, Any] = {
        "generated_at": plan.generated_at.isoformat(),
        "revision_id": plan.revision_id,
        "n_service_active": len(active_ids),
        "n_service_withdrawn": len(withdrawn_ids),
        "n_service_escalated": len(escalated_ids),
    }
    if asset_types:
        by_type = _split_by_asset_type(
            active_ids, withdrawn_ids, escalated_ids, asset_types
        )
        if by_type:
            record["by_asset_type"] = by_type
    target = _log_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("Could not append prognosis record to %s: %s", target, exc)
    return record


def prognosis_accuracy(path: Optional[pathlib.Path] = None) -> dict[str, float]:
    """Aggregate outcome rates over the recorded history.

    The false-positive rate is the share of withdrawals among all service
    prognoses observed (active + withdrawn); the false-negative rate is the
    share of escalations among them.
    """
    target = _log_path(path)
    if not target.exists():
        return {}
    active = withdrawn = escalated = 0
    by_type: dict[str, dict[str, int]] = {}
    for line in target.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        active += int(record.get("n_service_active", 0))
        withdrawn += int(record.get("n_service_withdrawn", 0))
        escalated += int(record.get("n_service_escalated", 0))
        for asset_type, counts in (record.get("by_asset_type") or {}).items():
            agg = by_type.setdefault(
                asset_type, {"active": 0, "withdrawn": 0, "escalated": 0}
            )
            for key in agg:
                agg[key] += int(counts.get(key, 0))
    observed = active + withdrawn
    if observed == 0:
        return {}
    result = {
        "n_observed": float(observed),
        "false_positive_rate": withdrawn / observed,
        "false_negative_rate": escalated / observed,
    }
    type_rates = {
        asset_type: rates
        for asset_type, counts in by_type.items()
        if (rates := _rates(counts)) is not None
    }
    if type_rates:
        result["by_asset_type"] = type_rates
    return result


def _rates(counts: dict[str, int]) -> Optional[dict[str, float]]:
    """False-positive/negative rates for one asset type's accumulated counts."""
    observed = counts["active"] + counts["withdrawn"]
    if observed == 0:
        return None
    return {
        "n_observed": float(observed),
        "false_positive_rate": counts["withdrawn"] / observed,
        "false_negative_rate": counts["escalated"] / observed,
    }


def log_threshold_recommendations(accuracy: dict[str, Any]) -> None:
    """Translate accumulated accuracy into monitoring-threshold suggestions.

    Logs a global recommendation and, where per-asset-type accuracy splits are
    present, one per station class so a single noisy asset type can be tuned
    without disturbing the rest of the fleet.
    """
    if not accuracy:
        return
    _log_recommendation("", accuracy)
    for asset_type, rates in (accuracy.get("by_asset_type") or {}).items():
        _log_recommendation(asset_type, rates)


def _log_recommendation(scope: str, rates: dict[str, float]) -> None:
    label = f" for asset type {scope}" if scope else ""
    fp_rate = rates.get("false_positive_rate", 0.0)
    fn_rate = rates.get("false_negative_rate", 0.0)
    if fp_rate > PROGNOSIS_FALSE_POSITIVE_ALERT:
        logger.warning(
            "Service prognoses withdrawn at %.0f%%%s: consider lowering "
            "batteryForecastHorizonDays or compositeHealthThreshold in the "
            "domain monitoring policy",
            fp_rate * 100.0,
            label,
        )
    if fn_rate > PROGNOSIS_FALSE_NEGATIVE_ALERT:
        logger.warning(
            "Service prognoses escalated at %.0f%%%s: consider raising "
            "batteryLowThresholdPct or batteryForecastHorizonDays in the "
            "domain monitoring policy",
            fn_rate * 100.0,
            label,
        )

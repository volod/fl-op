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


def record_prognosis_outcomes(
    plan: "Plan", path: Optional[pathlib.Path] = None
) -> dict[str, Any]:
    """Append one revision's service-task outcome record; return the record."""
    by_action = {
        action_type: sum(1 for ca in plan.corrective_actions if ca.action == action_type)
        for action_type in CorrectiveActionType
    }
    record = {
        "generated_at": plan.generated_at.isoformat(),
        "revision_id": plan.revision_id,
        "n_service_active": len(_service_task_ids(plan)),
        "n_service_withdrawn": by_action[CorrectiveActionType.SERVICE_WITHDRAWN],
        "n_service_escalated": by_action[CorrectiveActionType.SERVICE_ESCALATED],
    }
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
    observed = active + withdrawn
    if observed == 0:
        return {}
    return {
        "n_observed": float(observed),
        "false_positive_rate": withdrawn / observed,
        "false_negative_rate": escalated / observed,
    }


def log_threshold_recommendations(accuracy: dict[str, float]) -> None:
    """Translate accumulated accuracy into monitoring-threshold suggestions."""
    if not accuracy:
        return
    fp_rate = accuracy.get("false_positive_rate", 0.0)
    fn_rate = accuracy.get("false_negative_rate", 0.0)
    if fp_rate > PROGNOSIS_FALSE_POSITIVE_ALERT:
        logger.warning(
            "Service prognoses withdrawn at %.0f%%: consider lowering "
            "batteryForecastHorizonDays or compositeHealthThreshold in the "
            "domain monitoring policy",
            fp_rate * 100.0,
        )
    if fn_rate > PROGNOSIS_FALSE_NEGATIVE_ALERT:
        logger.warning(
            "Service prognoses escalated at %.0f%%: consider raising "
            "batteryLowThresholdPct or batteryForecastHorizonDays in the "
            "domain monitoring policy",
            fn_rate * 100.0,
        )

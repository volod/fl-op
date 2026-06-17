"""Task-completion lead-time measurement.

``task.completed`` events (and telemetry-derived completions) close the
execution feedback loop: each completion is logged with how much lead the
execution had against the task's deadline and how far it drifted from the
planned finish. The aggregated distribution shows how early or late
prognoses and schedules actually run - the signal that withdrawn/escalated
counts alone cannot provide.
"""

import json
import logging
import pathlib
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from fl_op.core.constants import LEAD_TIME_LOG_FILENAME, QUALITY_TREND_DIRNAME
from fl_op.core.paths import DATA_ROOT

if TYPE_CHECKING:
    from fl_op.canonical.plan import Plan

logger = logging.getLogger(__name__)


def _log_path(path: Optional[pathlib.Path]) -> pathlib.Path:
    return path or (DATA_ROOT / QUALITY_TREND_DIRNAME / LEAD_TIME_LOG_FILENAME)


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def record_completions(
    completions: list[dict[str, Any]],
    previous_plan: Optional["Plan"] = None,
    path: Optional[pathlib.Path] = None,
) -> list[dict[str, Any]]:
    """Append one lead-time record per completion; return the records.

    ``lead_time_s`` is deadline minus completion (positive: finished with
    lead, negative: finished late); ``schedule_error_s`` is completion minus
    the previous plan's planned finish (positive: ran behind schedule).
    """
    from fl_op.adapters.rolling.corrective import is_service_task_id

    assignments = (
        {a.task_id: a for a in previous_plan.assignments}
        if previous_plan is not None
        else {}
    )
    records: list[dict[str, Any]] = []
    for completion in completions:
        task_id = str(completion.get("task_id", ""))
        completed = _parse_ts(completion.get("completed_at"))
        deadline = _parse_ts(completion.get("deadline"))
        assignment = assignments.get(task_id)
        record: dict[str, Any] = {
            "task_id": task_id,
            "via": completion.get("via", ""),
            "is_service": is_service_task_id(task_id),
            "completed_at": completed.isoformat() if completed else None,
            "deadline": deadline.isoformat() if deadline else None,
            "lead_time_s": (
                round((deadline - completed).total_seconds(), 1)
                if deadline and completed
                else None
            ),
            "planned_finish": (
                assignment.planned_finish.isoformat() if assignment else None
            ),
            "schedule_error_s": (
                round((completed - assignment.planned_finish).total_seconds(), 1)
                if assignment and completed
                else None
            ),
        }
        records.append(record)

    if records:
        target = _log_path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a") as fh:
                for record in records:
                    fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.warning("Could not append lead-time records to %s: %s", target, exc)
    return records


def lead_time_stats(path: Optional[pathlib.Path] = None) -> dict[str, Any]:
    """Aggregate the recorded completion lead-time distribution.

    Reports counts, the lead-time mean/min/max and the late share over all
    completions with a measurable lead, plus the service-prognosis split
    (the forecast-lead-time signal the monitoring loop is tuned by).
    """
    target = _log_path(path)
    if not target.exists():
        return {}
    leads: list[float] = []
    service_leads: list[float] = []
    n_total = 0
    for line in target.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        n_total += 1
        lead = record.get("lead_time_s")
        if lead is None:
            continue
        leads.append(float(lead))
        if record.get("is_service"):
            service_leads.append(float(lead))
    if not n_total:
        return {}
    stats: dict[str, Any] = {"n_completions": n_total, "n_with_lead": len(leads)}
    if leads:
        stats.update(
            {
                "mean_lead_s": round(sum(leads) / len(leads), 1),
                "min_lead_s": round(min(leads), 1),
                "max_lead_s": round(max(leads), 1),
                "late_share": round(
                    sum(1 for lead in leads if lead < 0) / len(leads), 4
                ),
            }
        )
    if service_leads:
        stats["n_service_completions"] = len(service_leads)
        stats["mean_service_lead_s"] = round(
            sum(service_leads) / len(service_leads), 1
        )
        # Share of service tasks finishing after their deadline (lead < 0):
        # the signal guarded monitoring tuning folds in to loosen a policy
        # that derives service work too late.
        stats["service_late_share"] = round(
            sum(1 for lead in service_leads if lead < 0) / len(service_leads), 4
        )
    return stats

"""Feedback loops for solver memory sizing and LNS budgets."""

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.core import constants
from fl_op.core.paths import DATA_ROOT

logger = logging.getLogger(__name__)


def _feedback_dir() -> pathlib.Path:
    return DATA_ROOT / constants.SOLVER_FEEDBACK_DIRNAME


def _memory_path() -> pathlib.Path:
    return _feedback_dir() / constants.SOLVER_MEMORY_FEEDBACK_FILENAME


def _lns_path() -> pathlib.Path:
    return _feedback_dir() / constants.SOLVER_LNS_FEEDBACK_FILENAME


def load_worker_memory_feedback() -> dict[str, Any]:
    return _read_json(_memory_path())


def calibrated_worker_memory_mb(estimated_mb: float) -> float:
    """Use retained worker RSS feedback as a floor on the memory estimate."""
    if not constants.SOLVER_FEEDBACK_ENABLED:
        return estimated_mb
    feedback = load_worker_memory_feedback()
    observed = _as_float(feedback.get("max_worker_rss_mb"))
    if observed is None:
        return estimated_mb
    return max(estimated_mb, observed)


def record_solver_feedback(records: list[dict[str, Any]]) -> None:
    """Persist memory and LNS feedback from one completed cluster pool."""
    if not constants.SOLVER_FEEDBACK_ENABLED or not records:
        return
    _record_worker_memory(records)
    _record_lns(records)


def _record_worker_memory(records: list[dict[str, Any]]) -> None:
    rss_values = [
        value
        for value in (_as_float(record.get("worker_max_rss_mb")) for record in records)
        if value is not None and value > 0
    ]
    if not rss_values:
        return
    previous = load_worker_memory_feedback()
    previous_max = _as_float(previous.get("max_worker_rss_mb")) or 0.0
    count = int(previous.get("n_records", 0) or 0) + len(rss_values)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "n_records": count,
        "max_worker_rss_mb": round(max(previous_max, max(rss_values)), 2),
        "last_run_max_worker_rss_mb": round(max(rss_values), 2),
        "last_run_mean_worker_rss_mb": round(sum(rss_values) / len(rss_values), 2),
    }
    _write_json(_memory_path(), payload)


def _record_lns(records: list[dict[str, Any]]) -> None:
    attempts = [record for record in records if record.get("lns_attempted")]
    if not attempts:
        return
    improvements = [
        abs(float(record.get("lns_objective_delta", 0)))
        for record in attempts
        if float(record.get("lns_objective_delta", 0) or 0) < 0
    ]
    previous = load_lns_feedback()
    prev_attempts = int(previous.get("n_attempted", 0) or 0)
    prev_improved = int(previous.get("n_improved", 0) or 0)
    prev_total_delta = _as_float(previous.get("total_abs_objective_delta")) or 0.0
    total_delta = prev_total_delta + sum(improvements)
    n_improved = prev_improved + len(improvements)
    n_attempted = prev_attempts + len(attempts)
    mean_delta = total_delta / n_improved if n_improved else 0.0
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "n_attempted": n_attempted,
        "n_improved": n_improved,
        "total_abs_objective_delta": round(total_delta, 2),
        "mean_abs_objective_delta": round(mean_delta, 2),
        "last_run_attempted": len(attempts),
        "last_run_improved": len(improvements),
        "last_run_abs_objective_delta": round(sum(improvements), 2),
    }
    _write_json(_lns_path(), payload)


def load_lns_feedback() -> dict[str, Any]:
    return _read_json(_lns_path())


def lns_budget_multiplier() -> float:
    """Budget multiplier from retained LNS objective-delta feedback."""
    if not constants.SOLVER_FEEDBACK_ENABLED:
        return 1.0
    feedback = load_lns_feedback()
    attempts = int(feedback.get("n_attempted", 0) or 0)
    if attempts <= 0:
        return 1.0
    mean_delta = _as_float(feedback.get("mean_abs_objective_delta")) or 0.0
    reference = max(1.0, constants.CLUSTER_LNS_FEEDBACK_REFERENCE_DELTA)
    raw = mean_delta / reference
    if mean_delta <= 0:
        raw = constants.CLUSTER_LNS_MIN_BUDGET_MULTIPLIER
    return max(
        constants.CLUSTER_LNS_MIN_BUDGET_MULTIPLIER,
        min(constants.CLUSTER_LNS_MAX_BUDGET_MULTIPLIER, raw),
    )


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable solver feedback %s: %s", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    except OSError as exc:
        logger.warning("Could not write solver feedback %s: %s", path, exc)


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

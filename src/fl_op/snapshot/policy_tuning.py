"""Guarded automatic monitoring-policy tuning with an audit trail.

Accumulated service-prognosis accuracy (stream/prognosis.py) may adjust the
active monitoring policy automatically when MONITORING_AUTO_TUNE_ENABLED is
set: a high false-positive rate (withdrawn prognoses) tightens the policy by
lowering the battery forecast horizon and the composite health threshold; a
high false-negative rate (escalated prognoses) loosens it the other way.

Every adjustment is one bounded step (MONITORING_AUTO_TUNE_MAX_STEP_PCT,
absolute clamps) written to a tuned-policy overlay under DATA_DIR/quality
and appended to a JSONL audit trail. The reviewed profile document is never
modified; the snapshot builder layers the overlay on top of it, and deleting
the overlay reverts to the profile as reviewed.
"""

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.contracts.profile import MonitoringPolicySpec
from fl_op.core.constants import (
    MONITORING_AUTO_TUNE_MAX_STEP_PCT,
    MONITORING_TUNE_AUDIT_FILENAME,
    MONITORING_TUNE_COMPOSITE_MAX,
    MONITORING_TUNE_COMPOSITE_MIN,
    MONITORING_TUNE_HORIZON_MAX_DAYS,
    MONITORING_TUNE_HORIZON_MIN_DAYS,
    MONITORING_TUNED_POLICY_FILENAME,
    PROGNOSIS_FALSE_NEGATIVE_ALERT,
    PROGNOSIS_FALSE_POSITIVE_ALERT,
    QUALITY_TREND_DIRNAME,
)
from fl_op.core.paths import DATA_ROOT

logger = logging.getLogger(__name__)

# The auto-tunable policy fields and their absolute clamps.
_TUNABLE_BOUNDS: dict[str, tuple[float, float]] = {
    "batteryForecastHorizonDays": (
        MONITORING_TUNE_HORIZON_MIN_DAYS,
        MONITORING_TUNE_HORIZON_MAX_DAYS,
    ),
    "compositeHealthThreshold": (
        MONITORING_TUNE_COMPOSITE_MIN,
        MONITORING_TUNE_COMPOSITE_MAX,
    ),
}


def _overlay_path(path: Optional[pathlib.Path]) -> pathlib.Path:
    return path or (
        DATA_ROOT / QUALITY_TREND_DIRNAME / MONITORING_TUNED_POLICY_FILENAME
    )


def _audit_path(path: Optional[pathlib.Path]) -> pathlib.Path:
    return path or (
        DATA_ROOT / QUALITY_TREND_DIRNAME / MONITORING_TUNE_AUDIT_FILENAME
    )


def load_tuned_overrides(path: Optional[pathlib.Path] = None) -> dict[str, float]:
    """Read the tuned-policy overlay; empty when absent or unreadable."""
    target = _overlay_path(path)
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable tuned-policy overlay %s: %s", target, exc)
        return {}
    return {
        field: float(value)
        for field, value in raw.items()
        if field in _TUNABLE_BOUNDS and isinstance(value, (int, float))
    }


def apply_tuned_overrides(
    policy: MonitoringPolicySpec,
    overrides: dict[str, float],
) -> MonitoringPolicySpec:
    """Layer the tuned overlay on the reviewed profile policy."""
    if not overrides:
        return policy
    logger.info("Applying tuned monitoring-policy overrides: %s", overrides)
    return policy.model_copy(update=overrides)


def _bounded_step(field: str, current: float, direction: float) -> float:
    """One relative step in the given direction, clamped to the field bounds."""
    step = abs(current) * MONITORING_AUTO_TUNE_MAX_STEP_PCT / 100.0
    low, high = _TUNABLE_BOUNDS[field]
    return max(low, min(high, current + direction * step))


def auto_tune_monitoring_policy(
    accuracy: dict[str, float],
    policy: MonitoringPolicySpec,
    overlay_path: Optional[pathlib.Path] = None,
    audit_path: Optional[pathlib.Path] = None,
) -> dict[str, float]:
    """One guarded adjustment from accumulated prognosis accuracy.

    Returns the new overlay (existing overrides merged with this step's).
    Conflicting signals (both rates above their alerts) skip the adjustment
    with an audit record, so oscillation never goes unrecorded.
    """
    if not accuracy:
        return {}
    fp_rate = accuracy.get("false_positive_rate", 0.0)
    fn_rate = accuracy.get("false_negative_rate", 0.0)
    fp_high = fp_rate > PROGNOSIS_FALSE_POSITIVE_ALERT
    fn_high = fn_rate > PROGNOSIS_FALSE_NEGATIVE_ALERT
    if not fp_high and not fn_high:
        return load_tuned_overrides(overlay_path)

    overrides = load_tuned_overrides(overlay_path)
    adjustments: list[dict[str, Any]] = []
    if fp_high and fn_high:
        reason = "conflicting-signals"
    else:
        # Too many withdrawals: the policy fires too eagerly -> tighten.
        # Too many escalations: it fires too late -> loosen.
        direction = -1.0 if fp_high else 1.0
        reason = "false-positives" if fp_high else "false-negatives"
        for field in sorted(_TUNABLE_BOUNDS):
            current = overrides.get(field, float(getattr(policy, field)))
            adjusted = _bounded_step(field, current, direction)
            if adjusted != current:
                overrides[field] = adjusted
                adjustments.append(
                    {"field": field, "old": round(current, 4), "new": round(adjusted, 4)}
                )

    if adjustments:
        target = _overlay_path(overlay_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(overrides, indent=2, sort_keys=True) + "\n")
        logger.warning(
            "Auto-tuned monitoring policy (%s): %s -> %s", reason, adjustments, target
        )

    record = {
        "at": datetime.now(tz=timezone.utc).isoformat(),
        "reason": reason,
        "false_positive_rate": round(fp_rate, 4),
        "false_negative_rate": round(fn_rate, 4),
        "n_observed": accuracy.get("n_observed", 0.0),
        "adjustments": adjustments,
    }
    audit_target = _audit_path(audit_path)
    try:
        audit_target.parent.mkdir(parents=True, exist_ok=True)
        with audit_target.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("Could not append tuning audit record to %s: %s", audit_target, exc)
    return overrides

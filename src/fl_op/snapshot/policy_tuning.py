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
from typing import Any, Callable, Optional

from fl_op.contracts.profile import MonitoringPolicyOverride, MonitoringPolicySpec
from fl_op.core.constants import (
    MONITORING_AUTO_TUNE_MAX_STEP_PCT,
    MONITORING_LATE_SHARE_ALERT,
    MONITORING_LEAD_TIME_MIN_SAMPLES,
    MONITORING_TUNE_AUDIT_FILENAME,
    MONITORING_TUNE_BATTERY_LOW_MAX,
    MONITORING_TUNE_BATTERY_LOW_MIN,
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

# The auto-tunable policy fields and their absolute clamps. All move the same
# way under one tuning direction: tightening (fewer false positives) lowers
# each so service fires later; loosening raises each so it fires earlier.
_TUNABLE_BOUNDS: dict[str, tuple[float, float]] = {
    "batteryForecastHorizonDays": (
        MONITORING_TUNE_HORIZON_MIN_DAYS,
        MONITORING_TUNE_HORIZON_MAX_DAYS,
    ),
    "compositeHealthThreshold": (
        MONITORING_TUNE_COMPOSITE_MIN,
        MONITORING_TUNE_COMPOSITE_MAX,
    ),
    "batteryLowThresholdPct": (
        MONITORING_TUNE_BATTERY_LOW_MIN,
        MONITORING_TUNE_BATTERY_LOW_MAX,
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


_ASSET_TYPE_OVERRIDES_KEY = "assetTypeOverrides"


def _clean_tunables(raw: Any) -> dict[str, float]:
    """Keep only known scalar tunable fields with numeric values."""
    if not isinstance(raw, dict):
        return {}
    return {
        field: float(value)
        for field, value in raw.items()
        if field in _TUNABLE_BOUNDS and isinstance(value, (int, float))
    }


def load_tuned_overrides(path: Optional[pathlib.Path] = None) -> dict[str, Any]:
    """Read the tuned-policy overlay; empty when absent or unreadable.

    Returns the global scalar tunables, plus an ``assetTypeOverrides`` map of
    per-asset-type tunables when the overlay carries any -- both filtered to
    known tunable fields so a hand-edited overlay cannot inject arbitrary keys.
    """
    target = _overlay_path(path)
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable tuned-policy overlay %s: %s", target, exc)
        return {}
    overrides: dict[str, Any] = _clean_tunables(raw)
    type_overrides = {
        asset_type: cleaned
        for asset_type, fields in (raw.get(_ASSET_TYPE_OVERRIDES_KEY) or {}).items()
        if (cleaned := _clean_tunables(fields))
    }
    if type_overrides:
        overrides[_ASSET_TYPE_OVERRIDES_KEY] = type_overrides
    return overrides


def apply_tuned_overrides(
    policy: MonitoringPolicySpec,
    overrides: dict[str, Any],
) -> MonitoringPolicySpec:
    """Layer the tuned overlay on the reviewed profile policy.

    Global scalar tunables update the base policy; per-asset-type tunables merge
    into the policy's ``assetTypeOverrides`` field-by-field, so a learned per-type
    adjustment overrides the reviewed profile's per-type defaults only for the
    tuned fields.
    """
    if not overrides:
        return policy
    logger.info("Applying tuned monitoring-policy overrides: %s", overrides)
    type_overrides = overrides.get(_ASSET_TYPE_OVERRIDES_KEY) or {}
    scalar = {k: v for k, v in overrides.items() if k != _ASSET_TYPE_OVERRIDES_KEY}
    updated = policy.model_copy(update=scalar) if scalar else policy
    if not type_overrides:
        return updated
    merged = dict(updated.assetTypeOverrides)
    for asset_type, fields in type_overrides.items():
        base = (
            merged[asset_type].model_dump(exclude_none=True)
            if asset_type in merged
            else {}
        )
        base.update(fields)
        merged[asset_type] = MonitoringPolicyOverride(**base)
    return updated.model_copy(update={_ASSET_TYPE_OVERRIDES_KEY: merged})


def _bounded_step(field: str, current: float, direction: float) -> float:
    """One relative step in the given direction, clamped to the field bounds."""
    step = abs(current) * MONITORING_AUTO_TUNE_MAX_STEP_PCT / 100.0
    low, high = _TUNABLE_BOUNDS[field]
    return max(low, min(high, current + direction * step))


def _service_lateness(
    lead_time: Optional[dict[str, float]]
) -> tuple[Optional[float], int]:
    """Service-completion late share and sample count, trusted past the minimum.

    Returns ``(None, n)`` when there are too few service completions to trust
    the distribution, so a single late visit cannot swing the policy.
    """
    if not lead_time:
        return None, 0
    n = int(lead_time.get("n_service_completions", 0) or 0)
    if n < MONITORING_LEAD_TIME_MIN_SAMPLES:
        return None, n
    share = lead_time.get("service_late_share")
    return (float(share) if share is not None else None), n


def _tuning_direction(
    fp_rate: float,
    fn_rate: float,
    late_share: Optional[float] = None,
) -> tuple[Optional[float], str]:
    """Single tuning direction from the available signals.

    A high false-positive rate (withdrawals) tightens (-1); a high false-negative
    rate (escalations) or a high service late share loosens (+1). A tighten and a
    loosen at once is ``conflicting-signals``; no alert is ``healthy``. Both
    return a None direction so the caller makes no change.
    """
    tighten = fp_rate > PROGNOSIS_FALSE_POSITIVE_ALERT
    fn_high = fn_rate > PROGNOSIS_FALSE_NEGATIVE_ALERT
    late_high = late_share is not None and late_share > MONITORING_LATE_SHARE_ALERT
    loosen = fn_high or late_high
    if tighten and loosen:
        return None, "conflicting-signals"
    if tighten:
        return -1.0, "false-positives"
    if fn_high:
        return 1.0, "false-negatives"
    if late_high:
        return 1.0, "late-service-completions"
    return None, "healthy"


def _apply_steps(
    scope: dict[str, float],
    base_value: "Callable[[str], float]",
    direction: float,
) -> list[dict[str, Any]]:
    """Step every tunable field once in ``direction``, recording each change."""
    adjustments: list[dict[str, Any]] = []
    for field in sorted(_TUNABLE_BOUNDS):
        current = scope.get(field, base_value(field))
        adjusted = _bounded_step(field, current, direction)
        if adjusted != current:
            scope[field] = adjusted
            adjustments.append(
                {"field": field, "old": round(current, 4), "new": round(adjusted, 4)}
            )
    return adjustments


def auto_tune_monitoring_policy(
    accuracy: dict[str, Any],
    policy: MonitoringPolicySpec,
    overlay_path: Optional[pathlib.Path] = None,
    audit_path: Optional[pathlib.Path] = None,
    lead_time: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """One guarded adjustment from prognosis accuracy and lead-time feedback.

    Returns the new overlay (existing overrides merged with this step's). The
    global tuning direction comes from the global false-positive/false-negative
    rates plus the service completion lead-time distribution; per-asset-type
    accuracy splits (``accuracy['by_asset_type']``) additionally tune each
    station class into the overlay's ``assetTypeOverrides``, so a single noisy
    type is corrected without disturbing the rest of the fleet. Conflicting
    signals skip that scope's adjustment with an audit record, so oscillation
    never goes unrecorded.
    """
    accuracy = accuracy or {}
    overrides = load_tuned_overrides(overlay_path)
    fp_rate = accuracy.get("false_positive_rate", 0.0)
    fn_rate = accuracy.get("false_negative_rate", 0.0)
    late_share, n_service = _service_lateness(lead_time)

    records: list[dict[str, Any]] = []
    changed = False

    g_direction, g_reason = _tuning_direction(fp_rate, fn_rate, late_share)
    if g_direction is not None or g_reason == "conflicting-signals":
        adjustments = (
            _apply_steps(overrides, lambda f: float(getattr(policy, f)), g_direction)
            if g_direction is not None
            else []
        )
        changed = changed or bool(adjustments)
        records.append(
            _audit_record(
                "global", g_reason, fp_rate, fn_rate, late_share, n_service,
                accuracy.get("n_observed", 0.0), adjustments,
            )
        )

    by_type: dict[str, dict[str, float]] = accuracy.get("by_asset_type") or {}
    type_scope: dict[str, dict[str, float]] = (
        overrides.setdefault(_ASSET_TYPE_OVERRIDES_KEY, {}) if by_type else {}
    )
    for asset_type, rates in sorted(by_type.items()):
        t_fp = rates.get("false_positive_rate", 0.0)
        t_fn = rates.get("false_negative_rate", 0.0)
        t_direction, t_reason = _tuning_direction(t_fp, t_fn)
        if t_direction is None and t_reason != "conflicting-signals":
            continue
        scope = type_scope.setdefault(asset_type, {})
        adjustments = (
            _apply_steps(
                scope,
                lambda f, at=asset_type: float(getattr(policy.for_asset_type(at), f)),
                t_direction,
            )
            if t_direction is not None
            else []
        )
        changed = changed or bool(adjustments)
        if not scope:
            type_scope.pop(asset_type, None)
        records.append(
            _audit_record(
                asset_type, t_reason, t_fp, t_fn, None, 0,
                rates.get("n_observed", 0.0), adjustments,
            )
        )

    if _ASSET_TYPE_OVERRIDES_KEY in overrides and not overrides[_ASSET_TYPE_OVERRIDES_KEY]:
        overrides.pop(_ASSET_TYPE_OVERRIDES_KEY)

    if changed:
        target = _overlay_path(overlay_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(overrides, indent=2, sort_keys=True) + "\n")
        logger.warning("Auto-tuned monitoring policy: %s -> %s", records, target)
    if records:
        _append_audit(records, audit_path)
    return overrides


def _audit_record(
    scope: str,
    reason: str,
    fp_rate: float,
    fn_rate: float,
    late_share: Optional[float],
    n_service: int,
    n_observed: float,
    adjustments: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "at": datetime.now(tz=timezone.utc).isoformat(),
        "scope": scope,
        "reason": reason,
        "false_positive_rate": round(fp_rate, 4),
        "false_negative_rate": round(fn_rate, 4),
        "service_late_share": round(late_share, 4) if late_share is not None else None,
        "n_service_completions": n_service,
        "n_observed": n_observed,
        "adjustments": adjustments,
    }


def _append_audit(
    records: list[dict[str, Any]], audit_path: Optional[pathlib.Path]
) -> None:
    audit_target = _audit_path(audit_path)
    try:
        audit_target.parent.mkdir(parents=True, exist_ok=True)
        with audit_target.open("a") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("Could not append tuning audit record to %s: %s", audit_target, exc)

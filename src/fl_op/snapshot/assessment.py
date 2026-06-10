"""Statistical assessment of observation series.

Runs between mapping and monitoring: separates sensor faults from real signals
so a single bad reading neither triggers nor suppresses a service task.

Per (entity, metric) numeric series the assessment:

- excludes outlier readings (modified z-score over the median absolute
  deviation) and records one quality finding per excluded reading;
- discriminates suspected instrument faults (battery level rising without a
  service visit, a frozen non-zero series) and floors the series confidence so
  the monitoring policy ignores it;
- detects distribution drift on non-trending metrics (mean shift between the
  series halves measured in MADs) so drifting sensors get a calibration visit;
- assigns each surviving reading a confidence the monitoring policy gates on;
- aggregates per-source-contract error rates for the snapshot quality summary.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from fl_op.canonical.common import QualityFinding
from fl_op.canonical.enums import QualitySeverity
from fl_op.canonical.observation import Observation
from fl_op.core.constants import (
    BATTERY_RISE_FAULT_PCT,
    CLOCK_SKEW_TOLERANCE_S,
    DRIFT_EXEMPT_METRICS,
    DRIFT_MAD_MULTIPLIER,
    DRIFT_MIN_SERIES_READINGS,
    FROZEN_SERIES_MIN_READINGS,
    MAD_NORMAL_CONSISTENCY,
    METRIC_BATTERY_LEVEL,
    OBSERVATION_MAX_SERIES_READINGS,
    OBSERVATION_RETENTION_DAYS,
    OUTLIER_MAD_Z_THRESHOLD,
    OUTLIER_MIN_SERIES_READINGS,
    QUALITY_FLAG_CONFIDENCE,
    SUSPECT_SERIES_CONFIDENCE,
    TIMESTAMP_REGRESSION_TOLERANCE_S,
)
from fl_op.snapshot.monitoring import observation_history, observed_ts

logger = logging.getLogger(__name__)

_RULE_OUTLIER = "dq://observation/outlier"
_RULE_SENSOR_FAULT = "dq://observation/sensor-fault"
_RULE_DRIFT = "dq://observation/metric-drift"
_RULE_SOURCE_FLAGGED = "dq://observation/source-flagged"
_RULE_FUTURE_TIMESTAMP = "dq://observation/future-timestamp"
_RULE_TIMESTAMP_REGRESSION = "dq://observation/timestamp-regression"


@dataclass
class AssessmentResult:
    """Assessed observations plus the quality artifacts derived from them."""

    observations: list[Observation] = field(default_factory=list)
    findings: list[QualityFinding] = field(default_factory=list)
    # asset_id -> metrics whose distribution drifted (calibration needed).
    drifting_metrics: dict[str, list[str]] = field(default_factory=dict)
    # source contract id -> share of bad readings (outliers + suspect series).
    error_rates: dict[str, float] = field(default_factory=dict)
    # source contract id -> newest observed-at visible to this assessment.
    source_watermarks: dict[str, datetime] = field(default_factory=dict)


def _modified_z_scores(values: np.ndarray) -> np.ndarray:
    """Modified z-scores over the median absolute deviation; zero when MAD is 0."""
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    if mad == 0.0:
        return np.zeros(len(values))
    return MAD_NORMAL_CONSISTENCY * (values - median) / mad


def _outlier_flags(values: np.ndarray) -> np.ndarray:
    if len(values) < OUTLIER_MIN_SERIES_READINGS:
        return np.zeros(len(values), dtype=bool)
    return np.abs(_modified_z_scores(values)) > OUTLIER_MAD_Z_THRESHOLD


def _fault_reason(metric: str, values: np.ndarray) -> str:
    """Suspected-instrument-fault pattern in a clean (outlier-free) series."""
    if metric == METRIC_BATTERY_LEVEL and len(values) >= 2:
        rises = np.diff(values)
        if np.any(rises > BATTERY_RISE_FAULT_PCT):
            return f"battery-rise:{float(np.max(rises)):.1f}pct"
    if len(values) >= FROZEN_SERIES_MIN_READINGS:
        # Constant zero is excluded: a dead battery legitimately reads zero.
        if np.all(values == values[0]) and values[0] != 0.0:
            return f"frozen-value:{float(values[0]):.1f}"
    return ""


def _drift_reason(metric: str, values: np.ndarray) -> str:
    """Mean shift between series halves measured in baseline MADs.

    The noise scale comes from the first half only: a step change inflates the
    pooled MAD and would mask itself. Applies to non-trending metrics only.
    """
    if metric in DRIFT_EXEMPT_METRICS or len(values) < DRIFT_MIN_SERIES_READINGS:
        return ""
    half = len(values) // 2
    baseline = values[:half]
    mad = np.median(np.abs(baseline - np.median(baseline)))
    if mad == 0.0:
        mad = np.median(np.abs(values - np.median(values)))
    if mad == 0.0:
        return ""
    shift = abs(float(np.mean(values[half:]) - np.mean(baseline)))
    if shift > DRIFT_MAD_MULTIPLIER * float(mad):
        return f"mean-shift:{shift:.1f}"
    return ""


def _series_finding(
    rule_id: str,
    severity: QualitySeverity,
    obs: Observation,
    action: str,
    detail: str,
    detected_at: datetime,
) -> QualityFinding:
    return QualityFinding(
        quality_finding_id=f"qf-obs-{obs.entity_ref}-{obs.metric}-{action}",
        rule_id=rule_id,
        severity=severity,
        entity_ref=obs.entity_ref,
        field_ref=obs.metric,
        detected_at=detected_at,
        action_applied=action,
        original_value=detail,
        planning_impact=action,
        source_ref=obs.source_ref,
    )


def _with_confidence(obs: Observation, confidence: float) -> Observation:
    combined = confidence if obs.confidence is None else min(obs.confidence, confidence)
    return obs.model_copy(update={"confidence": combined})


def _contract_of(obs: Observation) -> str:
    return obs.source_ref.split(":", 1)[0] if obs.source_ref else ""


def _flag_factor(obs: Observation) -> float:
    """Confidence factor from the source-declared quality flag (default trust)."""
    if not obs.quality_flag:
        return 1.0
    return QUALITY_FLAG_CONFIDENCE.get(obs.quality_flag.lower(), 1.0)


def _retained(series: list[Observation], as_of: Optional[datetime]) -> list[Observation]:
    """Apply the retention window and aggregate over-long series into windows.

    Readings older than OBSERVATION_RETENTION_DAYS before ``as_of`` are
    dropped; series longer than OBSERVATION_MAX_SERIES_READINGS are bucketed
    into that many equal time windows, keeping the last reading per window as
    its representative (the oldest reading is always preserved so trend rules
    keep their endpoints). A reconnecting station flushing a burst therefore
    neither bloats the snapshot nor changes the series' time span.
    """
    if as_of is not None:
        cutoff = as_of - timedelta(days=OBSERVATION_RETENTION_DAYS)
        series = [o for o in series if o.observed_at is None or observed_ts(o) >= cutoff]
    if len(series) <= OBSERVATION_MAX_SERIES_READINGS:
        return series

    start = observed_ts(series[0])
    span_s = (observed_ts(series[-1]) - start).total_seconds()
    if span_s <= 0:
        return series[-OBSERVATION_MAX_SERIES_READINGS:]
    width_s = span_s / OBSERVATION_MAX_SERIES_READINGS
    last_per_window: dict[int, Observation] = {}
    for obs in series:
        bucket = min(
            int((observed_ts(obs) - start).total_seconds() / width_s),
            OBSERVATION_MAX_SERIES_READINGS - 1,
        )
        last_per_window[bucket] = obs
    kept = [last_per_window[b] for b in sorted(last_per_window)]
    if kept[0] is not series[0]:
        kept.insert(0, series[0])
    return kept


def _skew_excluded(
    series: list[Observation], as_of: Optional[datetime]
) -> tuple[list[Observation], list[Observation]]:
    """Split off readings claiming timestamps beyond the clock-skew tolerance."""
    if as_of is None:
        return series, []
    horizon = as_of + timedelta(seconds=CLOCK_SKEW_TOLERANCE_S)
    trusted = [o for o in series if o.observed_at is None or observed_ts(o) <= horizon]
    future = [o for o in series if o.observed_at is not None and observed_ts(o) > horizon]
    return trusted, future


def _regression_detail(arrival_order: list[Observation]) -> str:
    """Largest arrival-order timestamp regression beyond the tolerance, if any."""
    worst = 0.0
    for prev, cur in zip(arrival_order, arrival_order[1:]):
        if prev.observed_at is None or cur.observed_at is None:
            continue
        regression = (observed_ts(prev) - observed_ts(cur)).total_seconds()
        worst = max(worst, regression)
    if worst > TIMESTAMP_REGRESSION_TOLERANCE_S:
        return f"regression:{worst:.0f}s"
    return ""


def assess_observations(
    observations: list[Observation],
    detected_at: datetime,
    as_of: Optional[datetime] = None,
) -> AssessmentResult:
    """Assess every numeric observation series and return the cleaned set.

    Series are first bounded by the retention window and downsampled. Readings
    flagged bad by their source are excluded (with findings), as are outliers;
    fault-suspected series keep their readings at floor confidence; categorical
    series pass through. Each surviving reading's confidence is the minimum of
    its series confidence and its source-flag factor. Findings use
    deterministic identifiers so snapshots stay reproducible.
    """
    result = AssessmentResult()
    totals: dict[str, int] = {}
    bad: dict[str, int] = {}

    arrival_order: dict[tuple[str, str], list[Observation]] = {}
    for obs in observations:
        arrival_order.setdefault((obs.entity_ref, obs.metric), []).append(obs)

    history = observation_history(observations)
    for key in sorted(history):
        entity_ref, metric = key

        regression = _regression_detail(arrival_order[key])
        if regression:
            result.findings.append(
                _series_finding(
                    _RULE_TIMESTAMP_REGRESSION,
                    QualitySeverity.WARNING,
                    history[key][-1],
                    "timestamp-regression",
                    regression,
                    detected_at,
                )
            )

        trusted, future = _skew_excluded(history[key], as_of)
        for obs in future:
            result.findings.append(
                _series_finding(
                    _RULE_FUTURE_TIMESTAMP,
                    QualitySeverity.WARNING,
                    obs,
                    f"future-timestamp-excluded-{obs.observation_id}",
                    f"observed_at:{obs.observed_at}",
                    detected_at,
                )
            )
            contract = _contract_of(obs)
            if obs.value is not None:
                totals[contract] = totals.get(contract, 0) + 1
                bad[contract] = bad.get(contract, 0) + 1
        for obs in trusted:
            if obs.observed_at is None:
                continue
            contract = _contract_of(obs)
            current = result.source_watermarks.get(contract)
            if current is None or observed_ts(obs) > current:
                result.source_watermarks[contract] = observed_ts(obs)

        series = _retained(trusted, as_of)

        flagged_bad = [o for o in series if _flag_factor(o) <= 0.0]
        for obs in flagged_bad:
            result.findings.append(
                _series_finding(
                    _RULE_SOURCE_FLAGGED,
                    QualitySeverity.INFO,
                    obs,
                    f"source-flagged-{obs.observation_id}",
                    f"quality_flag:{obs.quality_flag}",
                    detected_at,
                )
            )
            if obs.value is not None:
                contract = _contract_of(obs)
                totals[contract] = totals.get(contract, 0) + 1
                bad[contract] = bad.get(contract, 0) + 1
        series = [o for o in series if _flag_factor(o) > 0.0]

        numeric = [o for o in series if o.value is not None]
        categorical = [o for o in series if o.value is None]
        for obs in categorical:
            result.observations.append(_with_confidence(obs, _flag_factor(obs)))

        if not numeric:
            continue
        contract = _contract_of(numeric[0])
        totals[contract] = totals.get(contract, 0) + len(numeric)

        values = np.array([float(o.value) for o in numeric])
        outliers = _outlier_flags(values)
        kept = [o for o, is_out in zip(numeric, outliers) if not is_out]
        for obs, is_out in zip(numeric, outliers):
            if is_out:
                result.findings.append(
                    _series_finding(
                        _RULE_OUTLIER,
                        QualitySeverity.WARNING,
                        obs,
                        f"outlier-excluded-{obs.observation_id}",
                        f"value:{obs.value}",
                        detected_at,
                    )
                )
        n_outliers = int(np.count_nonzero(outliers))
        bad[contract] = bad.get(contract, 0) + n_outliers
        if not kept:
            continue

        clean_values = values[~outliers]
        fault = _fault_reason(metric, clean_values)
        if fault:
            confidence = SUSPECT_SERIES_CONFIDENCE
            bad[contract] = bad.get(contract, 0) + len(kept)
            result.findings.append(
                _series_finding(
                    _RULE_SENSOR_FAULT,
                    QualitySeverity.WARNING,
                    kept[-1],
                    "sensor-fault-suspected",
                    fault,
                    detected_at,
                )
            )
        else:
            confidence = 1.0 - n_outliers / len(numeric)
            drift = _drift_reason(metric, clean_values)
            if drift:
                result.drifting_metrics.setdefault(entity_ref, []).append(metric)
                result.findings.append(
                    _series_finding(
                        _RULE_DRIFT,
                        QualitySeverity.INFO,
                        kept[-1],
                        "metric-drift",
                        drift,
                        detected_at,
                    )
                )

        result.observations.extend(
            _with_confidence(o, min(confidence, _flag_factor(o))) for o in kept
        )

    result.error_rates = {
        contract: bad.get(contract, 0) / total
        for contract, total in sorted(totals.items())
        if total > 0
    }
    n_excluded = len(observations) - len(result.observations)
    if result.findings:
        logger.info(
            "Observation assessment: %d findings, %d readings excluded, error rates %s",
            len(result.findings),
            n_excluded,
            result.error_rates,
        )
    return result

"""Stationary-equipment monitoring policy.

Derives canonical service tasks for stationary assets (sensor stations, fixed
road/field equipment) from their observations and maintenance state. The
derived tasks join the snapshot's task list, so the same solver chain that
dispatches field work also routes service crews to equipment that needs a
visit.

Thresholds and task attributes come from the optimization profile's
``monitoring`` section (MonitoringPolicySpec); engine-wide defaults live in
fl_op.core.constants.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fl_op.canonical.asset import Asset
from fl_op.canonical.enums import AssetMobility, HealthStatus, TaskStatus
from fl_op.canonical.observation import Observation
from fl_op.canonical.task import Task
from fl_op.contracts.profile import MonitoringPolicySpec
from fl_op.core.constants import (
    COMPOSITE_MIN_SIGNALS,
    HEALTH_STATE_SCORES,
    METRIC_BATTERY_LEVEL,
    METRIC_HEALTH_STATUS,
)

logger = logging.getLogger(__name__)

# Semantic terms carrying maintenance master data (mapped via asset.state.*).
_LAST_SERVICE_AT_TERM = "urn:xopt:maintenance:last-service-at"
_SERVICE_INTERVAL_TERM = "urn:xopt:maintenance:service-interval"

# Health states that require a service visit.
_UNHEALTHY_STATES = {HealthStatus.DEGRADED.value, HealthStatus.FAILED.value}

# Reason prefix marking a service task that must be handled urgently because
# the asset failed earlier than the prognosis.
ESCALATED_REASON_PREFIX = "escalated:"

_SECONDS_PER_DAY = 86400.0

ObservationHistory = dict[tuple[str, str], list[Observation]]


def observed_ts(obs: Observation) -> datetime:
    """Comparable UTC timestamp of a reading; missing/naive values sort earliest."""
    if obs.observed_at is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if obs.observed_at.tzinfo is None:
        return obs.observed_at.replace(tzinfo=timezone.utc)
    return obs.observed_at


def observation_history(observations: list[Observation]) -> ObservationHistory:
    """Group observations per (entity_ref, metric), sorted oldest to newest."""
    history: ObservationHistory = {}
    for obs in observations:
        history.setdefault((obs.entity_ref, obs.metric), []).append(obs)
    for series in history.values():
        series.sort(key=observed_ts)
    return history


def latest_observations(
    observations: list[Observation],
) -> dict[tuple[str, str], Observation]:
    """Return the most recent observation per (entity_ref, metric) pair."""
    return {key: series[-1] for key, series in observation_history(observations).items()}


def _confident(obs: Observation, policy: MonitoringPolicySpec) -> bool:
    """Whether a reading is trustworthy enough for the policy to act on."""
    if obs.confidence is None:
        return True
    return obs.confidence >= policy.minObservationConfidence


def _battery_reason(
    asset: Asset, history: ObservationHistory, policy: MonitoringPolicySpec
) -> Optional[str]:
    """Battery at or below the threshold in the latest battery-level reading.

    A level at or below the critical threshold means the asset effectively
    failed before its service visit: the reason is escalated.
    """
    series = history.get((asset.asset_id, METRIC_BATTERY_LEVEL), [])
    if not series or series[-1].value is None or not _confident(series[-1], policy):
        return None
    level = series[-1].value
    if level <= policy.batteryCriticalThresholdPct:
        return f"{ESCALATED_REASON_PREFIX}battery-critical:{level:.1f}pct"
    if level <= policy.batteryLowThresholdPct:
        return f"battery-low:{level:.1f}pct"
    return None


def _battery_forecast_reason(
    asset: Asset, history: ObservationHistory, policy: MonitoringPolicySpec
) -> Optional[str]:
    """Battery drain trend projects below the threshold within the horizon.

    The drain rate is estimated from the oldest and newest readings of the
    battery series; a positive rate projected past the threshold within
    ``batteryForecastHorizonDays`` derives a service task before the battery
    actually dies.
    """
    series = [
        o
        for o in history.get((asset.asset_id, METRIC_BATTERY_LEVEL), [])
        if o.value is not None and _confident(o, policy)
    ]
    if len(series) < 2:
        return None
    first, last = series[0], series[-1]
    span_days = (observed_ts(last) - observed_ts(first)).total_seconds() / _SECONDS_PER_DAY
    if span_days <= 0:
        return None
    drain_per_day = (first.value - last.value) / span_days
    if drain_per_day <= 0:
        return None
    projected = last.value - drain_per_day * policy.batteryForecastHorizonDays
    if projected <= policy.batteryLowThresholdPct:
        return (
            f"battery-forecast:{projected:.1f}pct"
            f"-in-{policy.batteryForecastHorizonDays:.0f}d"
        )
    return None


def _health_reason(
    asset: Asset, history: ObservationHistory, policy: MonitoringPolicySpec
) -> Optional[str]:
    """Unhealthy state from the latest health-status reading.

    A failed asset is past prognosis: the reason is escalated.
    """
    series = history.get((asset.asset_id, METRIC_HEALTH_STATUS), [])
    if not series or not _confident(series[-1], policy):
        return None
    state = series[-1].state_value.lower()
    if state == HealthStatus.FAILED.value:
        return f"{ESCALATED_REASON_PREFIX}health:{state}"
    if state in _UNHEALTHY_STATES:
        return f"health:{state}"
    return None


def _service_due(asset: Asset) -> Optional[datetime]:
    """Next planned service due date from the asset's maintenance master data."""
    last_raw = asset.capability_value(_LAST_SERVICE_AT_TERM)
    interval_days = asset.capability_value(_SERVICE_INTERVAL_TERM)
    if last_raw is None or interval_days is None:
        return None
    try:
        last = datetime.fromisoformat(str(last_raw).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Asset %s has unparseable last-service-at: %r", asset.asset_id, last_raw)
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last + timedelta(days=float(interval_days))


def _service_overdue_reason(asset: Asset, now: datetime) -> Optional[str]:
    """Planned service interval exceeded since the last completed visit."""
    due = _service_due(asset)
    if due is not None and due <= now:
        return f"service-overdue:due-{due.date().isoformat()}"
    return None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _composite_reason(
    asset: Asset,
    history: ObservationHistory,
    policy: MonitoringPolicySpec,
    now: datetime,
    drifting_metrics: list[str],
) -> Optional[str]:
    """Weighted health score combining partial signals below any single rule.

    Each available signal contributes a subscore in [0, 1] (1 = healthy):
    battery headroom above the low threshold, the latest health state, time
    remaining until the planned service date, and drifting metrics. A service
    task is derived when the weighted score falls below the policy threshold
    and at least COMPOSITE_MIN_SIGNALS signals are available (single-signal
    cases belong to the individual rules).
    """
    signals: list[tuple[float, float]] = []

    battery_series = history.get((asset.asset_id, METRIC_BATTERY_LEVEL), [])
    if battery_series and battery_series[-1].value is not None and _confident(
        battery_series[-1], policy
    ):
        headroom = (
            battery_series[-1].value - policy.batteryLowThresholdPct
        ) / policy.compositeBatteryHeadroomPct
        signals.append((policy.compositeWeightBattery, _clamp01(headroom)))

    health_series = history.get((asset.asset_id, METRIC_HEALTH_STATUS), [])
    if health_series and health_series[-1].state_value and _confident(
        health_series[-1], policy
    ):
        state = health_series[-1].state_value.lower()
        score = HEALTH_STATE_SCORES.get(state, HEALTH_STATE_SCORES["unknown"])
        signals.append((policy.compositeWeightHealth, score))

    due = _service_due(asset)
    if due is not None:
        days_until_due = (due - now).total_seconds() / _SECONDS_PER_DAY
        signals.append(
            (
                policy.compositeWeightService,
                _clamp01(days_until_due / policy.compositeServiceHeadroomDays),
            )
        )

    if drifting_metrics:
        signals.append((policy.compositeWeightDrift, 0.0))

    if len(signals) < COMPOSITE_MIN_SIGNALS:
        return None
    score = sum(w * s for w, s in signals) / sum(w for w, _ in signals)
    if score < policy.compositeHealthThreshold:
        return f"composite-health:{score:.2f}"
    return None


def derive_service_tasks(
    assets: list[Asset],
    observations: list[Observation],
    now: datetime,
    policy: Optional[MonitoringPolicySpec] = None,
    calibration_needs: Optional[dict[str, list[str]]] = None,
) -> list[Task]:
    """Derive one service task per asset that needs attention.

    Stationary equipment is always covered; mobile assets (prime movers, drones)
    are covered when the effective policy sets ``monitorMobileAssets`` (globally
    or per asset type). An asset needs a service visit when any rule fires:
    battery at or below threshold, battery drain trend projected below threshold
    within the
    forecast horizon, unhealthy status, planned service interval exceeded, a
    drifting metric reported by the observation assessment
    (``calibration_needs``: asset id -> drifting metrics), or a composite
    health score combining partial signals that individually stay below their
    rule thresholds. Readings below the policy's minimum confidence are
    ignored, so fault-suspected series never derive tasks. Policies resolve
    per asset type via the profile's ``assetTypeOverrides`` and per asset
    instance via ``assetOverrides`` (a single critical station). The task is
    anchored at the asset's home location reference so the solver can route a
    service-capable crew there; assets without an anchor location are reported
    and skipped.
    """
    policy = policy or MonitoringPolicySpec()
    calibration_needs = calibration_needs or {}
    history = observation_history(observations)
    tasks: list[Task] = []
    for asset in assets:
        effective = policy.for_asset(asset.asset_type, asset.asset_id)
        # Stationary equipment is always monitored; mobile assets (prime movers,
        # drones) only when the effective policy opts in, so predictive
        # maintenance can extend to the fleet without disturbing domains that
        # only monitor fixed equipment.
        if (
            asset.mobility != AssetMobility.STATIONARY.value
            and not effective.monitorMobileAssets
        ):
            continue
        drifting = calibration_needs.get(asset.asset_id, [])
        battery_now = _battery_reason(asset, history, effective)
        reasons = [
            r
            for r in (
                battery_now,
                None if battery_now else _battery_forecast_reason(asset, history, effective),
                _health_reason(asset, history, effective),
                _service_overdue_reason(asset, now),
                *(f"calibration:{metric}-drift" for metric in drifting),
            )
            if r is not None
        ]
        if not reasons:
            composite = _composite_reason(asset, history, effective, now, drifting)
            if composite is not None:
                reasons = [composite]
        if not reasons:
            continue
        if not asset.home_depot_ref:
            logger.warning(
                "Asset %s needs service (%s) but has no anchor location; skipped",
                asset.asset_id,
                ",".join(reasons),
            )
            continue
        escalated = any(r.startswith(ESCALATED_REASON_PREFIX) for r in reasons)
        deadline_days = (
            effective.escalatedDeadlineDays if escalated else effective.serviceDeadlineDays
        )
        priority = (
            effective.escalatedPriorityClass if escalated else effective.servicePriorityClass
        )
        tasks.append(
            Task(
                task_id=f"service-{asset.asset_id}",
                order_id=f"monitoring-{asset.asset_id}",
                operation_type=effective.serviceOperationType,
                location_ref=asset.home_depot_ref,
                area_ha=effective.serviceNominalAreaHa,
                service_duration_minutes=int(effective.serviceDurationMinutes),
                deadline=now + timedelta(days=deadline_days),
                priority_class=priority,
                penalty_per_day_eur=effective.servicePenaltyPerDayEur,
                status=TaskStatus.PENDING.value,
                source_ref=f"monitoring:{asset.asset_id}:{','.join(reasons)}",
            )
        )
    if tasks:
        logger.info("Monitoring derived %d service tasks", len(tasks))
    return tasks

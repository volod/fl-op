"""Charging-station scheduling and charging-queue capacity for hub fleets.

A post-solve pass over a solved plan that schedules the recharge each used asset
needs into its home hub's parallel charging bays. The routing solver consumes
hub battery *inventory* but does not model the *throughput* limit of recharging:
a hub has a bounded number of charging bays (``charging_slots``) sharing an
aggregate charger power (``charging_power_kw``), so when more homed assets return
to recharge than there are bays, sessions queue.

For each used prime mover the pass estimates the energy spent over its on-plan
busy hours (consumption rate x busy time, capped at battery capacity), then
schedules that recharge at the asset's home hub: a session arrives when the asset
finishes its last assignment, takes the earliest-free bay, and waits when every
bay is busy. Per-bay power is the hub's aggregate power split across its bays, so
the charge duration follows from the energy to replenish. The result is a
machine-readable charging schedule with per-hub utilisation and queue-wait KPIs.

The pass is deterministic and side-effect free.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fl_op.core.constants import (
    CHARGING_DEFAULT_POWER_KW,
    CHARGING_DEFAULT_SLOTS,
    CHARGING_MIN_SESSION_KWH,
    CHARGING_TURNAROUND_RISK_S,
)

if TYPE_CHECKING:
    from fl_op.canonical.plan import Assignment
    from fl_op.canonical.snapshot import PlanningSnapshot

# Key the charging schedule is embedded under inside the drone KPI block.
CHARGING_SCHEDULE_KEY = "charging_schedule"

_ENERGY_RATE_TERM = "urn:xopt:capability:energy-consumption-rate"
_ENERGY_CAPACITY_TERM = "urn:xopt:capability:energy-capacity"
_SECONDS_PER_HOUR = 3600.0


@dataclass
class _Session:
    """One asset's recharge demand and its scheduled bay occupancy."""

    asset_id: str
    hub_id: str
    energy_kwh: float
    arrival_s: float
    duration_s: float
    slot: int = -1
    charge_start_s: float = 0.0
    charge_end_s: float = 0.0

    @property
    def wait_s(self) -> float:
        return max(0.0, self.charge_start_s - self.arrival_s)

    @property
    def turnaround_s(self) -> float:
        """Total downtime: queue wait plus charge time (arrival -> ready)."""
        return max(0.0, self.charge_end_s - self.arrival_s)

    @property
    def at_turnaround_risk(self) -> bool:
        return self.turnaround_s > CHARGING_TURNAROUND_RISK_S


def build_charging_schedule(
    snapshot: "PlanningSnapshot",
    assignments: list["Assignment"],
) -> dict[str, Any]:
    """Schedule post-plan recharges into hub charging bays.

    Returns an empty dict when no used asset needs a non-negligible recharge, so
    the caller can embed the result unconditionally.
    """
    asset_by_id = {asset.asset_id: asset for asset in snapshot.assets}
    hub_by_id = {
        loc.location_id: loc
        for loc in snapshot.locations
        if loc.location_type == "depot"
    }

    demands = _energy_demands(assignments, asset_by_id)
    sessions = [
        _Session(
            asset_id=asset_id,
            hub_id=hub_id,
            energy_kwh=round(energy_kwh, 3),
            arrival_s=arrival_s,
            duration_s=0.0,
        )
        for (asset_id, hub_id, energy_kwh, arrival_s) in demands
        if hub_id in hub_by_id and energy_kwh >= CHARGING_MIN_SESSION_KWH
    ]
    if not sessions:
        return {}

    per_hub: dict[str, list[_Session]] = {}
    for session in sessions:
        per_hub.setdefault(session.hub_id, []).append(session)

    hub_reports: dict[str, Any] = {}
    for hub_id, hub_sessions in per_hub.items():
        hub_reports[hub_id] = _schedule_hub(hub_by_id[hub_id], hub_sessions)

    return _summarize(sessions, hub_reports)


def _energy_demands(
    assignments: list["Assignment"],
    asset_by_id: dict[str, Any],
) -> list[tuple[str, str, float, float]]:
    """Per-asset (asset_id, hub_id, energy_kwh, arrival_epoch) recharge demand."""
    busy_s: dict[str, float] = {}
    arrival_s: dict[str, float] = {}
    for assignment in assignments:
        if not assignment.asset_ids:
            continue
        asset_id = assignment.asset_ids[0]
        start = _epoch(assignment.planned_start)
        end = _epoch(assignment.planned_finish)
        busy_s[asset_id] = busy_s.get(asset_id, 0.0) + max(0.0, end - start)
        arrival_s[asset_id] = max(arrival_s.get(asset_id, end), end)

    demands: list[tuple[str, str, float, float]] = []
    for asset_id, busy in busy_s.items():
        asset = asset_by_id.get(asset_id)
        if asset is None or asset.home_depot_ref is None:
            continue
        rate = _capability_float(asset, _ENERGY_RATE_TERM)
        if rate <= 0.0:
            continue
        energy = rate * (busy / _SECONDS_PER_HOUR)
        capacity = _capability_float(asset, _ENERGY_CAPACITY_TERM)
        if capacity > 0.0:
            energy = min(energy, capacity)
        demands.append(
            (asset_id, str(asset.home_depot_ref), energy, arrival_s[asset_id])
        )
    return demands


def _schedule_hub(hub: Any, sessions: list[_Session]) -> dict[str, Any]:
    """Assign one hub's sessions to its bays in arrival order; record waits."""
    slots = max(1, int(hub.charging_slots or CHARGING_DEFAULT_SLOTS))
    aggregate_power = float(hub.charging_power_kw or CHARGING_DEFAULT_POWER_KW)
    per_slot_power = aggregate_power / slots if aggregate_power > 0 else 0.0

    ordered = sorted(sessions, key=lambda s: (s.arrival_s, s.asset_id))
    slot_free = [float("-inf")] * slots
    for session in ordered:
        if per_slot_power > 0:
            session.duration_s = (session.energy_kwh / per_slot_power) * (
                _SECONDS_PER_HOUR
            )
        slot = min(range(slots), key=lambda idx: (slot_free[idx], idx))
        session.slot = slot
        session.charge_start_s = max(session.arrival_s, slot_free[slot])
        session.charge_end_s = session.charge_start_s + session.duration_s
        slot_free[slot] = session.charge_end_s

    busy_s = sum(s.duration_s for s in ordered)
    waits = [s.wait_s for s in ordered]
    turnarounds = [s.turnaround_s for s in ordered]
    starts = [s.charge_start_s for s in ordered]
    ends = [s.charge_end_s for s in ordered]
    window_s = max(ends) - min(starts) if ordered else 0.0
    utilization = (
        busy_s / (slots * window_s) * 100.0 if window_s > 0 else 0.0
    )
    return {
        "slots": slots,
        "charging_power_kw": round(aggregate_power, 2),
        "per_slot_power_kw": round(per_slot_power, 2),
        "n_sessions": len(ordered),
        "energy_kwh": round(sum(s.energy_kwh for s in ordered), 2),
        "total_charge_time_s": round(busy_s, 1),
        "total_queue_wait_s": round(sum(waits), 1),
        "max_queue_wait_s": round(max(waits), 1) if waits else 0.0,
        "n_queued_sessions": sum(1 for w in waits if w > 0.0),
        "peak_queue_depth": _peak_queue_depth(ordered),
        "max_turnaround_s": round(max(turnarounds), 1) if turnarounds else 0.0,
        "n_turnaround_at_risk": sum(1 for s in ordered if s.at_turnaround_risk),
        "utilization_pct": round(utilization, 2),
    }


def _summarize(
    sessions: list[_Session], hub_reports: dict[str, Any]
) -> dict[str, Any]:
    waits = [s.wait_s for s in sessions]
    turnarounds = [s.turnaround_s for s in sessions]
    n_queued = sum(1 for w in waits if w > 0.0)
    return {
        "n_charging_sessions": len(sessions),
        "n_hubs_with_charging": len(hub_reports),
        "total_energy_charged_kwh": round(
            sum(s.energy_kwh for s in sessions), 2
        ),
        "total_queue_wait_s": round(sum(waits), 1),
        "max_queue_wait_s": round(max(waits), 1) if waits else 0.0,
        "mean_queue_wait_s": round(sum(waits) / len(waits), 1) if waits else 0.0,
        "n_queued_sessions": n_queued,
        "queued_share_pct": round(n_queued / len(sessions) * 100.0, 2),
        "max_concurrent_charging": _max_concurrent(sessions),
        "peak_queue_depth": max(
            (report["peak_queue_depth"] for report in hub_reports.values()),
            default=0,
        ),
        "max_turnaround_s": round(max(turnarounds), 1) if turnarounds else 0.0,
        "mean_turnaround_s": (
            round(sum(turnarounds) / len(turnarounds), 1) if turnarounds else 0.0
        ),
        "n_turnaround_at_risk": sum(1 for s in sessions if s.at_turnaround_risk),
        "hub_utilization": dict(sorted(hub_reports.items())),
        "sessions": [
            {
                "asset_id": s.asset_id,
                "hub_id": s.hub_id,
                "slot": s.slot,
                "energy_kwh": s.energy_kwh,
                "wait_s": round(s.wait_s, 1),
                "charge_time_s": round(s.duration_s, 1),
                "turnaround_s": round(s.turnaround_s, 1),
                "ready_at_s": round(s.charge_end_s, 1),
            }
            for s in sorted(sessions, key=lambda s: (s.hub_id, s.arrival_s))
        ],
    }


def _peak_queue_depth(sessions: list[_Session]) -> int:
    """Maximum number of sessions waiting for a bay at one hub at any instant.

    Each waiting session occupies the half-open interval ``[arrival,
    charge_start)``; a session that starts on arrival never waits and is
    excluded. Counting the peak overlap of those intervals (ends settled before
    starts at a tie) gives the deepest the charging queue ever gets.
    """
    events: list[tuple[float, int]] = []
    for session in sessions:
        if session.charge_start_s <= session.arrival_s:
            continue
        events.append((session.arrival_s, 1))
        events.append((session.charge_start_s, -1))
    events.sort(key=lambda e: (e[0], e[1]))
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def _max_concurrent(sessions: list[_Session]) -> int:
    events: list[tuple[float, int]] = []
    for session in sessions:
        events.append((session.charge_start_s, 1))
        events.append((session.charge_end_s, -1))
    events.sort(key=lambda e: (e[0], e[1]))
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def _capability_float(asset: Any, semantic_term: str) -> float:
    value = asset.capability_value(semantic_term)
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _epoch(value: datetime) -> float:
    try:
        return value.timestamp()
    except (AttributeError, TypeError, ValueError):
        return 0.0

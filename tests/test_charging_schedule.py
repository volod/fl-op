"""Unit coverage for hub charging-queue scheduling."""

from datetime import datetime, timedelta, timezone

from fl_op.canonical.asset import Asset, Capability, GeoLocation
from fl_op.canonical.location import Location
from fl_op.canonical.plan import Assignment
from fl_op.planning.charging import build_charging_schedule

_T0 = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
_RATE_TERM = "urn:xopt:capability:energy-consumption-rate"
_CAP_TERM = "urn:xopt:capability:energy-capacity"


class _Snap:
    def __init__(self, assets, locations):
        self.assets = assets
        self.locations = locations


def _hub(hub_id: str, slots: int, power_kw: float) -> Location:
    return Location(
        location_id=hub_id,
        location_type="depot",
        lat=50.45,
        lon=30.52,
        charging_power_kw=power_kw,
        charging_slots=slots,
    )


def _asset(asset_id: str, hub: str, rate_kwh_h: float, capacity_kwh: float = 1000.0) -> Asset:
    return Asset(
        asset_id=asset_id,
        asset_type="UAV",
        roles=["mobile-prime-mover"],
        home_depot_ref=hub,
        location=GeoLocation(lat=50.45, lon=30.52),
        capabilities=[
            Capability(
                capability_id="rate",
                semantic_term=_RATE_TERM,
                value=rate_kwh_h,
                canonical_unit="kWh/h",
            ),
            Capability(
                capability_id="cap",
                semantic_term=_CAP_TERM,
                value=capacity_kwh,
                canonical_unit="kWh",
            ),
        ],
    )


def _assignment(task_id: str, asset_id: str, start_min: float, dur_min: float) -> Assignment:
    start = _T0 + timedelta(minutes=start_min)
    return Assignment(
        assignment_id=f"a-{task_id}",
        task_id=task_id,
        bundle_id="b",
        asset_ids=[asset_id],
        planned_start=start,
        planned_finish=start + timedelta(minutes=dur_min),
    )


def test_no_demand_returns_empty():
    hub = _hub("h", 2, 100.0)
    # Idle asset (zero busy time) produces no charging session.
    asset = _asset("UAV_0", "h", 30.0)
    snap = _Snap([asset], [hub])
    assert build_charging_schedule(snap, []) == {}


def test_single_session_no_queue():
    hub = _hub("h", 2, 120.0)  # 60 kW per slot
    asset = _asset("UAV_0", "h", 30.0)  # 30 kWh/h
    snap = _Snap([asset], [hub])
    # 2 h busy -> 60 kWh -> 60/60 kW = 1 h = 3600 s charge.
    assignments = [_assignment("t0", "UAV_0", 0.0, 120.0)]
    sched = build_charging_schedule(snap, assignments)
    assert sched["n_charging_sessions"] == 1
    assert sched["total_energy_charged_kwh"] == 60.0
    assert sched["n_queued_sessions"] == 0
    session = sched["sessions"][0]
    assert session["charge_time_s"] == 3600.0
    assert session["wait_s"] == 0.0


def test_single_bay_forces_queue_wait():
    """Two assets homed at a 1-bay hub, arriving together: the second waits."""
    hub = _hub("h", 1, 60.0)  # single bay, 60 kW
    assets = [_asset("AAA", "h", 60.0), _asset("BBB", "h", 60.0)]
    snap = _Snap(assets, [hub])
    # Both finish at +60 min. AAA: 60 kWh -> 3600 s. BBB: 60 kWh -> 3600 s.
    assignments = [
        _assignment("ta", "AAA", 0.0, 60.0),
        _assignment("tb", "BBB", 0.0, 60.0),
    ]
    sched = build_charging_schedule(snap, assignments)
    assert sched["n_charging_sessions"] == 2
    assert sched["n_queued_sessions"] == 1
    assert sched["peak_queue_depth"] == 1
    # The earlier-sorted asset (AAA) charges first for 3600 s; BBB waits that long.
    hub_report = sched["hub_utilization"]["h"]
    assert hub_report["slots"] == 1
    assert hub_report["max_queue_wait_s"] == 3600.0
    waits = {s["asset_id"]: s["wait_s"] for s in sched["sessions"]}
    assert waits["AAA"] == 0.0
    assert waits["BBB"] == 3600.0


def test_turnaround_risk_flags_long_downtime():
    """Queue wait plus charge time beyond the risk threshold flags turnaround risk."""
    hub = _hub("h", 1, 60.0)  # single 60 kW bay
    assets = [_asset("AAA", "h", 60.0), _asset("BBB", "h", 60.0)]
    snap = _Snap(assets, [hub])
    # Each needs 120 kWh -> 7200 s charge. BBB waits 7200 s then charges 7200 s,
    # so its turnaround is 14400 s (> the 7200 s risk threshold); AAA is 7200 s.
    assignments = [
        _assignment("ta", "AAA", 0.0, 120.0),
        _assignment("tb", "BBB", 0.0, 120.0),
    ]
    sched = build_charging_schedule(snap, assignments)
    turnarounds = {s["asset_id"]: s["turnaround_s"] for s in sched["sessions"]}
    assert turnarounds["AAA"] == 7200.0
    assert turnarounds["BBB"] == 14400.0
    assert sched["max_turnaround_s"] == 14400.0
    assert sched["n_turnaround_at_risk"] == 1
    ready = {s["asset_id"]: s["ready_at_s"] for s in sched["sessions"]}
    assert ready["BBB"] > ready["AAA"]


def test_two_bays_absorb_two_sessions_without_wait():
    hub = _hub("h", 2, 120.0)
    assets = [_asset("AAA", "h", 60.0), _asset("BBB", "h", 60.0)]
    snap = _Snap(assets, [hub])
    assignments = [
        _assignment("ta", "AAA", 0.0, 60.0),
        _assignment("tb", "BBB", 0.0, 60.0),
    ]
    sched = build_charging_schedule(snap, assignments)
    assert sched["n_queued_sessions"] == 0
    assert sched["max_concurrent_charging"] == 2
    assert sched["peak_queue_depth"] == 0


def test_energy_capped_at_battery_capacity():
    hub = _hub("h", 2, 100.0)
    # Rate x busy would be 300 kWh, but the battery only holds 50 kWh.
    asset = _asset("UAV_0", "h", 100.0, capacity_kwh=50.0)
    snap = _Snap([asset], [hub])
    assignments = [_assignment("t0", "UAV_0", 0.0, 180.0)]
    sched = build_charging_schedule(snap, assignments)
    assert sched["total_energy_charged_kwh"] == 50.0


def test_default_slots_when_hub_unspecified():
    hub = Location(location_id="h", location_type="depot", lat=50.0, lon=30.0)
    asset = _asset("UAV_0", "h", 30.0)
    snap = _Snap([asset], [hub])
    assignments = [_assignment("t0", "UAV_0", 0.0, 60.0)]
    sched = build_charging_schedule(snap, assignments)
    from fl_op.core.constants import CHARGING_DEFAULT_SLOTS

    assert sched["hub_utilization"]["h"]["slots"] == CHARGING_DEFAULT_SLOTS

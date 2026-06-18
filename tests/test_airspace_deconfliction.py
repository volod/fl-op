"""Unit coverage for 3D airspace deconfliction of aerial flights."""

from datetime import datetime, timedelta, timezone

from fl_op.canonical.asset import Asset, Capability, GeoLocation
from fl_op.canonical.location import Location
from fl_op.canonical.plan import Assignment
from fl_op.canonical.task import Task
from fl_op.planning import airspace
from fl_op.planning.airspace import (
    apply_airspace_holds,
    build_airspace_plan,
    deconflict_airspace,
)

_T0 = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
_HUB = "hub_000"


class _Snap:
    """Minimal snapshot stand-in exposing what the airspace pass reads."""

    def __init__(self, assets, locations, tasks):
        self.assets = assets
        self.locations = locations
        self.tasks = tasks

    def task_index(self):
        return {t.task_id: t for t in self.tasks}


def _location(loc_id: str, lat: float, lon: float, loc_type: str = "field") -> Location:
    return Location(location_id=loc_id, location_type=loc_type, lat=lat, lon=lon)


def _uav(asset_id: str, home: str = _HUB) -> Asset:
    return Asset(
        asset_id=asset_id,
        asset_type="UAV",
        roles=["mobile-prime-mover"],
        home_depot_ref=home,
        location=GeoLocation(lat=50.45, lon=30.52),
    )


def _task(
    task_id: str,
    drop_ref: str,
    pickup_ref: str = _HUB,
    deadline_min: float | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        order_id=task_id,
        operation_type="UAV_DELIVERY",
        location_ref=drop_ref,
        pickup_location_ref=pickup_ref,
        deadline=(
            _T0 + timedelta(minutes=deadline_min) if deadline_min is not None else None
        ),
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


def _clique_snapshot(n: int, deadline_min: float | None = None):
    """n aerial flights from one shared hub, all time-overlapping (a conflict clique).

    With ``deadline_min`` set to the flights' finish time (20 min) every flight
    has zero deadline slack, so the temporal-separation pass cannot hold any of
    them and same-corridor conflicts stay residual; left as ``None`` (unbounded
    slack) the pass can always time-separate them.
    """
    hub = _location(_HUB, 50.45, 30.52, "depot")
    locations = [hub]
    assets = []
    tasks = []
    assignments = []
    for i in range(n):
        # Drop-offs fan out but every path starts at the shared hub, so all
        # pairs are laterally coincident at the origin (distance 0) -> conflict.
        drop_id = f"drop_{i}"
        locations.append(_location(drop_id, 50.46 + 0.001 * i, 30.53 + 0.001 * i))
        assets.append(_uav(f"UAV_{i:02d}"))
        tasks.append(_task(f"flight_{i}", drop_id, deadline_min=deadline_min))
        assignments.append(_assignment(f"flight_{i}", f"UAV_{i:02d}", 0.0, 20.0))
    return _Snap(assets, locations, tasks), assignments


def test_no_aerial_flights_returns_empty():
    hub = _location(_HUB, 50.45, 30.52, "depot")
    snap = _Snap([], [hub], [])
    assert build_airspace_plan(snap, []) == {}


def test_separated_flights_have_no_conflict():
    """Two flights far apart in space stay in one corridor with zero conflicts."""
    hub_a = _location("hub_a", 50.0, 30.0, "depot")
    hub_b = _location("hub_b", 51.0, 31.0, "depot")
    drop_a = _location("drop_a", 50.01, 30.01)
    drop_b = _location("drop_b", 51.01, 31.01)
    assets = [_uav("UAV_A", "hub_a"), _uav("UAV_B", "hub_b")]
    tasks = [_task("fa", "drop_a", "hub_a"), _task("fb", "drop_b", "hub_b")]
    snap = _Snap(assets, [hub_a, hub_b, drop_a, drop_b], tasks)
    assignments = [
        _assignment("fa", "UAV_A", 0.0, 20.0),
        _assignment("fb", "UAV_B", 0.0, 20.0),
    ]
    plan = build_airspace_plan(snap, assignments)
    assert plan["n_aerial_flights"] == 2
    assert plan["n_conflict_pairs"] == 0
    assert plan["max_concurrent_flights"] == 2
    assert plan["fully_deconflicted"] is True


def test_time_disjoint_flights_do_not_conflict():
    """Same path but non-overlapping windows: no separation needed."""
    snap, _ = _clique_snapshot(2)
    # Override to make the two flights sequential in time.
    assignments = [
        _assignment("flight_0", "UAV_00", 0.0, 20.0),
        _assignment("flight_1", "UAV_01", 120.0, 20.0),
    ]
    plan = build_airspace_plan(snap, assignments)
    assert plan["n_conflict_pairs"] == 0
    assert plan["max_concurrent_flights"] == 1


def test_overlapping_conflict_is_separated_into_corridors():
    """Two overlapping co-located flights get distinct altitude corridors."""
    snap, assignments = _clique_snapshot(2)
    plan = build_airspace_plan(snap, assignments)
    assert plan["n_conflict_pairs"] == 1
    assert plan["n_deconflicted_pairs"] == 1
    assert plan["n_residual_conflict_pairs"] == 0
    corridors = {flight["corridor"] for flight in plan["flights"]}
    assert len(corridors) == 2
    altitudes = {flight["altitude_m"] for flight in plan["flights"]}
    assert len(altitudes) == 2
    assert plan["fully_deconflicted"] is True


def test_corridor_exhaustion_with_no_slack_leaves_residual(monkeypatch):
    """A clique over the corridor budget with zero deadline slack stays residual."""
    monkeypatch.setattr(airspace, "AIRSPACE_CORRIDOR_COUNT", 2)
    # deadline == finish (20 min) => no slack, so temporal separation cannot help.
    snap, assignments = _clique_snapshot(3, deadline_min=20.0)
    plan = build_airspace_plan(snap, assignments)
    # A 3-clique needs 3 corridors; only 2 are available.
    assert plan["n_conflict_pairs"] == 3
    assert plan["corridors_available"] == 2
    assert plan["corridors_used"] == 2
    assert plan["n_residual_conflict_pairs"] >= 1
    assert plan["n_flights_held"] == 0
    assert plan["fully_deconflicted"] is False
    assert plan["n_deconflicted_pairs"] + plan["n_residual_conflict_pairs"] == 3


def test_temporal_separation_resolves_same_corridor_conflict(monkeypatch):
    """With one corridor and slack, overlapping flights are held apart in time."""
    monkeypatch.setattr(airspace, "AIRSPACE_CORRIDOR_COUNT", 1)
    snap, assignments = _clique_snapshot(2)  # unbounded slack (no deadline)
    plan = build_airspace_plan(snap, assignments)
    # Both forced into the single corridor, then deconflicted in time.
    assert plan["corridors_used"] == 1
    assert plan["n_conflict_pairs"] == 1
    assert plan["n_time_separated_pairs"] == 1
    assert plan["n_residual_conflict_pairs"] == 0
    assert plan["n_flights_held"] == 1
    assert plan["max_deconfliction_delay_s"] > 0.0
    assert plan["fully_deconflicted"] is True


def test_temporal_separation_bounded_by_deadline(monkeypatch):
    """A one-corridor conflict with zero slack cannot be timed apart -> residual."""
    monkeypatch.setattr(airspace, "AIRSPACE_CORRIDOR_COUNT", 1)
    snap, assignments = _clique_snapshot(2, deadline_min=20.0)
    plan = build_airspace_plan(snap, assignments)
    assert plan["n_conflict_pairs"] == 1
    assert plan["n_time_separated_pairs"] == 0
    assert plan["n_residual_conflict_pairs"] == 1
    assert plan["n_flights_held"] == 0
    assert plan["fully_deconflicted"] is False


def test_inbound_travel_extends_airborne_window():
    """A travel-speed asset is airborne before service-start, widening conflicts."""
    hub = _location(_HUB, 50.45, 30.52, "depot")
    far_drop = _location("drop_far", 50.62, 30.70)  # ~20 km from the hub
    near_drop = _location("drop_near", 50.452, 30.522)
    # Slow UAV so the inbound transit is many minutes.
    slow = Asset(
        asset_id="UAV_SLOW",
        asset_type="UAV",
        roles=["mobile-prime-mover"],
        home_depot_ref=_HUB,
        location=GeoLocation(lat=50.45, lon=30.52),
        capabilities=[
            Capability(
                capability_id="speed",
                semantic_term="urn:xopt:capability:travel-speed",
                value=30.0,
                canonical_unit="km/h",
            )
        ],
    )
    other = _uav("UAV_FAST")
    tasks = [_task("f_far", "drop_far"), _task("f_near", "drop_near")]
    snap = _Snap([slow, other], [hub, far_drop, near_drop], tasks)
    # f_far serves later than f_near finishes, so without the inbound-travel
    # window they would not overlap; the long transit makes them overlap.
    assignments = [
        _assignment("f_far", "UAV_SLOW", 30.0, 5.0),
        _assignment("f_near", "UAV_FAST", 0.0, 10.0),
    ]
    plan = build_airspace_plan(snap, assignments)
    assert plan["n_conflict_pairs"] == 1


def test_altitudes_are_vertically_separated():
    snap, assignments = _clique_snapshot(2)
    plan = build_airspace_plan(snap, assignments)
    alts = sorted(plan["corridor_altitudes_m"].values())
    assert alts[0] == airspace.AIRSPACE_BASE_ALTITUDE_M
    assert alts[1] - alts[0] == airspace.AIRSPACE_VERTICAL_SEPARATION_M


def test_holds_are_applied_to_dispatch(monkeypatch):
    """A temporal hold re-times the held flight's assignment, not the other."""
    monkeypatch.setattr(airspace, "AIRSPACE_CORRIDOR_COUNT", 1)
    snap, assignments = _clique_snapshot(2)  # unbounded slack
    result = deconflict_airspace(snap, assignments)
    assert result.holds  # the later flight is held
    held = apply_airspace_holds(assignments, result.holds)
    original = {a.task_id: a for a in assignments}
    shifted = [a for a in held if a.planned_start != original[a.task_id].planned_start]
    assert len(shifted) == 1
    held_task = next(iter(result.holds))
    moved = next(a for a in held if a.task_id == held_task)
    delay = result.holds[held_task]
    assert (
        moved.planned_start - original[held_task].planned_start
    ).total_seconds() == delay
    # The held window keeps its duration (start and finish move together).
    assert (moved.planned_finish - moved.planned_start) == (
        original[held_task].planned_finish - original[held_task].planned_start
    )


def test_frozen_flight_is_never_held(monkeypatch):
    """A frozen flight cannot be re-timed, so its conflict stays residual."""
    monkeypatch.setattr(airspace, "AIRSPACE_CORRIDOR_COUNT", 1)
    snap, assignments = _clique_snapshot(2)  # unbounded slack
    # Freeze the later-sorted flight so it cannot absorb the hold.
    assignments = [
        assignments[0],
        assignments[1].model_copy(update={"is_frozen": True}),
    ]
    result = deconflict_airspace(snap, assignments)
    assert result.holds == {}
    assert result.report["n_residual_conflict_pairs"] == 1
    held = apply_airspace_holds(assignments, result.holds)
    for original_a, held_a in zip(assignments, held):
        assert held_a.planned_start == original_a.planned_start


def test_no_holds_returns_same_list():
    snap, assignments = _clique_snapshot(2)  # 4 corridors -> separated, no holds
    result = deconflict_airspace(snap, assignments)
    assert result.holds == {}
    assert apply_airspace_holds(assignments, result.holds) is assignments

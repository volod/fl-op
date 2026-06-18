"""3D/4D airspace deconfliction for aerial (UAV) delivery flights.

A post-solve pass over a solved plan that models the vertical and temporal
dimensions the routing solver does not. Each aerial flight is first placed into
one of a bounded set of vertically separated altitude corridors so that two
flights whose lateral paths pass within the horizontal-separation buffer during
an overlapping airborne window are kept apart in 3D (altitude-corridor planning +
vehicle-to-vehicle separation). When the conflict graph needs more corridors
than exist, the remaining same-corridor conflicts are then resolved in the time
dimension: a deadline-bounded temporal-separation pass holds the later flight
until the shared corridor clears (4D deconfliction). Only conflicts that cannot
be cleared without missing a delivery deadline stay residual, so the gap is both
narrowed and measured rather than hidden.

The lateral path of a flight is reconstructed from canonical geometry: the
serving asset's home hub, the task pickup (when the delivery pairs one), and the
task drop-off. The airborne window spans the inbound travel leg as well as the
on-task service window (``planned_start`` is service-start at the drop, so the
drone is already airborne for the hub->...->drop transit before it), priced at
the asset's travel speed, so proximity in time reflects when the drone is
actually flying rather than only when it is serving.

The pass is deterministic (no randomness) and advisory: it recommends corridors
and holds but does not mutate the plan, so coupling the holds back into routing
reassignment remains open (see future-improvements item 11).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

from fl_op.core.constants import (
    AIRSPACE_BASE_ALTITUDE_M,
    AIRSPACE_CORRIDOR_COUNT,
    AIRSPACE_HORIZONTAL_SEPARATION_M,
    AIRSPACE_TIME_BUFFER_S,
    AIRSPACE_VERTICAL_SEPARATION_M,
)
from fl_op.core.geometry import haversine_km, segment_min_distance_m

if TYPE_CHECKING:
    from fl_op.canonical.plan import Assignment
    from fl_op.canonical.snapshot import PlanningSnapshot

_UAV_OPERATION = "UAV_DELIVERY"
_TRAVEL_SPEED_TERM = "urn:xopt:capability:travel-speed"
_SECONDS_PER_HOUR = 3600.0

# Key the airspace plan is embedded under inside the drone KPI block.
AIRSPACE_PLAN_KEY = "airspace_deconfliction"

Point = tuple[float, float]


@dataclass
class AirspaceDeconfliction:
    """Result of the airspace pass: the report plus the dispatch holds to apply.

    ``holds`` maps a held flight's task id to the seconds its dispatch should be
    delayed so it enters its shared corridor only after the earlier flight has
    cleared. ``apply_airspace_holds`` consumes it to re-time the plan's
    assignments (changing dispatch), while ``report`` is embedded in the KPIs.
    """

    report: dict[str, Any]
    holds: dict[str, float] = field(default_factory=dict)


@dataclass
class _Flight:
    """One aerial flight: its lateral path and (delay-shiftable) airborne window."""

    task_id: str
    asset_id: str
    path: list[Point]
    # Airborne window: entry_s is hub departure (service start minus inbound
    # travel), exit_s is service finish at the drop. The temporal-separation
    # pass shifts both by delay_s, bounded by slack_s (deadline headroom).
    entry_s: float
    exit_s: float
    slack_s: float
    corridor: int = -1
    delay_s: float = 0.0

    @property
    def eff_entry(self) -> float:
        return self.entry_s + self.delay_s

    @property
    def eff_exit(self) -> float:
        return self.exit_s + self.delay_s

    def segments(self) -> list[tuple[Point, Point]]:
        if len(self.path) < 2:
            # A degenerate single-point path is a zero-length self-segment so
            # proximity to other flights' paths is still measured.
            return [(self.path[0], self.path[0])] if self.path else []
        return list(zip(self.path, self.path[1:]))


def build_airspace_plan(
    snapshot: "PlanningSnapshot",
    assignments: list["Assignment"],
) -> dict[str, Any]:
    """Deconflict the plan's aerial flights and return just the KPI report."""
    return deconflict_airspace(snapshot, assignments).report


def apply_airspace_holds(
    assignments: list["Assignment"], holds: dict[str, float]
) -> list["Assignment"]:
    """Re-time held flights' dispatch by their deconfliction delay.

    Each held assignment's ``planned_start``/``planned_finish`` shift later by the
    recommended hold, so the published plan dispatches the deconflicted schedule
    rather than only annotating it. Frozen/pinned assignments are never shifted
    (their hold is zero by construction), so a rolling revision's committed work
    is untouched. Returns the input list unchanged when there are no holds.
    """
    if not holds:
        return assignments
    held: list["Assignment"] = []
    for assignment in assignments:
        delay = holds.get(assignment.task_id, 0.0)
        if delay > 0.0 and not assignment.is_frozen and not assignment.is_pinned:
            shift = timedelta(seconds=delay)
            held.append(
                assignment.model_copy(
                    update={
                        "planned_start": assignment.planned_start + shift,
                        "planned_finish": assignment.planned_finish + shift,
                    }
                )
            )
        else:
            held.append(assignment)
    return held


def deconflict_airspace(
    snapshot: "PlanningSnapshot",
    assignments: list["Assignment"],
) -> AirspaceDeconfliction:
    """Deconflict the plan's aerial flights in altitude and (residually) time.

    Returns an empty report and no holds when the plan has no aerial flights, so
    the caller can apply and embed the result unconditionally.
    """
    location_coords = {
        loc.location_id: (float(loc.lat), float(loc.lon))
        for loc in snapshot.locations
    }
    asset_coords = {
        asset.asset_id: (float(asset.location.lat), float(asset.location.lon))
        for asset in snapshot.assets
        if asset.location is not None
    }
    asset_home = {
        asset.asset_id: asset.home_depot_ref for asset in snapshot.assets
    }
    asset_speed = {
        asset.asset_id: _capability_float(asset, _TRAVEL_SPEED_TERM)
        for asset in snapshot.assets
    }
    task_by_id = snapshot.task_index()

    flights: list[_Flight] = []
    for assignment in assignments:
        task = task_by_id.get(assignment.task_id)
        if task is None or task.operation_type != _UAV_OPERATION:
            continue
        asset_id = assignment.asset_ids[0] if assignment.asset_ids else ""
        path = _flight_path(
            task, asset_id, location_coords, asset_coords, asset_home
        )
        if len(path) < 1:
            continue
        start_s = _epoch(assignment.planned_start)
        exit_s = _epoch(assignment.planned_finish)
        entry_s = start_s - _inbound_travel_s(path, asset_speed.get(asset_id, 0.0))
        # A frozen/pinned flight (committed in a rolling revision) cannot be
        # re-timed, so it has no slack and acts only as a fixed obstacle.
        frozen = bool(assignment.is_frozen or assignment.is_pinned)
        flights.append(
            _Flight(
                task_id=assignment.task_id,
                asset_id=asset_id,
                path=path,
                entry_s=entry_s,
                exit_s=exit_s,
                slack_s=_deadline_slack_s(task, exit_s, frozen),
            )
        )

    if not flights:
        return AirspaceDeconfliction(report={})

    conflicts = _conflict_pairs(flights)
    _assign_corridors(flights, conflicts)
    _apply_temporal_separation(flights, conflicts)

    corridor_separated = 0
    time_separated = 0
    residual = 0
    for i, j in conflicts:
        if flights[i].corridor != flights[j].corridor:
            corridor_separated += 1
        elif _overlap(flights[i].eff_entry, flights[i].eff_exit,
                      flights[j].eff_entry, flights[j].eff_exit):
            residual += 1
        else:
            time_separated += 1

    corridors_used = sorted({flight.corridor for flight in flights})
    delays = [flight.delay_s for flight in flights]
    report = {
        "n_aerial_flights": len(flights),
        "corridors_available": AIRSPACE_CORRIDOR_COUNT,
        "corridors_used": len(corridors_used),
        "vertical_separation_m": AIRSPACE_VERTICAL_SEPARATION_M,
        "horizontal_separation_m": AIRSPACE_HORIZONTAL_SEPARATION_M,
        "n_conflict_pairs": len(conflicts),
        "n_corridor_separated_pairs": corridor_separated,
        "n_time_separated_pairs": time_separated,
        "n_deconflicted_pairs": corridor_separated + time_separated,
        "n_residual_conflict_pairs": residual,
        "n_flights_held": sum(1 for d in delays if d > 0.0),
        "total_deconfliction_delay_s": round(sum(delays), 1),
        "max_deconfliction_delay_s": round(max(delays), 1) if delays else 0.0,
        "max_concurrent_flights": _max_concurrent(flights),
        "fully_deconflicted": residual == 0,
        "holds_applied_to_dispatch": True,
        "corridor_altitudes_m": {
            str(idx): _corridor_altitude(idx) for idx in corridors_used
        },
        "flights": [
            {
                "task_id": flight.task_id,
                "asset_id": flight.asset_id,
                "corridor": flight.corridor,
                "altitude_m": _corridor_altitude(flight.corridor),
                "deconfliction_delay_s": round(flight.delay_s, 1),
            }
            for flight in flights
        ],
    }
    holds = {
        flight.task_id: flight.delay_s
        for flight in flights
        if flight.delay_s > 0.0
    }
    return AirspaceDeconfliction(report=report, holds=holds)


def _flight_path(
    task: Any,
    asset_id: str,
    location_coords: dict[str, Point],
    asset_coords: dict[str, Point],
    asset_home: dict[str, Optional[str]],
) -> list[Point]:
    """Reconstruct an aerial flight's lateral waypoints (origin, pickup, drop)."""
    origin = None
    home_ref = asset_home.get(asset_id)
    if home_ref and home_ref in location_coords:
        origin = location_coords[home_ref]
    elif asset_id in asset_coords:
        origin = asset_coords[asset_id]
    waypoints: list[Point] = []
    if origin is not None:
        waypoints.append(origin)
    pickup_ref = getattr(task, "pickup_location_ref", None)
    if pickup_ref and pickup_ref in location_coords:
        waypoints.append(location_coords[pickup_ref])
    drop = location_coords.get(task.location_ref)
    if drop is not None:
        waypoints.append(drop)
    # Drop consecutive duplicates so a hub==pickup pair does not create a
    # spurious zero-length leg.
    deduped: list[Point] = []
    for point in waypoints:
        if not deduped or deduped[-1] != point:
            deduped.append(point)
    return deduped


def _conflict_pairs(flights: list[_Flight]) -> list[tuple[int, int]]:
    """Index pairs of flights that are both laterally close and time-overlapping.

    Uses the un-shifted airborne windows: temporal separation is decided later,
    so the conflict graph captures every pair that would collide if all flights
    flew at their planned times in one corridor.
    """
    pairs: list[tuple[int, int]] = []
    for i in range(len(flights)):
        for j in range(i + 1, len(flights)):
            if not _overlap(
                flights[i].entry_s, flights[i].exit_s,
                flights[j].entry_s, flights[j].exit_s,
            ):
                continue
            if _lateral_min_distance(flights[i], flights[j]) < (
                AIRSPACE_HORIZONTAL_SEPARATION_M
            ):
                pairs.append((i, j))
    return pairs


def _overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    """Two airborne windows are co-present unless one clears the other by buffer.

    The airspace time buffer is the minimum gap that counts as separated, so two
    windows conflict iff neither finishes at least ``AIRSPACE_TIME_BUFFER_S``
    before the other starts. A temporal hold of exactly the buffer therefore
    clears the conflict.
    """
    buffer_s = AIRSPACE_TIME_BUFFER_S
    return (a1 + buffer_s) > b0 and (b1 + buffer_s) > a0


def _lateral_min_distance(a: _Flight, b: _Flight) -> float:
    best = float("inf")
    for seg_a in a.segments():
        for seg_b in b.segments():
            best = min(best, segment_min_distance_m(seg_a, seg_b))
            if best == 0.0:
                return 0.0
    return best


def _assign_corridors(
    flights: list[_Flight], conflicts: list[tuple[int, int]]
) -> None:
    """Greedy degree-ordered corridor colouring with bounded corridor count.

    Flights are coloured in decreasing conflict-degree order (Welsh-Powell), so
    the most contended flights claim corridors first. Each takes the lowest
    corridor no conflicting, already-coloured neighbour uses; if every corridor
    is taken by a neighbour, it takes the corridor with the fewest conflicting
    neighbours (least residual contention). The remaining same-corridor conflicts
    are then handed to the temporal-separation pass.
    """
    neighbors: dict[int, set[int]] = {i: set() for i in range(len(flights))}
    for i, j in conflicts:
        neighbors[i].add(j)
        neighbors[j].add(i)

    order = sorted(
        range(len(flights)),
        key=lambda i: (-len(neighbors[i]), flights[i].entry_s, flights[i].task_id),
    )
    for i in order:
        used_by_neighbor: dict[int, int] = {}
        for n in neighbors[i]:
            if flights[n].corridor >= 0:
                used_by_neighbor[flights[n].corridor] = (
                    used_by_neighbor.get(flights[n].corridor, 0) + 1
                )
        corridor = _first_free_corridor(used_by_neighbor)
        if corridor is None:
            # Every corridor is occupied by a neighbour: pick the least-contended
            # one, leaving the unavoidable conflicts for temporal separation.
            corridor = min(
                range(AIRSPACE_CORRIDOR_COUNT),
                key=lambda c: (used_by_neighbor.get(c, 0), c),
            )
        flights[i].corridor = corridor


def _apply_temporal_separation(
    flights: list[_Flight], conflicts: list[tuple[int, int]]
) -> None:
    """Hold same-corridor conflicting flights apart in time, bounded by slack.

    For flights left sharing a corridor, a deterministic list-scheduling pass
    (in airborne-entry order) delays each flight just enough to clear every
    earlier same-corridor flight it conflicts with laterally, by at least the
    airspace time buffer. The delay is capped at the flight's deadline slack, so
    a flight is never pushed past its delivery deadline; conflicts that cannot be
    cleared within slack stay residual.
    """
    same_corridor = [
        (i, j) for i, j in conflicts if flights[i].corridor == flights[j].corridor
    ]
    if not same_corridor:
        return
    conflict_adj: dict[int, set[int]] = defaultdict(set)
    for i, j in same_corridor:
        conflict_adj[i].add(j)
        conflict_adj[j].add(i)

    by_corridor: dict[int, list[int]] = defaultdict(list)
    for idx, flight in enumerate(flights):
        by_corridor[flight.corridor].append(idx)

    for members in by_corridor.values():
        placed: list[int] = []
        for i in sorted(members, key=lambda k: (flights[k].entry_s, flights[k].task_id)):
            required_entry = flights[i].entry_s
            for j in placed:
                if j in conflict_adj[i]:
                    required_entry = max(
                        required_entry, flights[j].eff_exit + AIRSPACE_TIME_BUFFER_S
                    )
            desired_delay = max(0.0, required_entry - flights[i].entry_s)
            flights[i].delay_s = min(desired_delay, flights[i].slack_s)
            placed.append(i)


def _first_free_corridor(used_by_neighbor: dict[int, int]) -> Optional[int]:
    for corridor in range(AIRSPACE_CORRIDOR_COUNT):
        if corridor not in used_by_neighbor:
            return corridor
    return None


def _max_concurrent(flights: list[_Flight]) -> int:
    """Peak number of simultaneously airborne flights after any temporal holds."""
    events: list[tuple[float, int]] = []
    for flight in flights:
        events.append((flight.eff_entry, 1))
        events.append((flight.eff_exit, -1))
    # Ends before starts at an equal timestamp so a hand-off does not count twice.
    events.sort(key=lambda e: (e[0], e[1]))
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def _inbound_travel_s(path: list[Point], speed_kmh: float) -> float:
    """Airborne transit seconds over the lateral path at the asset's speed."""
    if speed_kmh <= 0.0 or len(path) < 2:
        return 0.0
    km = sum(
        haversine_km(path[k][0], path[k][1], path[k + 1][0], path[k + 1][1])
        for k in range(len(path) - 1)
    )
    return km / speed_kmh * _SECONDS_PER_HOUR


def _deadline_slack_s(task: Any, exit_s: float, frozen: bool = False) -> float:
    """Seconds a flight may be held before missing its deadline.

    A frozen/pinned flight cannot move (zero slack); otherwise the slack is the
    headroom to the deadline (unbounded when the task declares none).
    """
    if frozen:
        return 0.0
    deadline = getattr(task, "deadline", None)
    if deadline is None:
        return float("inf")
    return max(0.0, _epoch(deadline) - exit_s)


def _corridor_altitude(corridor: int) -> float:
    safe = max(0, corridor)
    return round(
        AIRSPACE_BASE_ALTITUDE_M + safe * AIRSPACE_VERTICAL_SEPARATION_M, 1
    )


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

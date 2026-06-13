"""Held rolling assignments as vehicle time-window constraints.

A vehicle held by a frozen/carried assignment is no longer excluded from the
incremental re-solve: its busy interval becomes a break on the routing time
dimension, so new work lands only in a real non-overlapping gap.
"""

from datetime import datetime, timedelta, timezone

from fl_op.adapters.rolling.compiler import _held_asset_windows, _resolve_tasks
from fl_op.canonical.plan import Assignment
from fl_op.solver.cluster_solver import solve_cluster
from fl_op.solver.inputs import (
    SECTION_DEPOTS,
    SECTION_OPERATORS,
    SECTION_PRIME_MOVERS,
    SECTION_RELATED,
    SECTION_SITES,
    SECTION_TASKS,
)
from fl_op.solver.types import DepotRow, OperatorRow, PrimeMoverRow, RelatedRow, SiteRow, TaskRow

_HELD_HOURS = 4
_CLOCK_TOLERANCE_S = 120


def _order(oid: str, fid: str = "f0") -> TaskRow:
    return TaskRow.from_canonical_dict({
        "task_id": oid, "location_ref": fid, "operation_type": "SPRAYING",
        "area": "10", "deadline": "2027-12-01T00:00:00+00:00",
        "penalty_per_day": "100", "status": "pending",
        "revenue": "2000", "order_ref": "c0",
    })


def _vehicle(vid: str) -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict({
        "asset_id": vid, "asset_type": "TRACTOR", "rated_power": "150",
        "fuel_tank_volume": "400", "fuel_consumption_rate": "18",
        "lat": "48.5", "lon": "32.0", "home_depot_ref": "d0", "travel_speed": "15",
    })


def _implement(iid: str) -> RelatedRow:
    return RelatedRow.from_canonical_dict({
        "asset_id": iid, "asset_type": "SPRAYER",
        "compatible_operations": "['SPRAYING']", "required_power": "100",
        "working_width": "24", "min_speed": "5", "max_speed": "12",
        "material_capacity": "500", "home_depot_ref": "d0",
    })


def _operator(opid: str) -> OperatorRow:
    return OperatorRow.from_canonical_dict({
        "asset_id": opid, "name": opid, "shift_start": "21600",
        "shift_end": "57600", "certified_operations": "['SPRAYING']",
        "home_depot_ref": "d0",
    })


def _field(fid: str = "f0") -> SiteRow:
    return SiteRow.from_canonical_dict(
        {"location_id": fid, "lat": "48.5", "lon": "32.0", "area": "10"})


def _depot(did: str = "d0") -> DepotRow:
    return DepotRow.from_canonical_dict(
        {"location_id": did, "lat": "48.5", "lon": "32.0"})


def _cluster(allocated):
    return {
        "cluster_id": "cl0", "depot_ref": "d0", "task_ids": ["o0"],
        "allocated_prime_related": allocated, "total_penalty_per_day": 100.0,
    }


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


class TestRoutingHeldWindows:
    def test_dispatch_scheduled_after_held_window(self):
        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
        held = {"v0": [(now_epoch, now_epoch + _HELD_HOURS * 3600)]}
        dispatch, infeasible = solve_cluster(
            _cluster({"v0": ["i0"]}), [_order("o0")], [_vehicle("v0")],
            [_implement("i0")], [_field()], [_depot()],
            {}, {"v0": 0}, {"i0": 0}, held,
        )
        assert len(dispatch) == 1
        start_epoch = _epoch(dispatch[0]["scheduled_start"])
        held_end = now_epoch + _HELD_HOURS * 3600
        assert start_epoch >= held_end - _CLOCK_TOLERANCE_S

    def test_past_held_window_does_not_delay_dispatch(self):
        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
        held = {"v0": [(now_epoch - 8 * 3600, now_epoch - _HELD_HOURS * 3600)]}
        dispatch, _ = solve_cluster(
            _cluster({"v0": ["i0"]}), [_order("o0")], [_vehicle("v0")],
            [_implement("i0")], [_field()], [_depot()],
            {}, {"v0": 0}, {"i0": 0}, held,
        )
        assert len(dispatch) == 1
        start_epoch = _epoch(dispatch[0]["scheduled_start"])
        assert start_epoch < now_epoch + 3600

    def test_other_vehicle_windows_do_not_constrain(self):
        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
        held = {"v_other": [(now_epoch, now_epoch + _HELD_HOURS * 3600)]}
        dispatch, _ = solve_cluster(
            _cluster({"v0": ["i0"]}), [_order("o0")], [_vehicle("v0")],
            [_implement("i0")], [_field()], [_depot()],
            {}, {"v0": 0}, {"i0": 0}, held,
        )
        assert len(dispatch) == 1
        start_epoch = _epoch(dispatch[0]["scheduled_start"])
        assert start_epoch < now_epoch + 3600


class TestEffectiveTimeBasis:
    """Deadlines and held-window offsets are exact under a synthetic now.

    With an explicit now_epoch (the snapshot effective time), scheduled times
    derive from that origin instead of wall-clock now, so no clock tolerance
    is needed: replayed and synthetic timelines are exact.
    """

    _SYNTHETIC_NOW = datetime(2027, 6, 1, 6, 0, tzinfo=timezone.utc)

    def test_dispatch_times_derive_from_synthetic_now(self):
        now_epoch = int(self._SYNTHETIC_NOW.timestamp())
        dispatch, _ = solve_cluster(
            _cluster({"v0": ["i0"]}), [_order("o0")], [_vehicle("v0")],
            [_implement("i0")], [_field()], [_depot()],
            {}, {"v0": 0}, {"i0": 0}, None, None, None, now_epoch,
        )
        assert len(dispatch) == 1
        start_epoch = _epoch(dispatch[0]["scheduled_start"])
        assert now_epoch <= start_epoch < now_epoch + 24 * 3600

    def test_held_window_offsets_exact_under_synthetic_now(self):
        now_epoch = int(self._SYNTHETIC_NOW.timestamp())
        held = {"v0": [(now_epoch, now_epoch + _HELD_HOURS * 3600)]}
        dispatch, _ = solve_cluster(
            _cluster({"v0": ["i0"]}), [_order("o0")], [_vehicle("v0")],
            [_implement("i0")], [_field()], [_depot()],
            {}, {"v0": 0}, {"i0": 0}, held, None, None, now_epoch,
        )
        assert len(dispatch) == 1
        start_epoch = _epoch(dispatch[0]["scheduled_start"])
        # Exact comparison, no clock tolerance: the hold ends 4h after the
        # synthetic origin and the dispatch must start at or after that.
        assert start_epoch >= now_epoch + _HELD_HOURS * 3600


def _held_assignment(now: datetime) -> Assignment:
    return Assignment(
        assignment_id="a-held",
        task_id="o-held",
        bundle_id="b-held",
        asset_ids=["vehicle_h", "implement_a"],
        operator_ids=["op_held"],
        planned_start=now + timedelta(hours=6),
        planned_finish=now + timedelta(hours=8),
    )


class TestRollingResolveWithHeldVehicle:
    def test_held_asset_windows_collects_per_asset_intervals(self):
        now = datetime.now(tz=timezone.utc)
        windows = _held_asset_windows([_held_assignment(now)], {"vehicle_h"})
        assert list(windows) == ["vehicle_h"]
        start, end = windows["vehicle_h"][0]
        assert end - start == 2 * 3600

    def test_held_asset_windows_cover_implements_and_operators(self):
        """Every held asset gets a calendar: prime mover, implement, operator."""
        now = datetime.now(tz=timezone.utc)
        windows = _held_asset_windows(
            [_held_assignment(now)], {"vehicle_h", "implement_a", "op_held"}
        )
        assert sorted(windows) == ["implement_a", "op_held", "vehicle_h"]

    def test_held_assets_classified_by_section_not_id_prefix(self):
        """Domain-neutral classification: any id works when it is a prime mover."""
        now = datetime.now(tz=timezone.utc)
        held = _held_assignment(now).model_copy(
            update={"asset_ids": ["machine_00001", "attachment_000002"]}
        )
        windows = _held_asset_windows([held], {"machine_00001"})
        assert list(windows) == ["machine_00001"]

    def test_held_vehicle_reused_in_gap(self):
        """The only vehicle is held later today; the new task fits before it.

        With held-vehicle exclusion this task had no vehicle at all; with held
        windows it is dispatched into the gap, without overlapping the hold.
        """
        now = datetime.now(tz=timezone.utc)
        held = _held_assignment(now)
        solver_rows = {
            SECTION_PRIME_MOVERS: [_vehicle("vehicle_h")],
            SECTION_RELATED: [_implement("implement_a"), _implement("implement_b")],
            SECTION_OPERATORS: [_operator("op_held"), _operator("op_free")],
            SECTION_SITES: [_field()],
            SECTION_DEPOTS: [_depot()],
            SECTION_TASKS: [_order("o-new")],
        }
        chain_result = _resolve_tasks(solver_rows, {"o-new"}, [held])
        assert chain_result is not None
        assert len(chain_result.dispatch) == 1
        package = chain_result.dispatch[0]
        assert package["prime_asset_id"] == "vehicle_h"
        # The held implement stays allocatable, but hold-aware scoring
        # prefers the free one when both are otherwise equal.
        assert package["related_asset_id"] == "implement_b"

        start = _epoch(package["scheduled_start"])
        end = _epoch(package["scheduled_end"])
        held_start = int(held.planned_start.timestamp())
        held_end = int(held.planned_finish.timestamp())
        assert end <= held_start + _CLOCK_TOLERANCE_S or start >= held_end - _CLOCK_TOLERANCE_S

    def test_held_implement_reused_in_gap_when_it_is_the_only_one(self):
        """The only implement is held later today; the task fits its gap.

        With held-implement exclusion this task had no implement at all; as a
        resource calendar it is dispatched around the hold, never inside it.
        """
        now = datetime.now(tz=timezone.utc)
        held = _held_assignment(now)
        solver_rows = {
            SECTION_PRIME_MOVERS: [_vehicle("vehicle_h"), _vehicle("vehicle_free")],
            SECTION_RELATED: [_implement("implement_a")],
            SECTION_OPERATORS: [_operator("op_free")],
            SECTION_SITES: [_field()],
            SECTION_DEPOTS: [_depot()],
            SECTION_TASKS: [_order("o-new")],
        }
        chain_result = _resolve_tasks(solver_rows, {"o-new"}, [held])
        assert chain_result is not None
        assert len(chain_result.dispatch) == 1
        package = chain_result.dispatch[0]
        assert package["related_asset_id"] == "implement_a"

        start = _epoch(package["scheduled_start"])
        end = _epoch(package["scheduled_end"])
        held_start = int(held.planned_start.timestamp())
        held_end = int(held.planned_finish.timestamp())
        assert end <= held_start + _CLOCK_TOLERANCE_S or start >= held_end - _CLOCK_TOLERANCE_S

    def test_held_operator_stays_available_to_allocation(self):
        """A held operator is no longer excluded; with no alternative it still
        staffs the re-solved cluster."""
        now = datetime.now(tz=timezone.utc)
        held = _held_assignment(now)
        solver_rows = {
            SECTION_PRIME_MOVERS: [_vehicle("vehicle_free")],
            SECTION_RELATED: [_implement("implement_b")],
            SECTION_OPERATORS: [_operator("op_held")],
            SECTION_SITES: [_field()],
            SECTION_DEPOTS: [_depot()],
            SECTION_TASKS: [_order("o-new")],
        }
        chain_result = _resolve_tasks(solver_rows, {"o-new"}, [held])
        assert chain_result is not None
        assert len(chain_result.dispatch) == 1
        assert chain_result.dispatch[0]["operator_asset_id"] == "op_held"

"""Restricted zones and time-restricted areas: filters and interval algebra."""

from datetime import datetime, timedelta, timezone

from fl_op.canonical.enums import ReasonCode
from fl_op.core import constants
from fl_op.solver.restrictions import (
    allowed_start_intervals,
    apply_location_restrictions,
    merge_intervals,
    parse_polygon,
    point_in_polygon,
    polygons_intersect,
    subtract_intervals,
)
from fl_op.solver.types import SiteRow, TaskRow


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _order(oid: str, fid: str = "f0", op: str = "SPRAYING", windows: str = "",
           deadline_days: int = 30) -> TaskRow:
    return TaskRow.from_canonical_dict({
        "task_id": oid, "location_ref": fid, "operation_type": op,
        "area": "10", "deadline": _iso(_now() + timedelta(days=deadline_days)),
        "penalty_per_day": "100", "status": "pending", "revenue": "2000",
        "time_windows": windows,
    })


def _site(
    fid: str = "f0",
    restricted_ops: str = "[]",
    windows: str = "[]",
    polygon: str = "[]",
) -> SiteRow:
    return SiteRow.from_canonical_dict({
        "location_id": fid, "lat": "48.5", "lon": "32.0", "area": "10",
        "restricted_operations": restricted_ops, "restriction_windows": windows,
        "polygon": polygon,
    })


class TestIntervalAlgebra:
    def test_merge_overlapping_and_adjacent(self):
        assert merge_intervals([(5, 9), (0, 4), (12, 14)]) == [(0, 9), (12, 14)]

    def test_subtract_inner_block_splits_interval(self):
        assert subtract_intervals([(0, 10)], [(3, 5)]) == [(0, 2), (6, 10)]

    def test_subtract_full_cover_empties(self):
        assert subtract_intervals([(2, 8)], [(0, 10)]) == []

    def test_subtract_disjoint_is_noop(self):
        assert subtract_intervals([(0, 10)], [(20, 30)]) == [(0, 10)]


class TestGeometricRestrictions:
    def test_polygon_intersection_detects_overlap_and_containment(self):
        a = parse_polygon("[[0, 0], [0, 2], [2, 2], [2, 0]]")
        b = parse_polygon("[[1, 1], [1, 3], [3, 3], [3, 1]]")
        c = parse_polygon("[[4, 4], [4, 5], [5, 5], [5, 4]]")
        assert polygons_intersect(a, b)
        assert not polygons_intersect(a, c)
        assert point_in_polygon((1.0, 1.0), a)

    def test_fully_covering_restricted_area_excludes_task(self):
        task_site = _site(
            fid="field-1",
            polygon="[[0, 0], [0, 2], [2, 2], [2, 0]]",
        )
        # The restricted area fully contains the 2x2 site, so no work area
        # survives and the task is dropped.
        protected_area = _site(
            fid="wetland",
            restricted_ops="['SPRAYING']",
            polygon="[[-1, -1], [-1, 3], [3, 3], [3, -1]]",
        )

        kept, infeasible = apply_location_restrictions(
            [_order("o0", fid="field-1", op="SPRAYING")],
            [task_site, protected_area],
            _now(),
        )

        assert kept == []
        assert infeasible[0]["reason_code"] == ReasonCode.RESTRICTED_ZONE.value

    def test_partial_overlap_clips_work_area_instead_of_dropping(self):
        # The wetland covers a 1x1 corner of the 2x2 site: 25% restricted, so
        # 75% of the 10 ha work area survives (7.5 ha) and the task is kept.
        task_site = _site(
            fid="field-1",
            polygon="[[0, 0], [0, 2], [2, 2], [2, 0]]",
        )
        protected_area = _site(
            fid="wetland",
            restricted_ops="['SPRAYING']",
            polygon="[[1, 1], [1, 3], [3, 3], [3, 1]]",
        )

        kept, infeasible = apply_location_restrictions(
            [_order("o0", fid="field-1", op="SPRAYING")],
            [task_site, protected_area],
            _now(),
        )

        assert infeasible == []
        assert [o.task_id for o in kept] == ["o0"]
        assert kept[0].area == 7.5
        # Revenue scales with the clipped area: only 75% of the field is served.
        assert kept[0].revenue == 1500.0

    def test_centroid_inside_restricted_area_excludes_task_without_site_polygon(self):
        protected_area = _site(
            fid="wetland",
            restricted_ops="['SPRAYING']",
            polygon="[[48.0, 31.5], [48.0, 32.5], [49.0, 32.5], [49.0, 31.5]]",
        )

        kept, infeasible = apply_location_restrictions(
            [_order("o0", fid="f0", op="SPRAYING")],
            [_site(fid="f0"), protected_area],
            _now(),
        )

        assert kept == []
        assert infeasible[0]["task_id"] == "o0"


class TestAllowedStartIntervals:
    def test_no_windows_no_restrictions_is_full_range(self):
        order = _order("o0")
        site = _site()
        assert allowed_start_intervals(order, site, 100, 200) == [(100, 200)]

    def test_restriction_window_is_removed(self):
        start = _now() + timedelta(days=1)
        end = start + timedelta(days=2)
        order = _order("o0")
        site = _site(windows=str([f"{_iso(start)}/{_iso(end)}"]))
        now_epoch = int(_now().timestamp())
        deadline_epoch = now_epoch + 30 * 24 * 3600
        intervals = allowed_start_intervals(order, site, now_epoch, deadline_epoch)
        assert len(intervals) == 2
        assert intervals[0][0] == now_epoch
        assert intervals[1][1] == deadline_epoch
        blocked_probe = int(start.timestamp()) + 3600
        assert not any(s <= blocked_probe <= e for s, e in intervals)


class TestLocationRestrictionFilter:
    def test_prohibited_operation_excluded(self):
        orders = [_order("o0", op="SPRAYING"), _order("o1", op="SEEDING")]
        sites = [_site(restricted_ops="['SPRAYING']")]
        kept, infeasible = apply_location_restrictions(orders, sites, _now())
        assert [o.task_id for o in kept] == ["o1"]
        assert infeasible[0]["task_id"] == "o0"
        assert infeasible[0]["reason_code"] == ReasonCode.RESTRICTED_ZONE.value

    def test_full_horizon_restriction_excluded(self):
        window = str([f"{_iso(_now() - timedelta(days=1))}/{_iso(_now() + timedelta(days=60))}"])
        orders = [_order("o0", deadline_days=30)]
        sites = [_site(windows=window)]
        kept, infeasible = apply_location_restrictions(orders, sites, _now())
        assert kept == []
        assert infeasible[0]["reason_code"] == ReasonCode.RESTRICTED_ZONE.value

    def test_partial_restriction_passes_through(self):
        window = str([f"{_iso(_now() + timedelta(days=1))}/{_iso(_now() + timedelta(days=2))}"])
        orders = [_order("o0", deadline_days=30)]
        sites = [_site(windows=window)]
        kept, infeasible = apply_location_restrictions(orders, sites, _now())
        assert [o.task_id for o in kept] == ["o0"]
        assert infeasible == []

    def test_unknown_site_passes_through(self):
        orders = [_order("o0", fid="ghost")]
        kept, infeasible = apply_location_restrictions(orders, [_site()], _now())
        assert [o.task_id for o in kept] == ["o0"]
        assert infeasible == []


class TestRoutingRestriction:
    def test_schedule_starts_after_restriction_window(self):
        """A near-term restriction pushes the scheduled start past its end."""
        from fl_op.solver.cluster_solver import solve_cluster
        from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow

        restriction_end = _now() + timedelta(hours=24)
        window = str([f"{_iso(_now() - timedelta(hours=1))}/{_iso(restriction_end)}"])
        cd = {
            "cluster_id": "cl0", "depot_ref": "d0", "task_ids": ["o0"],
            "allocated_prime_related": {"v0": ["i0"]},
            "total_penalty_per_day": 100.0,
        }
        orders = [_order("o0")]
        vehicles = [PrimeMoverRow.from_canonical_dict({
            "asset_id": "v0", "rated_power": "150", "lat": "48.5", "lon": "32.0",
            "home_depot_ref": "d0", "travel_speed": "15",
        })]
        implements = [RelatedRow.from_canonical_dict({
            "asset_id": "i0", "compatible_operations": "['SPRAYING']",
            "required_power": "100", "working_width": "24", "max_speed": "12",
        })]
        fields = [_site(windows=window)]
        depots = [DepotRow.from_canonical_dict(
            {"location_id": "d0", "lat": "48.5", "lon": "32.0"})]

        dispatch, infeasible = solve_cluster(
            cd, orders, vehicles, implements, fields, depots,
            {}, {"v0": 0}, {"i0": 0},
        )
        assert {d["task_id"] for d in dispatch} == {"o0"}, infeasible
        scheduled = datetime.fromisoformat(dispatch[0]["scheduled_start"])
        assert scheduled >= restriction_end - timedelta(minutes=5)


class TestRoutingOccupancy:
    """Service-duration-aware occupancy: execution may not run into a block."""

    @staticmethod
    def _inputs(service_minutes: int):
        from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow

        cd = {
            "cluster_id": "cl0", "depot_ref": "d0", "task_ids": ["o0"],
            "allocated_prime_related": {"v0": ["i0"]},
            "total_penalty_per_day": 100.0,
        }
        order = TaskRow.from_canonical_dict({
            "task_id": "o0", "location_ref": "f0", "operation_type": "SPRAYING",
            "area": "10", "deadline": _iso(_now() + timedelta(days=30)),
            "penalty_per_day": "100", "status": "pending", "revenue": "2000",
            "service_duration_min": str(service_minutes),
        })
        vehicles = [PrimeMoverRow.from_canonical_dict({
            "asset_id": "v0", "rated_power": "150", "lat": "48.5", "lon": "32.0",
            "home_depot_ref": "d0", "travel_speed": "15",
        })]
        implements = [RelatedRow.from_canonical_dict({
            "asset_id": "i0", "compatible_operations": "['SPRAYING']",
            "required_power": "100", "working_width": "24", "max_speed": "12",
        })]
        depots = [DepotRow.from_canonical_dict(
            {"location_id": "d0", "lat": "48.5", "lon": "32.0"})]
        return cd, [order], vehicles, implements, depots

    def test_execution_cannot_run_into_restriction_window(self):
        """A 4 h job before a block starting in 2 h must wait for the block end.

        The start itself lies outside the restriction window, so start-only
        semantics would schedule immediately; occupancy semantics push the
        start past the window end.
        """
        from fl_op.solver.cluster_solver import solve_cluster

        block_start = _now() + timedelta(hours=2)
        block_end = _now() + timedelta(hours=6)
        window = str([f"{_iso(block_start)}/{_iso(block_end)}"])
        cd, orders, vehicles, implements, depots = self._inputs(service_minutes=240)
        fields = [_site(windows=window)]

        dispatch, infeasible = solve_cluster(
            cd, orders, vehicles, implements, fields, depots,
            {}, {"v0": 0}, {"i0": 0},
        )
        assert {d["task_id"] for d in dispatch} == {"o0"}, infeasible
        scheduled = datetime.fromisoformat(dispatch[0]["scheduled_start"])
        assert scheduled >= block_end - timedelta(minutes=5)

    def test_short_job_still_fits_before_restriction_window(self):
        """A 1 h job fits before a block starting in 2 h; no needless delay."""
        from fl_op.solver.cluster_solver import solve_cluster

        block_start = _now() + timedelta(hours=2)
        block_end = _now() + timedelta(hours=6)
        window = str([f"{_iso(block_start)}/{_iso(block_end)}"])
        cd, orders, vehicles, implements, depots = self._inputs(service_minutes=60)
        fields = [_site(windows=window)]

        dispatch, infeasible = solve_cluster(
            cd, orders, vehicles, implements, fields, depots,
            {}, {"v0": 0}, {"i0": 0},
        )
        assert {d["task_id"] for d in dispatch} == {"o0"}, infeasible
        end = datetime.fromisoformat(dispatch[0]["scheduled_end"])
        assert end <= block_start + timedelta(minutes=5)

    def test_execution_scheduled_outside_non_compliant_weather_window(self):
        """A weather-blocked interval keeps the whole execution out of it."""
        from fl_op.solver.cluster_solver import solve_cluster

        now_epoch = int(_now().timestamp())
        block_start = now_epoch + 2 * 3600
        block_end = now_epoch + 6 * 3600
        cd, orders, vehicles, implements, depots = self._inputs(service_minutes=240)
        fields = [_site()]

        dispatch, infeasible = solve_cluster(
            cd, orders, vehicles, implements, fields, depots,
            {}, {"v0": 0}, {"i0": 0},
            now_epoch=now_epoch,
            weather_blocked={"o0": [(block_start, block_end)]},
        )
        assert {d["task_id"] for d in dispatch} == {"o0"}, infeasible
        start = datetime.fromisoformat(dispatch[0]["scheduled_start"]).timestamp()
        end = datetime.fromisoformat(dispatch[0]["scheduled_end"]).timestamp()
        assert end <= block_start + 300 or start >= block_end - 300


class TestTimeDependentRouteRestriction:
    @staticmethod
    def _solve(zone_window: list[str]):
        from fl_op.solver.cluster_solver import solve_cluster
        from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow

        now = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
        now_epoch = int(now.timestamp())
        cluster = {
            "cluster_id": "cl-route-window",
            "depot_ref": "d0",
            "task_ids": ["o0"],
            "allocated_prime_related": {"v0": ["i0"]},
            "total_penalty_per_day": 100.0,
        }
        order = TaskRow.from_canonical_dict({
            "task_id": "o0",
            "location_ref": "f0",
            "operation_type": "SPRAYING",
            "deadline": (now + timedelta(days=1)).isoformat(),
            "revenue": 2000.0,
        })
        vehicle = PrimeMoverRow.from_canonical_dict({
            "asset_id": "v0",
            "rated_power": 150.0,
            "travel_speed": 60.0,
            "home_depot_ref": "d0",
        })
        implement = RelatedRow.from_canonical_dict({
            "asset_id": "i0",
            "required_power": 100.0,
            "compatible_operations": ["SPRAYING"],
        })
        field = SiteRow.from_canonical_dict({
            "location_id": "f0", "lat": 0.0, "lon": 0.04,
        })
        zone = SiteRow.from_canonical_dict({
            "location_id": "route-zone",
            "lat": 0.0,
            "lon": 0.02,
            "polygon": [
                [-0.01, 0.01],
                [-0.01, 0.03],
                [0.01, 0.03],
                [0.01, 0.01],
            ],
            "restricted_operations": ["SPRAYING"],
            "restriction_windows": zone_window,
        })
        depot = DepotRow.from_canonical_dict({
            "location_id": "d0", "lat": 0.0, "lon": 0.0,
        })

        dispatch, infeasible = solve_cluster(
            cluster,
            [order],
            [vehicle],
            [implement],
            [field, zone],
            [depot],
            {},
            {"v0": 0},
            {"i0": 0},
            now_epoch=now_epoch,
        )
        assert not infeasible
        return now, dispatch[0]

    def test_active_window_uses_detour_duration_and_waypoints(self):
        now = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
        window = [
            f"{(now - timedelta(minutes=5)).isoformat()}/"
            f"{(now + timedelta(hours=2)).isoformat()}"
        ]

        origin, dispatch = self._solve(window)

        elapsed = datetime.fromisoformat(dispatch["scheduled_start"]) - origin
        assert elapsed > timedelta(minutes=6)
        assert len(dispatch["route_waypoints"]) > 1

    def test_future_window_keeps_direct_route(self):
        now = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
        window = [
            f"{(now + timedelta(hours=2)).isoformat()}/"
            f"{(now + timedelta(hours=3)).isoformat()}"
        ]

        origin, dispatch = self._solve(window)

        elapsed = datetime.fromisoformat(dispatch["scheduled_start"]) - origin
        assert elapsed < timedelta(minutes=6)
        assert len(dispatch["route_waypoints"]) == 1


class TestRouteRestrictionPrimitives:
    """Direct unit coverage for the time-dependent route-restriction helpers."""

    _POLY = "[[-0.01, 0.01], [-0.01, 0.03], [0.01, 0.03], [0.01, 0.01]]"

    @staticmethod
    def _vehicle():
        from fl_op.solver.types import PrimeMoverRow, RelatedRow

        return {
            "prime": PrimeMoverRow.from_canonical_dict({"asset_id": "v0"}),
            "related": RelatedRow.from_canonical_dict(
                {"asset_id": "i0", "compatible_operations": ["SPRAYING"]}
            ),
        }

    def _zone(self, windows: str = "[]", ops: str = "['SPRAYING']") -> SiteRow:
        return _site(
            fid="zone", restricted_ops=ops, windows=windows, polygon=self._POLY
        )

    def test_windowless_restriction_is_always_active(self):
        from fl_op.solver.routing_geography import route_restrictions_for_vehicle

        restrictions = route_restrictions_for_vehicle(
            [self._zone()], self._vehicle(), int(_now().timestamp())
        )

        assert len(restrictions) == 1
        assert restrictions[0].windows == ()

    def test_incompatible_operation_is_skipped(self):
        from fl_op.solver.routing_geography import route_restrictions_for_vehicle

        restrictions = route_restrictions_for_vehicle(
            [self._zone(ops="['SEEDING']")], self._vehicle(), int(_now().timestamp())
        )

        assert restrictions == []

    def test_window_entirely_in_past_is_inactive(self):
        from fl_op.solver.routing_geography import route_restrictions_for_vehicle

        now = _now()
        window = str(
            [f"{_iso(now - timedelta(hours=3))}/{_iso(now - timedelta(hours=1))}"]
        )

        restrictions = route_restrictions_for_vehicle(
            [self._zone(windows=window)], self._vehicle(), int(now.timestamp())
        )

        assert restrictions == []

    def test_active_polygons_track_arc_occupancy_window(self):
        from fl_op.solver.routing_geography import (
            active_polygons,
            route_restrictions_for_vehicle,
        )

        now = _now()
        now_epoch = int(now.timestamp())
        window = str(
            [f"{_iso(now - timedelta(minutes=5))}/{_iso(now + timedelta(hours=2))}"]
        )
        restrictions = route_restrictions_for_vehicle(
            [self._zone(windows=window)], self._vehicle(), now_epoch
        )

        assert len(restrictions) == 1
        # An arc traversed inside the window sees the polygon; one after it does not.
        assert (
            len(active_polygons(restrictions, now_epoch + 3600, now_epoch + 3700)) == 1
        )
        assert (
            active_polygons(
                restrictions, now_epoch + 5 * 3600, now_epoch + 5 * 3600 + 100
            )
            == []
        )

    def test_active_polygons_include_unconditional_and_overlapping(self):
        from fl_op.solver.routing_geography import RouteRestriction, active_polygons

        always = RouteRestriction([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)], ())
        timed = RouteRestriction(
            [(2.0, 2.0), (2.0, 3.0), (3.0, 3.0)], ((1000, 2000),)
        )

        # Within the timed window both polygons are active.
        assert active_polygons([always, timed], 1500, 1800) == [
            always.polygon,
            timed.polygon,
        ]
        # Past the timed window only the always-active polygon remains.
        assert active_polygons([always, timed], 3000, 3500) == [always.polygon]

    def test_horizon_segments_split_on_window_edges(self):
        from fl_op.solver.routing_geography import (
            RouteRestriction,
            horizon_restriction_segments,
        )

        poly = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        now = 1_000_000
        timed = RouteRestriction(poly, ((now + 2000, now + 5000),))

        segments = horizon_restriction_segments([timed], now, 10_000)

        bounds = [
            (s.start_offset_s, s.end_offset_s, len(s.polygons)) for s in segments
        ]
        # Inactive -> active (window) -> inactive, cut at the window edges.
        assert bounds == [(0, 2000, 0), (2000, 5001, 1), (5001, 10000, 0)]

    def test_horizon_segments_single_without_windows(self):
        from fl_op.solver.routing_geography import (
            RouteRestriction,
            horizon_restriction_segments,
        )

        always = RouteRestriction([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)], ())

        segments = horizon_restriction_segments([always], 1_000_000, 10_000)

        assert len(segments) == 1
        assert len(segments[0].polygons) == 1


class TestTimeExpandedRouting:
    """The opt-in single-pass time-expanded path matches the refinement path."""

    def test_active_window_detours_in_single_pass(self, monkeypatch):
        monkeypatch.setattr(constants, "ROUTE_TIME_EXPANDED_ENABLED", True)
        now = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
        window = [
            f"{(now - timedelta(minutes=5)).isoformat()}/"
            f"{(now + timedelta(hours=2)).isoformat()}"
        ]

        origin, dispatch = TestTimeDependentRouteRestriction._solve(window)

        elapsed = datetime.fromisoformat(dispatch["scheduled_start"]) - origin
        assert elapsed > timedelta(minutes=6)
        assert len(dispatch["route_waypoints"]) > 1

    def test_future_window_keeps_direct_route_single_pass(self, monkeypatch):
        monkeypatch.setattr(constants, "ROUTE_TIME_EXPANDED_ENABLED", True)
        now = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
        window = [
            f"{(now + timedelta(hours=2)).isoformat()}/"
            f"{(now + timedelta(hours=3)).isoformat()}"
        ]

        origin, dispatch = TestTimeDependentRouteRestriction._solve(window)

        elapsed = datetime.fromisoformat(dispatch["scheduled_start"]) - origin
        assert elapsed < timedelta(minutes=6)
        assert len(dispatch["route_waypoints"]) == 1

    def test_disabled_flag_uses_refinement_path(self):
        # With the flag off (default) the time-expanded entry declines, so the
        # refinement path produces the schedule; the detour result is identical.
        assert constants.ROUTE_TIME_EXPANDED_ENABLED is False
        now = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
        window = [
            f"{(now - timedelta(minutes=5)).isoformat()}/"
            f"{(now + timedelta(hours=2)).isoformat()}"
        ]

        origin, dispatch = TestTimeDependentRouteRestriction._solve(window)

        elapsed = datetime.fromisoformat(dispatch["scheduled_start"]) - origin
        assert elapsed > timedelta(minutes=6)
        assert len(dispatch["route_waypoints"]) > 1

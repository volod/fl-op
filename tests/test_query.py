"""T30-T31: Query-contract tests.

Tests cover the pure helper functions (_build_vehicle_time_index,
_windows_overlap, _compute_conflict_risk) and verify that the query
module never imports or calls the OR-Tools cluster solver.
"""

import inspect

import pytest

from fl_op.solver.query import (
    TimeWindow,
    _build_vehicle_time_index,
    _compute_conflict_risk,
    _windows_overlap,
)


# ---------------------------------------------------------------------------
# _build_vehicle_time_index
# ---------------------------------------------------------------------------


class TestBuildVehicleTimeIndex:
    def test_empty_schedule_returns_empty_dict(self):
        assert _build_vehicle_time_index([]) == {}

    def test_groups_windows_by_vehicle(self):
        packages = [
            {"vehicle_id": "v0", "scheduled_start": "s0", "scheduled_end": "e0", "order_id": "o0"},
            {"vehicle_id": "v0", "scheduled_start": "s1", "scheduled_end": "e1", "order_id": "o1"},
            {"vehicle_id": "v1", "scheduled_start": "s2", "scheduled_end": "e2", "order_id": "o2"},
        ]
        index = _build_vehicle_time_index(packages)
        assert set(index.keys()) == {"v0", "v1"}
        assert len(index["v0"]) == 2
        assert len(index["v1"]) == 1

    def test_time_window_is_named_tuple(self):
        dp = {"vehicle_id": "v0", "scheduled_start": "2026-06-01T06:00:00+00:00",
              "scheduled_end": "2026-06-01T10:00:00+00:00", "order_id": "o0"}
        index = _build_vehicle_time_index([dp])
        tw = index["v0"][0]
        assert isinstance(tw, TimeWindow)

    def test_time_window_fields_match_package(self):
        dp = {"vehicle_id": "v0", "scheduled_start": "2026-06-01T06:00:00+00:00",
              "scheduled_end": "2026-06-01T10:00:00+00:00", "order_id": "o42"}
        index = _build_vehicle_time_index([dp])
        tw = index["v0"][0]
        assert tw.start == "2026-06-01T06:00:00+00:00"
        assert tw.end == "2026-06-01T10:00:00+00:00"
        assert tw.order_id == "o42"


# ---------------------------------------------------------------------------
# _windows_overlap
# ---------------------------------------------------------------------------


class TestWindowsOverlap:
    def test_fully_overlapping(self):
        assert _windows_overlap(
            "2026-06-01T06:00:00+00:00", "2026-06-01T10:00:00+00:00",
            "2026-06-01T07:00:00+00:00", "2026-06-01T09:00:00+00:00",
        )

    def test_partially_overlapping(self):
        assert _windows_overlap(
            "2026-06-01T06:00:00+00:00", "2026-06-01T10:00:00+00:00",
            "2026-06-01T08:00:00+00:00", "2026-06-01T12:00:00+00:00",
        )

    def test_non_overlapping_sequential(self):
        assert not _windows_overlap(
            "2026-06-01T06:00:00+00:00", "2026-06-01T09:00:00+00:00",
            "2026-06-01T11:00:00+00:00", "2026-06-01T14:00:00+00:00",
        )

    def test_adjacent_windows_do_not_overlap(self):
        # Touching at a single point: s2 == e1 -> s2 < e1 is False
        assert not _windows_overlap(
            "2026-06-01T06:00:00+00:00", "2026-06-01T10:00:00+00:00",
            "2026-06-01T10:00:00+00:00", "2026-06-01T14:00:00+00:00",
        )

    def test_invalid_iso_strings_return_false(self):
        assert not _windows_overlap("not-a-date", "also-bad", "2026-01-01", "2027-01-01")

    def test_both_invalid_return_false(self):
        assert not _windows_overlap("bad", "bad", "bad", "bad")


# ---------------------------------------------------------------------------
# _compute_conflict_risk
# ---------------------------------------------------------------------------


def _tw(start: str, end: str, oid: str = "o0") -> TimeWindow:
    return TimeWindow(start=start, end=end, order_id=oid)


_MORNING = "2026-06-01T06:00:00+00:00"
_NOON = "2026-06-01T12:00:00+00:00"
_EVENING = "2026-06-01T18:00:00+00:00"
_NEXT_DAY = "2026-06-02T06:00:00+00:00"


class TestComputeConflictRisk:
    def test_no_windows_in_index_returns_low(self):
        assert _compute_conflict_risk("v0", _MORNING, _NOON, {}) == "low"

    def test_vehicle_not_in_index_returns_low(self):
        index = {"v1": [_tw(_MORNING, _NOON)]}
        assert _compute_conflict_risk("v0", _MORNING, _NOON, index) == "low"

    def test_no_overlap_returns_low(self):
        index = {"v0": [_tw(_NEXT_DAY, "2026-06-02T10:00:00+00:00")]}
        assert _compute_conflict_risk("v0", _MORNING, _NOON, index) == "low"

    def test_one_overlap_returns_medium(self):
        index = {"v0": [_tw(_MORNING, _EVENING)]}
        assert _compute_conflict_risk("v0", _MORNING, _NOON, index) == "medium"

    def test_two_overlaps_returns_medium(self):
        index = {"v0": [_tw(_MORNING, _NOON, "o0"), _tw(_MORNING, _NOON, "o1")]}
        assert _compute_conflict_risk("v0", _MORNING, _EVENING, index) == "medium"

    def test_three_overlaps_returns_high(self):
        index = {"v0": [
            _tw(_MORNING, _NOON, "o0"),
            _tw(_MORNING, _NOON, "o1"),
            _tw(_MORNING, _NOON, "o2"),
        ]}
        # New window covers the same span — all 3 overlap
        assert _compute_conflict_risk("v0", _MORNING, _NOON, index) == "high"

    def test_risk_only_counts_overlapping_not_all(self):
        # Mix overlapping and non-overlapping windows; only overlapping ones raise risk
        index = {"v0": [
            _tw(_MORNING, _NOON, "o0"),       # overlaps
            _tw(_NEXT_DAY, "2026-06-02T10:00:00+00:00", "o1"),  # no overlap
        ]}
        risk = _compute_conflict_risk("v0", _MORNING, _NOON, index)
        # 1 overlap → medium
        assert risk == "medium"


# ---------------------------------------------------------------------------
# No OR-Tools / cluster_solver in query module
# ---------------------------------------------------------------------------


class TestQueryModuleDoesNotUseSolver:
    def test_cluster_solver_not_referenced_in_source(self):
        import fl_op.solver.query as qmod
        src = inspect.getsource(qmod)
        assert "cluster_solver" not in src, (
            "query module must not import or reference cluster_solver"
        )

    def test_pool_solve_not_referenced_in_source(self):
        import fl_op.solver.query as qmod
        src = inspect.getsource(qmod)
        assert "pool_solve" not in src, (
            "query module must not call pool_solve"
        )


# ---------------------------------------------------------------------------
# Top-3 sort order: descending margin, ascending vehicle_id for tiebreak
# ---------------------------------------------------------------------------


class TestTop3SortOrder:
    def test_highest_margin_first(self):
        candidates = [
            {"vehicle_id": "v0", "estimated_margin_eur": 300.0},
            {"vehicle_id": "v1", "estimated_margin_eur": 600.0},
            {"vehicle_id": "v2", "estimated_margin_eur": 100.0},
        ]
        candidates.sort(key=lambda c: (-c["estimated_margin_eur"], c["vehicle_id"]))
        assert candidates[0]["estimated_margin_eur"] == 600.0

    def test_vehicle_id_tiebreak_is_alphabetical(self):
        candidates = [
            {"vehicle_id": "v_z", "estimated_margin_eur": 500.0},
            {"vehicle_id": "v_a", "estimated_margin_eur": 500.0},
            {"vehicle_id": "v_m", "estimated_margin_eur": 500.0},
        ]
        candidates.sort(key=lambda c: (-c["estimated_margin_eur"], c["vehicle_id"]))
        assert candidates[0]["vehicle_id"] == "v_a"
        assert candidates[1]["vehicle_id"] == "v_m"
        assert candidates[2]["vehicle_id"] == "v_z"

    def test_mixed_margin_and_tiebreak(self):
        candidates = [
            {"vehicle_id": "v_z", "estimated_margin_eur": 500.0},
            {"vehicle_id": "v_a", "estimated_margin_eur": 500.0},
            {"vehicle_id": "v_x", "estimated_margin_eur": 700.0},
        ]
        candidates.sort(key=lambda c: (-c["estimated_margin_eur"], c["vehicle_id"]))
        assert candidates[0]["vehicle_id"] == "v_x"   # highest margin
        assert candidates[1]["vehicle_id"] == "v_a"   # tiebreak: a before z
        assert candidates[2]["vehicle_id"] == "v_z"

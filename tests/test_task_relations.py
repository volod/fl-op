"""Task precedence and workable time windows: structural relation semantics."""

from datetime import datetime, timedelta, timezone

from fl_op.canonical.enums import ReasonCode
from fl_op.solver.task_relations import (
    apply_dependency_filter,
    apply_time_window_filter,
    enforce_dependency_outcomes,
    parse_time_windows,
)
from fl_op.solver.types import TaskRow


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _order(oid: str, windows: str = "", depends_on: str = "", deadline_days: int = 30) -> TaskRow:
    return TaskRow.from_canonical_dict({
        "task_id": oid, "location_ref": "f0", "operation_type": "SPRAYING",
        "area": "10", "deadline": _iso(_now() + timedelta(days=deadline_days)),
        "penalty_per_day": "100", "status": "pending", "revenue": "2000",
        "order_ref": "c0", "time_windows": windows, "depends_on_task_ref": depends_on,
    })


class TestParseTimeWindows:
    def test_parses_stringified_interval_list(self):
        start = _now()
        end = start + timedelta(hours=6)
        raw = str([f"{_iso(start)}/{_iso(end)}"])
        windows = parse_time_windows(raw)
        assert len(windows) == 1
        assert windows[0][0] == start
        assert windows[0][1] == end

    def test_open_ended_window(self):
        start = _now()
        windows = parse_time_windows([f"{_iso(start)}/"])
        assert windows == [(start, None)]

    def test_malformed_items_are_skipped(self):
        start = _now()
        raw = ["not-a-window", f"{_iso(start)}/{_iso(start + timedelta(hours=1))}"]
        assert len(parse_time_windows(raw)) == 1

    def test_empty_inputs(self):
        assert parse_time_windows(None) == []
        assert parse_time_windows("[]") == []
        assert parse_time_windows("") == []


class TestTimeWindowFilter:
    def test_no_windows_passes_through(self):
        kept, infeasible = apply_time_window_filter([_order("o0")], _now())
        assert [o.task_id for o in kept] == ["o0"]
        assert infeasible == []

    def test_future_window_passes(self):
        windows = str([f"{_iso(_now() + timedelta(hours=2))}/{_iso(_now() + timedelta(hours=8))}"])
        kept, infeasible = apply_time_window_filter([_order("o0", windows)], _now())
        assert [o.task_id for o in kept] == ["o0"]

    def test_fully_elapsed_windows_excluded(self):
        windows = str([f"{_iso(_now() - timedelta(days=3))}/{_iso(_now() - timedelta(days=2))}"])
        kept, infeasible = apply_time_window_filter([_order("o0", windows)], _now())
        assert kept == []
        assert infeasible[0]["task_id"] == "o0"
        assert infeasible[0]["reason_code"] == ReasonCode.CONTRACT_WINDOW_INFEASIBLE.value

    def test_window_opening_after_deadline_excluded(self):
        windows = str([f"{_iso(_now() + timedelta(days=40))}/{_iso(_now() + timedelta(days=41))}"])
        kept, infeasible = apply_time_window_filter(
            [_order("o0", windows, deadline_days=30)], _now()
        )
        assert kept == []
        assert infeasible[0]["reason_code"] == ReasonCode.CONTRACT_WINDOW_INFEASIBLE.value


class TestDependencyFilter:
    def test_dependent_of_excluded_predecessor_cascades(self):
        orders = [
            _order("o1", depends_on="o0"),
            _order("o2", depends_on="o1"),
            _order("o3"),
        ]
        kept, infeasible = apply_dependency_filter(orders, {"o0"})
        assert [o.task_id for o in kept] == ["o3"]
        assert [r["task_id"] for r in infeasible] == ["o1", "o2"]
        assert all(
            r["reason_code"] == ReasonCode.PREDECESSOR_UNSERVED.value for r in infeasible
        )

    def test_reference_to_absent_task_is_satisfied(self):
        orders = [_order("o1", depends_on="completed-earlier")]
        kept, infeasible = apply_dependency_filter(orders, set())
        assert [o.task_id for o in kept] == ["o1"]
        assert infeasible == []


class TestDependencyOutcomes:
    def test_dependent_dispatch_withdrawn_when_predecessor_unserved(self):
        orders = [_order("o_pre"), _order("o_dep", depends_on="o_pre")]
        dispatch = [{"task_id": "o_dep", "cluster_id": "c0"}]
        infeasible = [{"task_id": "o_pre", "cluster_id": "c0",
                       "reason_code": ReasonCode.OPTIMIZATION_TRADEOFF.value, "detail": ""}]
        out_dispatch, out_infeasible = enforce_dependency_outcomes(
            dispatch, infeasible, orders
        )
        assert out_dispatch == []
        withdrawn = [r for r in out_infeasible if r["task_id"] == "o_dep"]
        assert withdrawn[0]["reason_code"] == ReasonCode.PREDECESSOR_UNSERVED.value

    def test_satisfied_chain_kept(self):
        orders = [_order("o_pre"), _order("o_dep", depends_on="o_pre")]
        dispatch = [
            {"task_id": "o_pre", "cluster_id": "c0"},
            {"task_id": "o_dep", "cluster_id": "c0"},
        ]
        out_dispatch, out_infeasible = enforce_dependency_outcomes(dispatch, [], orders)
        assert {d["task_id"] for d in out_dispatch} == {"o_pre", "o_dep"}
        assert out_infeasible == []

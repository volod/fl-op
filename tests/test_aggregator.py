"""T24-T27: Aggregator tests.

Tests cover the pure-function helpers (_compute_kpis, _write_report, _write_json)
and the fast-path of pool_solve (empty cluster list).  End-to-end pool behaviour
is covered by test_smoke.py.
"""

import json

import pytest

from fl_op.solver.aggregator import _compute_kpis, _write_json, _write_report
from fl_op.solver.cluster_pool import pool_solve
from fl_op.solver.types import TaskRow


# ---------------------------------------------------------------------------
# _compute_kpis — pure function
# ---------------------------------------------------------------------------


class TestComputeKpis:
    def test_empty_inputs_return_zeros(self):
        kpis = _compute_kpis([], [], [], {})
        assert kpis["n_dispatched"] == 0
        assert kpis["n_infeasible"] == 0
        assert kpis["total_estimated_margin_eur"] == 0.0
        assert kpis["greedy_baseline_margin_eur"] == 0.0
        assert kpis["solver_improvement_eur"] == 0.0
        assert kpis["total_fuel_l"] == 0.0
        assert kpis["total_fertilizer_kg"] == 0.0
        assert kpis["infeasibility_reasons"] == {}

    def test_dispatched_and_infeasible_counts(self):
        dispatch = [
            {"task_id": "o0", "estimated_margin_eur": 1000.0,
             "estimated_fuel_l": 50.0, "estimated_fertilizer_kg": 20.0},
        ]
        infeasible = [{"task_id": "o1", "reason_code": "OPTIMIZATION_TRADEOFF"}]
        orders = [
            TaskRow.from_canonical_dict({"task_id": "o0", "revenue": "1000", "area": "10"}),
            TaskRow.from_canonical_dict({"task_id": "o1", "revenue": "500", "area": "5"}),
        ]
        kpis = _compute_kpis(dispatch, infeasible, orders, {"o0": (0, 0)})
        assert kpis["n_dispatched"] == 1
        assert kpis["n_infeasible"] == 1

    def test_total_margin_summed_from_dispatch(self):
        dispatch = [
            {"task_id": "o0", "estimated_margin_eur": 300.0,
             "estimated_fuel_l": 10.0, "estimated_fertilizer_kg": 0.0},
            {"task_id": "o1", "estimated_margin_eur": 700.0,
             "estimated_fuel_l": 20.0, "estimated_fertilizer_kg": 0.0},
        ]
        kpis = _compute_kpis(dispatch, [], [], {})
        assert kpis["total_estimated_margin_eur"] == pytest.approx(1000.0)

    def test_fuel_and_fertilizer_summed(self):
        dispatch = [
            {"task_id": "o0", "estimated_margin_eur": 0,
             "estimated_fuel_l": 30.0, "estimated_fertilizer_kg": 10.0},
            {"task_id": "o1", "estimated_margin_eur": 0,
             "estimated_fuel_l": 20.0, "estimated_fertilizer_kg": 5.0},
        ]
        kpis = _compute_kpis(dispatch, [], [], {})
        assert kpis["total_fuel_l"] == pytest.approx(50.0)
        assert kpis["total_fertilizer_kg"] == pytest.approx(15.0)

    def test_infeasibility_reasons_counted(self):
        infeasible = [
            {"task_id": "o0", "reason_code": "OPTIMIZATION_TRADEOFF"},
            {"task_id": "o1", "reason_code": "OPTIMIZATION_TRADEOFF"},
            {"task_id": "o2", "reason_code": "UNKNOWN"},
        ]
        kpis = _compute_kpis([], infeasible, [], {})
        assert kpis["infeasibility_reasons"]["OPTIMIZATION_TRADEOFF"] == 2
        assert kpis["infeasibility_reasons"]["UNKNOWN"] == 1

    def test_improvement_equals_total_minus_baseline(self):
        dispatch = [
            {"task_id": "o0", "estimated_margin_eur": 800.0,
             "estimated_fuel_l": 0, "estimated_fertilizer_kg": 0},
        ]
        orders = [TaskRow.from_canonical_dict({"task_id": "o0", "revenue": "600", "area": "0"})]
        # greedy baseline: 600 EUR revenue - 0 fuel cost = 600
        kpis = _compute_kpis(dispatch, [], orders, {"o0": (0, 0)})
        assert kpis["solver_improvement_eur"] == pytest.approx(
            kpis["total_estimated_margin_eur"] - kpis["greedy_baseline_margin_eur"]
        )

    def test_required_keys_present(self):
        kpis = _compute_kpis([], [], [], {})
        for key in (
            "n_dispatched", "n_infeasible",
            "total_estimated_margin_eur", "greedy_baseline_margin_eur",
            "solver_improvement_eur", "total_fuel_l", "total_fertilizer_kg",
            "infeasibility_reasons",
        ):
            assert key in kpis, f"KPI dict missing key: {key}"


# ---------------------------------------------------------------------------
# pool_solve — fast-path only (empty cluster list, no process spawn)
# ---------------------------------------------------------------------------


class TestPoolSolveFastPath:
    def test_empty_clusters_returns_empty_lists(self):
        dispatch, infeasible = pool_solve([], [], [], [], [], [], {}, {}, {})
        assert dispatch == []
        assert infeasible == []

    def test_return_types(self):
        dispatch, infeasible = pool_solve([], [], [], [], [], [], {}, {}, {})
        assert isinstance(dispatch, list)
        assert isinstance(infeasible, list)


# ---------------------------------------------------------------------------
# _write_report — output format
# ---------------------------------------------------------------------------


class TestWriteReport:
    def _kpis(self, dispatched=3, infeasible=1, margin=2500.0, baseline=2000.0,
               fuel=80.0, fertilizer=10.0, reasons=None):
        return {
            "n_dispatched": dispatched,
            "n_infeasible": infeasible,
            "total_estimated_margin_eur": margin,
            "greedy_baseline_margin_eur": baseline,
            "solver_improvement_eur": round(margin - baseline, 2),
            "total_fuel_l": fuel,
            "total_fertilizer_kg": fertilizer,
            "infeasibility_reasons": reasons or {},
        }

    def test_report_contains_header(self, tmp_path):
        path = tmp_path / "report.txt"
        _write_report([], [], self._kpis(), path)
        assert "Fleet Optimization Schedule Report" in path.read_text()

    def test_dispatched_and_infeasible_counts_in_report(self, tmp_path):
        path = tmp_path / "report.txt"
        _write_report([], [], self._kpis(dispatched=7, infeasible=3), path)
        text = path.read_text()
        assert "Dispatched:   7" in text
        assert "Infeasible:   3" in text

    def test_margin_formatted_in_report(self, tmp_path):
        path = tmp_path / "report.txt"
        _write_report([], [], self._kpis(margin=3500.0), path)
        assert "3500.00 EUR" in path.read_text()

    def test_infeasibility_reasons_in_report(self, tmp_path):
        path = tmp_path / "report.txt"
        kpis = self._kpis(reasons={"OPTIMIZATION_TRADEOFF": 2, "UNKNOWN": 1})
        _write_report([], [], kpis, path)
        text = path.read_text()
        assert "OPTIMIZATION_TRADEOFF: 2" in text
        assert "UNKNOWN: 1" in text

    def test_infeasible_orders_section_written(self, tmp_path):
        path = tmp_path / "report.txt"
        infeasible = [{"task_id": "o99", "reason_code": "UNKNOWN", "detail": "err"}]
        _write_report([], infeasible, self._kpis(infeasible=1), path)
        text = path.read_text()
        assert "o99" in text
        assert "UNKNOWN" in text

    def test_report_file_created(self, tmp_path):
        path = tmp_path / "report.txt"
        _write_report([], [], self._kpis(), path)
        assert path.exists()
        assert path.stat().st_size > 0


# ---------------------------------------------------------------------------
# _write_json — roundtrip
# ---------------------------------------------------------------------------


class TestWriteJson:
    def test_json_roundtrip(self, tmp_path):
        obj = {"key": "value", "num": 42, "nested": {"a": [1, 2, 3]}}
        path = tmp_path / "out.json"
        _write_json(obj, path)
        loaded = json.loads(path.read_text())
        assert loaded == obj

    def test_non_serialisable_uses_str_fallback(self, tmp_path):
        from datetime import datetime, timezone

        obj = {"ts": datetime.now(tz=timezone.utc)}
        path = tmp_path / "out.json"
        _write_json(obj, path)  # must not raise; uses default=str
        loaded = json.loads(path.read_text())
        assert isinstance(loaded["ts"], str)

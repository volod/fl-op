"""T24-T27: Aggregator tests.

Tests cover the pure-function helpers (_compute_kpis, _write_report, _write_json)
and the fast-path of pool_solve (empty cluster list).  End-to-end pool behaviour
is covered by test_smoke.py.
"""

import json
from datetime import datetime, timezone

import pytest

from fl_op.solver.aggregator import _compute_kpis, _write_json, _write_report
from fl_op.solver.cluster_pool import pool_solve
from fl_op.solver.types import PrimeMoverRow, RelatedRow, SiteRow, TaskRow


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

    def test_operating_and_toll_costs_summed(self):
        dispatch = [
            {"task_id": "o0", "estimated_margin_eur": 0,
             "estimated_distance_km": 12.0, "estimated_labor_cost_eur": 30.0,
             "estimated_machine_wear_cost_eur": 8.0, "estimated_toll_cost_eur": 0.5},
            {"task_id": "o1", "estimated_margin_eur": 0,
             "estimated_distance_km": 8.0, "estimated_labor_cost_eur": 20.0,
             "estimated_machine_wear_cost_eur": 5.0, "estimated_toll_cost_eur": 0.3},
        ]
        kpis = _compute_kpis(dispatch, [], [], {})
        assert kpis["total_distance_km"] == pytest.approx(20.0)
        assert kpis["total_labor_cost_eur"] == pytest.approx(50.0)
        assert kpis["total_machine_wear_cost_eur"] == pytest.approx(13.0)
        assert kpis["total_toll_cost_eur"] == pytest.approx(0.8)

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

    def test_greedy_baseline_uses_dispatch_like_net_costs_when_rows_available(self):
        dispatch = [
            {
                "task_id": "o0",
                "estimated_margin_eur": 900.0,
                "estimated_fuel_l": 10.0,
                "estimated_fertilizer_kg": 80.0,
            },
        ]
        orders = [
            TaskRow.from_canonical_dict(
                {
                    "task_id": "o0",
                    "location_ref": "f0",
                    "revenue": "1000",
                    "service_duration_min": "60",
                }
            )
        ]
        vehicles = [
            PrimeMoverRow.from_canonical_dict(
                {
                    "asset_id": "v0",
                    "fuel_consumption_rate": "10",
                    "lat": "48.5",
                    "lon": "32.0",
                }
            )
        ]
        implements = [
            RelatedRow.from_canonical_dict(
                {
                    "asset_id": "i0",
                    "material_capacity": "100",
                }
            )
        ]
        fields = [
            SiteRow.from_canonical_dict(
                {
                    "location_id": "f0",
                    "lat": "48.5",
                    "lon": "32.0",
                }
            )
        ]

        kpis = _compute_kpis(
            dispatch,
            [],
            orders,
            {"o0": (0, 0)},
            fuel_price_eur_per_l=3.0,
            material_price_eur_per_kg=2.0,
            vehicles=vehicles,
            implements=implements,
            fields=fields,
        )

        # 1000 revenue - 10 L operation fuel * 3 - 80 kg material * 2.
        assert kpis["greedy_baseline_margin_eur"] == pytest.approx(810.0)
        assert kpis["solver_improvement_eur"] == pytest.approx(90.0)

    def test_required_keys_present(self):
        kpis = _compute_kpis([], [], [], {})
        for key in (
            "n_dispatched", "n_infeasible",
            "total_estimated_margin_eur", "greedy_baseline_margin_eur",
            "solver_improvement_eur", "total_fuel_l", "total_fertilizer_kg",
            "total_distance_km", "total_labor_cost_eur",
            "total_machine_wear_cost_eur", "total_toll_cost_eur",
            "infeasibility_reasons",
        ):
            assert key in kpis, f"KPI dict missing key: {key}"

    def test_completion_time_and_on_time_kpis(self):
        origin = datetime(2026, 6, 13, 8, 0, tzinfo=timezone.utc)
        dispatch = [
            {
                "task_id": "o0",
                "scheduled_start": "2026-06-13T08:10:00+00:00",
                "scheduled_end": "2026-06-13T08:30:00+00:00",
            },
            {
                "task_id": "o1",
                "scheduled_start": "2026-06-13T09:00:00+00:00",
                "scheduled_end": "2026-06-13T09:30:00+00:00",
            },
        ]
        orders = [
            TaskRow.from_canonical_dict(
                {"task_id": "o0", "deadline": "2026-06-13T08:45:00+00:00"}
            ),
            TaskRow.from_canonical_dict(
                {"task_id": "o1", "deadline": "2026-06-13T09:00:00+00:00"}
            ),
        ]

        kpis = _compute_kpis(
            dispatch,
            [],
            orders,
            {},
            planning_origin=origin,
            optimization_objective="time",
        )

        assert kpis["optimization_objective"] == "time"
        assert kpis["total_completion_time_s"] == pytest.approx(7200.0)
        assert kpis["avg_completion_time_s"] == pytest.approx(3600.0)
        assert kpis["max_completion_time_s"] == pytest.approx(5400.0)
        assert kpis["n_on_time"] == 1
        assert kpis["n_late"] == 1
        assert kpis["on_time_rate_pct"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# pool_solve — fast-path only (empty cluster list, no process spawn)
# ---------------------------------------------------------------------------


class TestPoolSolveFastPath:
    def test_empty_clusters_returns_empty_lists(self):
        dispatch, infeasible, telemetry = pool_solve([], [], [], [], [], [], {}, {}, {})
        assert dispatch == []
        assert infeasible == []
        assert telemetry == []

    def test_return_types(self):
        dispatch, infeasible, telemetry = pool_solve([], [], [], [], [], [], {}, {}, {})
        assert isinstance(dispatch, list)
        assert isinstance(infeasible, list)
        assert isinstance(telemetry, list)


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

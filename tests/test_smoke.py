"""T34: E2E smoke test: generate-data -> solve -> reschedule -> query-contract."""

import json
import os
import pathlib

import pytest


@pytest.mark.timeout(300)
def test_e2e_smoke(tmp_path):
    """Full pipeline smoke test at minimum scale; asserts no crash and artifacts written."""
    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        from fl_op.data.generator import run_generate

        # 1. Generate data
        run_generate(n_vehicles=10, n_implements=30, n_orders=5, n_depots=2, seed=7, data_path=None)
        data_dirs = sorted((tmp_path / ".data" / "generate-data").iterdir())
        assert data_dirs, "generate-data produced no output directory"
        data_dir = str(data_dirs[-1])

        # 2. Solve
        from fl_op.solver.solve_pipeline import run_solve

        try:
            run_solve(data_dir=data_dir)
        except SystemExit as exc:
            # exit(1) is allowed when 0 orders served (all infeasible)
            assert exc.code == 1, f"run_solve exited with unexpected code {exc.code}"

        solve_dirs = sorted((tmp_path / ".data" / "solve").iterdir())
        assert solve_dirs, "solve produced no output directory"
        solve_dir = solve_dirs[-1]

        # infeasible_orders.json must always be written
        inf_path = solve_dir / "infeasible_orders.json"
        assert inf_path.exists(), "infeasible_orders.json not written"

        # schedule_kpis.json must always be written
        kpi_path = solve_dir / "schedule_kpis.json"
        assert kpi_path.exists(), "schedule_kpis.json not written"

        kpis = json.loads(kpi_path.read_text())
        assert "n_dispatched" in kpis
        assert "n_infeasible" in kpis

        # Analyse latest solve artifacts and print console statistics.
        from fl_op.solver.analysis import run_analyse

        run_analyse(schedule_dir=str(solve_dir))

        # 3. Reschedule (no events; should rerun on pending orders)
        from fl_op.solver.reschedule_pipeline import run_reschedule

        try:
            run_reschedule(
                data_dir=data_dir,
                schedule_dir=str(solve_dir),
                events_path=None,
            )
        except SystemExit:
            pass  # exit 0 (all-frozen) or exit 1 (all infeasible) are both acceptable

        reschedule_dirs = list((tmp_path / ".data" / "reschedule").iterdir()) if (
            tmp_path / ".data" / "reschedule"
        ).exists() else []
        # Either reschedule wrote output or exited 0 (all-frozen guard)
        if reschedule_dirs:
            rdir = sorted(reschedule_dirs)[-1]
            assert (rdir / "plan_diff.json").exists()
            assert (rdir / "plan_diff.txt").exists()

        # 4. Query-contract (write a minimal order JSON first)
        order_file = tmp_path / "new_order.json"
        order_file.write_text(json.dumps({
            "order_id": "new_order_test",
            "contract_id": "c_test",
            "field_id": "f000000",
            "operation_type": "SPRAYING",
            "area_ha": 50,
            "deadline": "2026-07-01T00:00:00+00:00",
            "penalty_per_day_eur": 300,
            "status": "pending",
            "estimated_revenue_eur": 8000,
        }))

        from fl_op.solver.query_pipeline import run_query

        run_query(
            data_dir=data_dir,
            schedule_dir=str(solve_dir),
            order_path=str(order_file),
        )

        query_dirs = list((tmp_path / ".data" / "query-contract").iterdir()) if (
            tmp_path / ".data" / "query-contract"
        ).exists() else []
        assert query_dirs, "query-contract produced no output"
        qdir = sorted(query_dirs)[-1]
        qresult = json.loads((qdir / "query_result.json").read_text())
        assert "feasible" in qresult
        assert "candidates" in qresult

    finally:
        os.chdir(orig_cwd)

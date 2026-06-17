"""Parameter tuning: Optuna study wiring, artifacts, and MLflow gating."""

import contextlib
import json
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from fl_op.core import constants
from fl_op.main import cli
from fl_op.solver.chain import SolverChainResult
from fl_op.solver.parameters import SolverParameters


def _fake_chain(rows, enforcement=None, parameters=None, **kwargs):
    """Deterministic, parameter-sensitive stand-in for the real solver chain.

    The margin peaks at cluster_target_size 35 so the study has a real
    optimum to find; everything else about the chain is irrelevant here.
    """
    params = parameters or SolverParameters()
    margin = 1000.0 - abs(params.cluster_target_size - 35) * 10.0
    return SolverChainResult(
        dispatch=[],
        infeasible=[],
        kpis={"total_estimated_margin_eur": margin},
        greedy_assignment={},
        n_clusters=1,
    )


@pytest.fixture
def tune_run(dataset_dir: pathlib.Path, tmp_path, monkeypatch):
    from fl_op.tuning import optuna_tuner

    monkeypatch.setattr("fl_op.solver.chain.run_solver_chain", _fake_chain)
    monkeypatch.setattr(optuna_tuner, "DATA_ROOT", tmp_path)
    out_dir = optuna_tuner.run_tune(str(dataset_dir), n_trials=3, seed=11)
    return out_dir


def test_tune_writes_baseline_trials_and_best_params(tune_run) -> None:
    for name in ("baseline.json", "trials.json", "best_params.json"):
        assert (tune_run / name).exists(), f"missing artifact {name}"


def test_tune_trials_stay_within_declared_bounds(tune_run) -> None:
    trials = json.loads((tune_run / "trials.json").read_text())["trials"]
    assert len(trials) == 3
    for record in trials:
        params = record["params"]
        assert (
            constants.TUNE_CLUSTER_TARGET_SIZE_MIN
            <= params["cluster_target_size"]
            <= constants.TUNE_CLUSTER_TARGET_SIZE_MAX
        )
        for key in ("score_weight_margin", "score_weight_reposition"):
            assert (
                constants.TUNE_SCORE_WEIGHT_MIN
                <= params[key]
                <= constants.TUNE_SCORE_WEIGHT_MAX
            )
        assert params["cluster_solve_time_limit_s"] >= constants.TUNE_TIME_LIMIT_MIN_S


def test_tune_best_record_is_consistent_with_trials(tune_run) -> None:
    trials = json.loads((tune_run / "trials.json").read_text())["trials"]
    best = json.loads((tune_run / "best_params.json").read_text())
    baseline = json.loads((tune_run / "baseline.json").read_text())

    assert best["best_objective"] == max(t["objective"] for t in trials)
    assert best["improvement_over_baseline"] == pytest.approx(
        round(best["best_objective"] - baseline["objective"], 2)
    )
    assert best["snapshot_id"]
    assert best["snapshot_hash"]
    assert best["n_trials"] == 3


def test_tune_baseline_uses_trial_scale_time_budget(tune_run) -> None:
    baseline = json.loads((tune_run / "baseline.json").read_text())
    assert (
        baseline["baseline_params"]["cluster_solve_time_limit_s"]
        <= constants.TUNE_TIME_LIMIT_MAX_S
    )


def test_tune_records_multi_objective_metadata(tune_run) -> None:
    trials = json.loads((tune_run / "trials.json").read_text())
    best = json.loads((tune_run / "best_params.json").read_text())

    assert trials["multi_objective"] is True
    assert trials["objective_names"] == [
        "business_objective",
        "plan_instability_penalty",
        "wall_time_s",
    ]
    assert best["objective_directions"] == ["maximize", "minimize", "minimize"]
    assert best["pareto_trials"]
    assert all("wall_time_s" in t["objectives"] for t in trials["trials"])


def test_tune_can_average_multiple_datasets(dataset_dir, tmp_path, monkeypatch) -> None:
    from fl_op.tuning import optuna_tuner

    monkeypatch.setattr("fl_op.solver.chain.run_solver_chain", _fake_chain)
    monkeypatch.setattr(optuna_tuner, "DATA_ROOT", tmp_path)
    out_dir = optuna_tuner.run_tune(
        str(dataset_dir),
        extra_data_dirs=[str(dataset_dir)],
        n_trials=2,
        seed=13,
    )

    baseline = json.loads((out_dir / "baseline.json").read_text())
    best = json.loads((out_dir / "best_params.json").read_text())
    assert baseline["kpis"]["n_dataset_cases"] == 2
    assert len(baseline["cases"]) == 2
    assert len(best["snapshot_hashes"]) == 2
    assert baseline["kpis"]["workload_weight_total"] > 0


def test_multi_dataset_evaluation_is_workload_weighted(monkeypatch) -> None:
    from fl_op.tuning.optuna_tuner import _DatasetCase, _evaluate

    def weighted_chain(rows, **kwargs):
        tasks = rows["tasks"]
        margin = 100.0 if len(tasks) == 1 else 0.0
        return SolverChainResult(
            dispatch=[],
            infeasible=[],
            kpis={"total_estimated_margin_eur": margin},
            greedy_assignment={},
            n_clusters=1,
        )

    monkeypatch.setattr("fl_op.solver.chain.run_solver_chain", weighted_chain)
    small = _DatasetCase(
        data_dir="small",
        snapshot_id="snap-small",
        snapshot_hash="hash-small",
        rows={"tasks": [SimpleNamespace(task_id="s", penalty_per_day=0.0)]},
        enforcement=None,
        workload_weight=1,
    )
    large = _DatasetCase(
        data_dir="large",
        snapshot_id="snap-large",
        snapshot_hash="hash-large",
        rows={
            "tasks": [
                SimpleNamespace(task_id=f"l{i}", penalty_per_day=0.0)
                for i in range(3)
            ]
        },
        enforcement=None,
        workload_weight=3,
    )

    evaluation = _evaluate([small, large], SolverParameters())
    assert evaluation.objective == pytest.approx(25.0)
    assert evaluation.kpis["total_estimated_margin_eur"] == pytest.approx(25.0)
    assert evaluation.kpis["workload_weight_total"] == pytest.approx(4.0)


def test_parallel_tune_defaults_to_local_rdb_storage(
    dataset_dir, tmp_path, monkeypatch
) -> None:
    from fl_op.tuning import optuna_tuner

    monkeypatch.setattr("fl_op.solver.chain.run_solver_chain", _fake_chain)
    monkeypatch.setattr(optuna_tuner, "DATA_ROOT", tmp_path)
    out_dir = optuna_tuner.run_tune(
        str(dataset_dir),
        n_trials=2,
        seed=17,
        n_jobs=2,
    )

    best = json.loads((out_dir / "best_params.json").read_text())
    assert best["n_jobs"] == 2
    assert best["storage"].startswith("sqlite:///")
    assert (out_dir / "study.db").exists()


def test_promote_best_params_creates_reviewed_overlay(tmp_path) -> None:
    from fl_op.tuning.solver_profile import (
        load_tuned_solver_parameters,
        promote_best_params,
        solver_parameters_for_profile,
    )

    best_path = tmp_path / "best_params.json"
    best_path.write_text(
        json.dumps(
            {
                "best_params": {
                    "cluster_target_size": 42,
                    "score_weight_margin": 1.5,
                    "unknown": "ignored",
                },
                "snapshot_hash": "abc",
                "n_trials": 4,
            }
        )
    )
    overlay = tmp_path / "solver-parameters-tuned.json"
    promote_best_params(best_path, output_path=overlay, reviewed_by="tester")

    overrides = load_tuned_solver_parameters(overlay)
    assert overrides["cluster_target_size"] == 42
    assert overrides["score_weight_margin"] == pytest.approx(1.5)
    assert "unknown" not in overrides

    profile = SimpleNamespace(
        allocationPolicy=SimpleNamespace(countPriority=0.25)
    )
    params = solver_parameters_for_profile(profile, tuned_path=overlay)
    assert params.cluster_target_size == 42
    assert params.assignment_count_priority == pytest.approx(0.25)


def test_scoped_tuned_overlay_requires_matching_scope_and_expiry(
    tmp_path, monkeypatch
) -> None:
    from fl_op.tuning import solver_profile

    monkeypatch.setattr(solver_profile, "DATA_ROOT", tmp_path)
    best_path = tmp_path / "best_params.json"
    best_path.write_text(
        json.dumps(
            {
                "best_params": {"cluster_target_size": 44},
                "snapshot_hash": "abc",
                "n_trials": 2,
            }
        )
    )
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    overlay = solver_profile.promote_best_params(
        best_path,
        domain_id="drone_logistics",
        profile_id="drone-logistics",
        adapter_version="0.1.0",
        expires_at=(now + timedelta(days=1)).isoformat(),
    )
    assert overlay == solver_profile.scoped_tuned_solver_profile_path(
        "drone_logistics", "drone-logistics", "0.1.0"
    )
    assert solver_profile.load_tuned_solver_parameters(
        overlay,
        domain_id="drone_logistics",
        profile_id="drone-logistics",
        adapter_version="0.1.0",
        now=now,
    )["cluster_target_size"] == 44
    assert solver_profile.load_tuned_solver_parameters(
        overlay,
        domain_id="agricultural",
        profile_id="agricultural-custom-services",
        adapter_version="0.1.0",
        now=now,
    ) == {}
    assert solver_profile.load_tuned_solver_parameters(
        overlay,
        domain_id="drone_logistics",
        profile_id="drone-logistics",
        adapter_version="0.1.0",
        now=now + timedelta(days=2),
    ) == {}


def test_tune_promote_cli_can_write_scoped_overlay(tmp_path, monkeypatch) -> None:
    from fl_op.tuning import solver_profile

    monkeypatch.setattr(solver_profile, "DATA_ROOT", tmp_path)
    best_path = tmp_path / "best_params.json"
    best_path.write_text(
        json.dumps(
            {
                "best_params": {"cluster_target_size": 45},
                "snapshot_hash": "cli-scoped",
                "n_trials": 2,
            }
        )
    )

    result = CliRunner().invoke(
        cli,
        [
            "tune-promote",
            "--best-params",
            str(best_path),
            "--domain",
            "drone_logistics",
            "--profile",
            "drone-logistics",
            "--adapter-version",
            "0.1.0",
        ],
    )

    assert result.exit_code == 0, result.output
    overlay = solver_profile.scoped_tuned_solver_profile_path(
        "drone_logistics", "drone-logistics", "0.1.0"
    )
    assert overlay.exists()
    assert solver_profile.load_tuned_solver_parameters(
        overlay,
        domain_id="drone_logistics",
        profile_id="drone-logistics",
        adapter_version="0.1.0",
    )["cluster_target_size"] == 45


def test_drone_profile_uses_scoped_overlay_not_shared_overlay(
    tmp_path, monkeypatch
) -> None:
    from fl_op.contracts.registry import FileRegistry
    from fl_op.tuning import solver_profile

    monkeypatch.setattr(solver_profile, "DATA_ROOT", tmp_path)
    best_path = tmp_path / "best_params.json"
    best_path.write_text(
        json.dumps(
            {
                "best_params": {"cluster_target_size": 12},
                "snapshot_hash": "shared",
                "n_trials": 2,
            }
        )
    )
    solver_profile.promote_best_params(
        best_path,
        output_path=solver_profile.default_tuned_solver_profile_path(),
    )
    profile = FileRegistry().get_profile("drone-logistics")
    assert solver_profile.solver_parameters_for_profile(profile).cluster_target_size == 36

    best_path.write_text(
        json.dumps(
            {
                "best_params": {"cluster_target_size": 44},
                "snapshot_hash": "scoped",
                "n_trials": 2,
            }
        )
    )
    solver_profile.promote_best_params(
        best_path,
        domain_id="drone_logistics",
        profile_id="drone-logistics",
        adapter_version="0.1.0",
    )
    assert solver_profile.solver_parameters_for_profile(profile).cluster_target_size == 44


def test_mlflow_logging_disabled_returns_none(monkeypatch) -> None:
    from fl_op.tuning.mlflow_logger import log_solver_run

    monkeypatch.setattr(constants, "MLFLOW_LOGGING_ENABLED", False)
    assert log_solver_run("run", {"p": 1}, {"m": 2.0}) is None


def test_mlflow_logging_filters_non_numeric_metrics(tmp_path, monkeypatch) -> None:
    from fl_op.tuning import mlflow_logger

    monkeypatch.setattr(constants, "MLFLOW_LOGGING_ENABLED", True)
    monkeypatch.setattr(mlflow_logger, "DATA_ROOT", tmp_path)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

    captured: dict[str, dict] = {}

    @contextlib.contextmanager
    def fake_start_run(run_name=None):
        yield types.SimpleNamespace(info=types.SimpleNamespace(run_id="run-1"))

    fake_mlflow = types.SimpleNamespace(
        set_tracking_uri=lambda uri: captured.setdefault("uri", uri),
        set_experiment=lambda name: captured.setdefault("experiment", name),
        start_run=fake_start_run,
        log_params=lambda params: captured.setdefault("params", params),
        log_metrics=lambda metrics: captured.setdefault("metrics", metrics),
        set_tags=lambda tags: captured.setdefault("tags", tags),
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    run_id = mlflow_logger.log_solver_run(
        run_name="test-run",
        params={"cluster_target_size": 35},
        metrics={
            "objective": 12.5,
            "n_assigned": 4,
            "status_text": "OPTIMAL",
            "flag": True,
        },
        tags={"phase": "trial"},
    )

    assert run_id == "run-1"
    # MLflow metrics are floats: strings and bools must be dropped.
    assert captured["metrics"] == {"objective": 12.5, "n_assigned": 4.0}
    assert captured["params"] == {"cluster_target_size": "35"}
    assert captured["tags"] == {"phase": "trial"}
    assert captured["uri"].startswith("sqlite:///")


# ---------------------------------------------------------------------------
# Experiment-maturity additions: auto parallelism + real instability
# ---------------------------------------------------------------------------


def _case(n_tasks: int, primes=None):
    from fl_op.tuning.optuna_tuner import _DatasetCase

    rows = {"tasks": [SimpleNamespace(task_id=f"t{i}", penalty_per_day=0.0)
                      for i in range(n_tasks)]}
    if primes is not None:
        rows["prime_movers"] = [SimpleNamespace(asset_id=pid) for pid in primes]
    return _DatasetCase(
        data_dir="d", snapshot_id="s", snapshot_hash="h",
        rows=rows, enforcement=None, workload_weight=max(1, n_tasks),
    )


class TestAutoTuningParallelism:
    def test_cpu_bound_when_memory_is_ample(self, monkeypatch):
        from fl_op.solver import cluster_pool
        from fl_op.tuning import optuna_tuner

        monkeypatch.setattr(cluster_pool, "available_memory_mb", lambda: 1_000_000.0)
        monkeypatch.setattr("os.cpu_count", lambda: 4)
        assert optuna_tuner._auto_n_jobs([_case(10)]) == 4

    def test_memory_bound_below_cpu(self, monkeypatch):
        from fl_op.solver import cluster_pool
        from fl_op.tuning import optuna_tuner

        monkeypatch.setattr(optuna_tuner.constants, "TUNE_JOB_BASE_MEMORY_MB", 1000.0)
        monkeypatch.setattr(optuna_tuner.constants, "TUNE_JOB_MEMORY_MB_PER_TASK", 0.0)
        monkeypatch.setattr(optuna_tuner.constants, "SOLVER_MEMORY_HEADROOM_PCT", 0.0)
        monkeypatch.setattr(cluster_pool, "available_memory_mb", lambda: 2500.0)
        monkeypatch.setattr("os.cpu_count", lambda: 8)
        # 2500 / 1000 = 2 worker footprints fit.
        assert optuna_tuner._auto_n_jobs([_case(10)]) == 2

    def test_bigger_dataset_reduces_parallelism(self, monkeypatch):
        from fl_op.solver import cluster_pool
        from fl_op.tuning import optuna_tuner

        monkeypatch.setattr(optuna_tuner.constants, "TUNE_JOB_BASE_MEMORY_MB", 500.0)
        monkeypatch.setattr(optuna_tuner.constants, "TUNE_JOB_MEMORY_MB_PER_TASK", 10.0)
        monkeypatch.setattr(optuna_tuner.constants, "SOLVER_MEMORY_HEADROOM_PCT", 0.0)
        monkeypatch.setattr(cluster_pool, "available_memory_mb", lambda: 6000.0)
        monkeypatch.setattr("os.cpu_count", lambda: 16)
        small = optuna_tuner._auto_n_jobs([_case(50)])   # per job 1000 -> 6
        large = optuna_tuner._auto_n_jobs([_case(500)])  # per job 5500 -> 1
        assert small > large

    def test_unmeasurable_memory_falls_back_to_one(self, monkeypatch):
        from fl_op.solver import cluster_pool
        from fl_op.tuning import optuna_tuner

        monkeypatch.setattr(cluster_pool, "available_memory_mb", lambda: None)
        assert optuna_tuner._auto_n_jobs([_case(10)]) == 1


class TestPerturbedInstability:
    def _scenario_chain(self):
        """Base plan vs the plan after the busiest mover (v1) is removed."""
        base = [
            {"task_id": "t1", "prime_asset_id": "v1", "related_asset_id": "i1"},
            {"task_id": "t2", "prime_asset_id": "v1", "related_asset_id": "i1"},
            {"task_id": "t3", "prime_asset_id": "v2", "related_asset_id": "i2"},
            {"task_id": "t4", "prime_asset_id": "v3", "related_asset_id": "i3"},
        ]
        perturbed = [
            {"task_id": "t1", "prime_asset_id": "v2", "related_asset_id": "i1"},
            {"task_id": "t2", "prime_asset_id": "v3", "related_asset_id": "i1"},
            {"task_id": "t3", "prime_asset_id": "v2", "related_asset_id": "i2"},
            # t4 cascades from v3 to v2 despite v3 still being available -> churn.
            {"task_id": "t4", "prime_asset_id": "v2", "related_asset_id": "i3"},
        ]

        def chain(rows, enforcement=None, parameters=None, **kwargs):
            primes = {getattr(m, "asset_id", "") for m in rows.get("prime_movers", [])}
            dispatch = base if "v1" in primes else perturbed
            return SolverChainResult(
                dispatch=list(dispatch), infeasible=[],
                kpis={"total_estimated_margin_eur": 0.0},
                greedy_assignment={}, n_clusters=1,
            )

        return chain

    def test_avoidable_churn_times_change_penalty(self, monkeypatch):
        from fl_op.tuning import optuna_tuner

        monkeypatch.setattr(
            "fl_op.solver.chain.run_solver_chain", self._scenario_chain()
        )
        case = _case(0, primes=["v1", "v2", "v3"])
        params = SolverParameters(rolling_change_penalty=1000)
        evaluation = optuna_tuner._evaluate_case(case, params, measure_instability=True)
        # Only t4 is avoidable churn (t1/t2 had to move with v1); 1 x 1000.
        assert evaluation.instability == pytest.approx(1000.0)

    def test_disabled_keeps_periodic_zero_instability(self, monkeypatch):
        from fl_op.tuning import optuna_tuner

        monkeypatch.setattr(
            "fl_op.solver.chain.run_solver_chain", self._scenario_chain()
        )
        case = _case(0, primes=["v1", "v2", "v3"])
        params = SolverParameters(rolling_change_penalty=1000)
        evaluation = optuna_tuner._evaluate_case(case, params, measure_instability=False)
        assert evaluation.instability == 0.0

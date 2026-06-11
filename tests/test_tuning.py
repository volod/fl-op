"""Parameter tuning: Optuna study wiring, artifacts, and MLflow gating."""

import contextlib
import json
import pathlib
import sys
import types

import pytest

from fl_op.core import constants
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

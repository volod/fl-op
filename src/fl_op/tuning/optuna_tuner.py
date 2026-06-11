"""Optuna tuning of solver parameters against a recorded KPI baseline.

One tuning run builds the canonical snapshot once, records the baseline KPIs
of the default SolverParameters (at trial-scale time budget, for
comparability), then runs a seeded TPE study over the tunable parameters
(cluster target size, greedy score weights, per-cluster time limit). Each
trial executes the full solver chain on the same projected rows.

Objective (maximized): total estimated margin minus the lateness-penalty
exposure of unassigned tasks, so a parameter set cannot win by dropping
penalty-heavy work.

Artifacts under $DATA_DIR/tune/<run_timestamp>/: baseline.json, trials.json,
best_params.json. With MLFLOW_LOGGING_ENABLED=1 every trial and the baseline
are additionally logged as MLflow runs.
"""

import dataclasses
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.canonical.enums import PlanningMode
from fl_op.core import constants
from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.solver.parameters import SolverParameters

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class _Evaluation:
    objective: float
    kpis: dict[str, Any]
    unassigned_penalty_eur_per_day: float


def _evaluate(
    rows: dict[str, list[Any]],
    enforcement: Any,
    parameters: SolverParameters,
) -> _Evaluation:
    """Run the solver chain once and score the outcome."""
    from fl_op.solver.chain import run_solver_chain
    from fl_op.solver.inputs import SECTION_TASKS

    result = run_solver_chain(
        dict(rows), enforcement=enforcement, parameters=parameters
    )
    penalty_by_task = {
        order.task_id: float(order.penalty_per_day or 0.0)
        for order in rows.get(SECTION_TASKS, [])
    }
    unassigned_penalty = sum(
        penalty_by_task.get(record["task_id"], 0.0) for record in result.infeasible
    )
    margin = float(result.kpis.get("total_estimated_margin_eur", 0.0))
    return _Evaluation(
        objective=margin - unassigned_penalty,
        kpis=result.kpis,
        unassigned_penalty_eur_per_day=unassigned_penalty,
    )


def _trial_parameters(trial: Any, baseline_limit_s: int) -> SolverParameters:
    return SolverParameters(
        cluster_target_size=trial.suggest_int(
            "cluster_target_size",
            constants.TUNE_CLUSTER_TARGET_SIZE_MIN,
            constants.TUNE_CLUSTER_TARGET_SIZE_MAX,
        ),
        score_weight_margin=trial.suggest_float(
            "score_weight_margin",
            constants.TUNE_SCORE_WEIGHT_MIN,
            constants.TUNE_SCORE_WEIGHT_MAX,
            log=True,
        ),
        score_weight_reposition=trial.suggest_float(
            "score_weight_reposition",
            constants.TUNE_SCORE_WEIGHT_MIN,
            constants.TUNE_SCORE_WEIGHT_MAX,
            log=True,
        ),
        cluster_solve_time_limit_s=trial.suggest_int(
            "cluster_solve_time_limit_s",
            constants.TUNE_TIME_LIMIT_MIN_S,
            max(constants.TUNE_TIME_LIMIT_MIN_S, baseline_limit_s),
        ),
    )


def run_tune(
    data_dir: str,
    n_trials: Optional[int] = None,
    seed: Optional[int] = None,
) -> pathlib.Path:
    """Tune solver parameters with Optuna; returns the artifact directory."""
    import optuna

    from fl_op.contracts.registry import FileRegistry
    from fl_op.planning.artifacts import run_timestamp, write_json
    from fl_op.snapshot.builder import SnapshotBuilder
    from fl_op.solver.enforcement import EnforcementPolicy
    from fl_op.solver.inputs import build_solver_inputs
    from fl_op.tuning.mlflow_logger import log_solver_run

    n_trials = n_trials if n_trials is not None else constants.TUNE_N_TRIALS
    seed = seed if seed is not None else constants.TUNE_SEED
    out_dir = DATA_ROOT / "tune" / run_timestamp()

    registry = FileRegistry()
    snapshot = SnapshotBuilder(registry).build(data_dir, PlanningMode.PERIODIC)
    rows = build_solver_inputs(snapshot, registry)
    profile_id = registry.active_profile_id
    enforcement = (
        EnforcementPolicy.from_profile(registry.get_profile(profile_id))
        if profile_id
        else None
    )

    # Recorded KPI baseline: default parameters at the trial-scale time budget
    # so trials and baseline compare under one compute budget.
    baseline_limit_s = min(
        SolverParameters().cluster_solve_time_limit_s, constants.TUNE_TIME_LIMIT_MAX_S
    )
    baseline_params = SolverParameters(cluster_solve_time_limit_s=baseline_limit_s)
    baseline = _evaluate(rows, enforcement, baseline_params)
    logger.info(
        "Tuning baseline: objective %.2f (margin %.2f, unassigned penalty %.2f)",
        baseline.objective,
        baseline.kpis.get("total_estimated_margin_eur", 0.0),
        baseline.unassigned_penalty_eur_per_day,
    )
    log_solver_run(
        run_name="tune-baseline",
        params=baseline_params.as_dict(),
        metrics={"objective": baseline.objective, **baseline.kpis},
        tags={"phase": "baseline", "snapshot_hash": snapshot.snapshot_hash},
    )

    trial_records: list[dict[str, Any]] = []

    def objective(trial: "optuna.Trial") -> float:
        parameters = _trial_parameters(trial, baseline_limit_s)
        evaluation = _evaluate(rows, enforcement, parameters)
        trial_records.append(
            {
                "number": trial.number,
                "params": parameters.as_dict(),
                "objective": evaluation.objective,
                "kpis": evaluation.kpis,
                "unassigned_penalty_eur_per_day": (
                    evaluation.unassigned_penalty_eur_per_day
                ),
            }
        )
        log_solver_run(
            run_name=f"tune-trial-{trial.number:03d}",
            params=parameters.as_dict(),
            metrics={"objective": evaluation.objective, **evaluation.kpis},
            tags={"phase": "trial", "snapshot_hash": snapshot.snapshot_hash},
        )
        return evaluation.objective

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed)
    )
    study.optimize(objective, n_trials=n_trials)

    best = study.best_trial
    best_record = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "best_params": dict(best.params),
        "best_objective": best.value,
        "baseline_objective": baseline.objective,
        "improvement_over_baseline": round(best.value - baseline.objective, 2),
        "n_trials": n_trials,
        "seed": seed,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": snapshot.snapshot_hash,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    write_json(
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "baseline_params": baseline_params.as_dict(),
            "objective": baseline.objective,
            "kpis": baseline.kpis,
            "unassigned_penalty_eur_per_day": baseline.unassigned_penalty_eur_per_day,
        },
        out_dir / "baseline.json",
    )
    write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, "trials": trial_records},
        out_dir / "trials.json",
    )
    write_json(best_record, out_dir / "best_params.json")

    logger.info(
        "Tuning complete: best objective %.2f vs baseline %.2f "
        "(improvement %.2f) over %d trials -> %s",
        best.value,
        baseline.objective,
        best.value - baseline.objective,
        n_trials,
        out_dir,
    )
    return out_dir

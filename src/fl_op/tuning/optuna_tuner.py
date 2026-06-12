"""Optuna tuning of solver parameters against recorded KPI baselines.

One tuning run builds one or more canonical snapshots, records the baseline
KPIs of the default SolverParameters (at trial-scale time budget, for
comparability), then runs a seeded TPE study over the tunable parameters
(cluster target size, greedy score weights, per-cluster time limit). Each
trial executes the full solver chain on every projected dataset case.

Primary objective (maximized): average estimated margin minus the
lateness-penalty exposure of unassigned tasks, so a parameter set cannot win by
dropping penalty-heavy work. Multi-objective runs also minimize plan
instability and wall time, retaining a Pareto frontier while still reporting a
recommended primary-best parameter set.

Artifacts under $DATA_DIR/tune/<run_timestamp>/: baseline.json, trials.json,
best_params.json. With MLFLOW_LOGGING_ENABLED=1 every trial and the baseline
are additionally logged as MLflow runs.
"""

import copy
import dataclasses
import logging
import pathlib
import threading
import time
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
    instability: float
    wall_time_s: float
    kpis: dict[str, Any]
    unassigned_penalty_eur_per_day: float
    cases: list[dict[str, Any]]

    @property
    def objectives(self) -> dict[str, float]:
        return {
            "business_objective": self.objective,
            "plan_instability_penalty": self.instability,
            "wall_time_s": self.wall_time_s,
        }


@dataclasses.dataclass
class _DatasetCase:
    data_dir: str
    snapshot_id: str
    snapshot_hash: str
    rows: dict[str, list[Any]]
    enforcement: Any


def _evaluate(
    cases: list[_DatasetCase],
    parameters: SolverParameters,
) -> _Evaluation:
    """Run the solver chain over every dataset case and score the outcome."""
    evaluations = [_evaluate_case(case, parameters) for case in cases]
    n = max(1, len(evaluations))
    return _Evaluation(
        objective=sum(e.objective for e in evaluations) / n,
        instability=sum(e.instability for e in evaluations) / n,
        wall_time_s=sum(e.wall_time_s for e in evaluations),
        kpis=_average_numeric_kpis([e.kpis for e in evaluations]),
        unassigned_penalty_eur_per_day=sum(
            e.unassigned_penalty_eur_per_day for e in evaluations
        )
        / n,
        cases=[
            {
                "data_dir": case.data_dir,
                "snapshot_id": case.snapshot_id,
                "snapshot_hash": case.snapshot_hash,
                "objective": evaluation.objective,
                "objectives": evaluation.objectives,
                "kpis": evaluation.kpis,
                "unassigned_penalty_eur_per_day": (
                    evaluation.unassigned_penalty_eur_per_day
                ),
            }
            for case, evaluation in zip(cases, evaluations)
        ],
    )


def _evaluate_case(
    case: _DatasetCase,
    parameters: SolverParameters,
) -> _Evaluation:
    """Run the solver chain once and score one dataset case."""
    from fl_op.solver.chain import run_solver_chain
    from fl_op.solver.inputs import SECTION_TASKS

    started = time.perf_counter()
    result = run_solver_chain(
        copy.deepcopy(case.rows), enforcement=case.enforcement, parameters=parameters
    )
    wall_time_s = round(time.perf_counter() - started, 6)
    penalty_by_task = {
        order.task_id: float(order.penalty_per_day or 0.0)
        for order in case.rows.get(SECTION_TASKS, [])
    }
    unassigned_penalty = sum(
        penalty_by_task.get(record["task_id"], 0.0) for record in result.infeasible
    )
    margin = float(result.kpis.get("total_estimated_margin_eur", 0.0))
    instability = float(result.kpis.get("plan_instability_penalty", 0.0))
    kpis = dict(result.kpis)
    kpis["wall_time_s"] = wall_time_s
    kpis["plan_instability_penalty"] = instability
    return _Evaluation(
        objective=margin - unassigned_penalty,
        instability=instability,
        wall_time_s=wall_time_s,
        kpis=kpis,
        unassigned_penalty_eur_per_day=unassigned_penalty,
        cases=[],
    )


def _average_numeric_kpis(records: list[dict[str, Any]]) -> dict[str, Any]:
    keys = sorted({key for record in records for key in record})
    averaged: dict[str, Any] = {}
    for key in keys:
        values = [
            record[key]
            for record in records
            if isinstance(record.get(key), (int, float))
            and not isinstance(record.get(key), bool)
        ]
        if values:
            averaged[key] = sum(float(v) for v in values) / len(values)
    averaged["n_dataset_cases"] = len(records)
    return averaged


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
    data_dir: str | list[str],
    n_trials: Optional[int] = None,
    seed: Optional[int] = None,
    extra_data_dirs: Optional[list[str]] = None,
    n_jobs: Optional[int] = None,
    storage: Optional[str] = None,
    multi_objective: bool = True,
    study_name: Optional[str] = None,
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
    n_jobs = n_jobs if n_jobs is not None else constants.TUNE_N_JOBS
    out_dir = DATA_ROOT / "tune" / run_timestamp()
    out_dir.mkdir(parents=True, exist_ok=True)

    registry = FileRegistry()
    profile_id = registry.active_profile_id
    enforcement = (
        EnforcementPolicy.from_profile(registry.get_profile(profile_id))
        if profile_id
        else None
    )
    data_dirs = _data_dirs(data_dir, extra_data_dirs)
    builder = SnapshotBuilder(registry)
    cases = []
    for case_dir in data_dirs:
        snapshot = builder.build(case_dir, PlanningMode.PERIODIC)
        cases.append(
            _DatasetCase(
                data_dir=case_dir,
                snapshot_id=snapshot.snapshot_id,
                snapshot_hash=snapshot.snapshot_hash,
                rows=build_solver_inputs(snapshot, registry),
                enforcement=enforcement,
            )
        )
    snapshot_hashes = [case.snapshot_hash for case in cases]

    # Recorded KPI baseline: default parameters at the trial-scale time budget
    # so trials and baseline compare under one compute budget.
    baseline_limit_s = min(
        SolverParameters().cluster_solve_time_limit_s, constants.TUNE_TIME_LIMIT_MAX_S
    )
    baseline_params = SolverParameters(cluster_solve_time_limit_s=baseline_limit_s)
    baseline = _evaluate(cases, baseline_params)
    logger.info(
        "Tuning baseline: objective %.2f (margin %.2f, unassigned penalty %.2f, "
        "wall %.3fs, datasets %d)",
        baseline.objective,
        baseline.kpis.get("total_estimated_margin_eur", 0.0),
        baseline.unassigned_penalty_eur_per_day,
        baseline.wall_time_s,
        len(cases),
    )
    log_solver_run(
        run_name="tune-baseline",
        params=baseline_params.as_dict(),
        metrics={
            "objective": baseline.objective,
            **baseline.objectives,
            **baseline.kpis,
        },
        tags={
            "phase": "baseline",
            "snapshot_hashes": ",".join(snapshot_hashes),
            "n_dataset_cases": str(len(cases)),
        },
    )

    trial_records: list[dict[str, Any]] = []
    records_lock = threading.Lock()

    def objective(trial: "optuna.Trial") -> float | tuple[float, float, float]:
        parameters = _trial_parameters(trial, baseline_limit_s)
        evaluation = _evaluate(cases, parameters)
        record = {
            "number": trial.number,
            "params": parameters.as_dict(),
            "objective": evaluation.objective,
            "objectives": evaluation.objectives,
            "kpis": evaluation.kpis,
            "cases": evaluation.cases,
            "unassigned_penalty_eur_per_day": (
                evaluation.unassigned_penalty_eur_per_day
            ),
        }
        with records_lock:
            trial_records.append(record)
        log_solver_run(
            run_name=f"tune-trial-{trial.number:03d}",
            params=parameters.as_dict(),
            metrics={
                "objective": evaluation.objective,
                **evaluation.objectives,
                **evaluation.kpis,
            },
            tags={
                "phase": "trial",
                "snapshot_hashes": ",".join(snapshot_hashes),
                "n_dataset_cases": str(len(cases)),
            },
        )
        if multi_objective:
            return evaluation.objective, evaluation.instability, evaluation.wall_time_s
        return evaluation.objective

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    storage_uri = storage or constants.TUNE_STORAGE_URI or None
    if n_jobs != 1 and storage_uri is None:
        storage_uri = f"sqlite:///{out_dir / 'study.db'}"
    directions = (
        ["maximize", "minimize", "minimize"] if multi_objective else ["maximize"]
    )
    study = optuna.create_study(
        directions=directions,
        sampler=optuna.samplers.TPESampler(seed=seed),
        storage=storage_uri,
        study_name=study_name or f"fl-op-tune-{out_dir.name}",
        load_if_exists=bool(storage_uri),
    )
    study.optimize(objective, n_trials=n_trials, n_jobs=n_jobs)

    best = _recommended_trial(study, multi_objective)
    best_values = list(best.values or [best.value])
    best_objective = float(best_values[0])
    pareto_trials = [
        {
            "number": trial.number,
            "params": dict(trial.params),
            "values": [float(v) for v in (trial.values or [])],
        }
        for trial in (study.best_trials if multi_objective else [best])
    ]
    best_record = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "best_params": dict(best.params),
        "best_objective": best_objective,
        "best_values": best_values,
        "objective_names": [
            "business_objective",
            "plan_instability_penalty",
            "wall_time_s",
        ]
        if multi_objective
        else ["business_objective"],
        "objective_directions": directions,
        "pareto_trials": pareto_trials,
        "baseline_objective": baseline.objective,
        "improvement_over_baseline": round(best_objective - baseline.objective, 2),
        "n_trials": n_trials,
        "n_jobs": n_jobs,
        "seed": seed,
        "storage": storage_uri or "",
        "dataset_dirs": data_dirs,
        "snapshot_ids": [case.snapshot_id for case in cases],
        "snapshot_hashes": snapshot_hashes,
        # Backward-compatible aliases for single-dataset consumers.
        "snapshot_id": cases[0].snapshot_id,
        "snapshot_hash": cases[0].snapshot_hash,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    write_json(
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "baseline_params": baseline_params.as_dict(),
            "objective": baseline.objective,
            "objectives": baseline.objectives,
            "kpis": baseline.kpis,
            "cases": baseline.cases,
            "unassigned_penalty_eur_per_day": baseline.unassigned_penalty_eur_per_day,
            "dataset_dirs": data_dirs,
            "snapshot_hashes": snapshot_hashes,
        },
        out_dir / "baseline.json",
    )
    write_json(
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "multi_objective": multi_objective,
            "objective_names": best_record["objective_names"],
            "objective_directions": directions,
            "trials": sorted(trial_records, key=lambda r: r["number"]),
        },
        out_dir / "trials.json",
    )
    write_json(best_record, out_dir / "best_params.json")

    logger.info(
        "Tuning complete: best objective %.2f vs baseline %.2f "
        "(improvement %.2f) over %d trials -> %s",
        best_objective,
        baseline.objective,
        best_objective - baseline.objective,
        n_trials,
        out_dir,
    )
    return out_dir


def _data_dirs(
    data_dir: str | list[str],
    extra_data_dirs: Optional[list[str]],
) -> list[str]:
    if isinstance(data_dir, list):
        dirs = [str(path) for path in data_dir]
    else:
        dirs = [str(data_dir)]
    dirs.extend(str(path) for path in (extra_data_dirs or []))
    if not dirs:
        raise ValueError("At least one dataset directory is required")
    return dirs


def _recommended_trial(study: Any, multi_objective: bool) -> Any:
    if not multi_objective:
        return study.best_trial
    return max(
        study.best_trials,
        key=lambda trial: (
            float((trial.values or [float("-inf")])[0]),
            -float((trial.values or [0.0, float("inf")])[1]),
            -float((trial.values or [0.0, 0.0, float("inf")])[2]),
        ),
    )

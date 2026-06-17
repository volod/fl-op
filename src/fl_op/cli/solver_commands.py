"""Batch solver CLI commands: solve, analyse, reschedule, query-contract."""

import click

from fl_op.cli.options import (
    data_option,
    resolve_data_dir,
    resolve_schedule_dir,
    schedule_option,
)


@click.command("solve")
@data_option
def solve(data: str) -> None:
    """Run full fleet scheduling solver."""
    from fl_op.solver.solve_pipeline import run_solve

    run_solve(data_dir=str(resolve_data_dir(data)))


@click.command("analyse")
@schedule_option
def analyse(schedule: str) -> None:
    """Pretty-print statistics for a completed solver run."""
    from fl_op.solver.analysis import run_analyse

    run_analyse(schedule_dir=str(resolve_schedule_dir(schedule)))


@click.command("reschedule")
@data_option
@schedule_option
@click.option(
    "--events",
    required=False,
    default=None,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Path to events JSON file (mark_started, etc.).",
)
def reschedule(data: str, schedule: str, events: str | None) -> None:
    """Re-run solver after in-progress updates."""
    from fl_op.solver.reschedule_pipeline import run_reschedule

    run_reschedule(
        data_dir=str(resolve_data_dir(data)),
        schedule_dir=str(resolve_schedule_dir(schedule)),
        events_path=events,
    )


@click.command("tune")
@data_option
@click.option(
    "--extra-data",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Additional dataset directory to include in the averaged objective.",
)
@click.option(
    "--trials",
    default=None,
    type=int,
    help="Optuna trials to run (default: TUNE_N_TRIALS).",
)
@click.option(
    "--seed",
    default=None,
    type=int,
    help="TPE sampler seed for reproducibility (default: TUNE_SEED).",
)
@click.option(
    "--jobs",
    default=None,
    type=int,
    help="Parallel Optuna workers (default: TUNE_N_JOBS; 0 = auto from CPU/memory).",
)
@click.option(
    "--measure-instability",
    is_flag=True,
    default=False,
    help="Measure real plan churn via a perturbed re-solve (slower; two solves per trial).",
)
@click.option(
    "--storage",
    default=None,
    help="Optuna RDB storage URI; n_jobs>1 defaults to tune/<run>/study.db.",
)
@click.option(
    "--single-objective",
    is_flag=True,
    default=False,
    help="Optimize only the business objective; default keeps a Pareto frontier.",
)
def tune(
    data: str,
    extra_data: tuple[str, ...],
    trials: int | None,
    seed: int | None,
    jobs: int | None,
    storage: str | None,
    single_objective: bool,
    measure_instability: bool,
) -> None:
    """Tune solver parameters with Optuna against a recorded KPI baseline."""
    from fl_op.tuning.optuna_tuner import run_tune

    run_tune(
        data_dir=str(resolve_data_dir(data)),
        extra_data_dirs=[str(resolve_data_dir(path)) for path in extra_data],
        n_trials=trials,
        seed=seed,
        n_jobs=jobs,
        storage=storage,
        multi_objective=not single_objective,
        measure_instability=measure_instability,
    )


@click.command("tune-promote")
@click.option(
    "--best-params",
    required=True,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Path to a tuning run's best_params.json.",
)
@click.option(
    "--out",
    default=None,
    type=click.Path(dir_okay=False, resolve_path=True),
    help="Reviewed artifact path (default: DATA_DIR/tune/solver-parameters-tuned.json).",
)
@click.option("--reviewed-by", default=None, help="Reviewer recorded in the artifact.")
@click.option("--notes", default=None, help="Review notes recorded in the artifact.")
@click.option("--domain", "domain_id", default=None, help="Optional domain scope.")
@click.option("--profile", "profile_id", default=None, help="Optional profile scope.")
@click.option(
    "--adapter-version",
    default=None,
    help="Optional adapter-version scope.",
)
@click.option(
    "--expires-at",
    default=None,
    help="Optional ISO-8601 expiry timestamp for the scoped overlay.",
)
def tune_promote(
    best_params: str,
    out: str | None,
    reviewed_by: str | None,
    notes: str | None,
    domain_id: str | None,
    profile_id: str | None,
    adapter_version: str | None,
    expires_at: str | None,
) -> None:
    """Promote best_params.json into the reviewed tuned solver profile."""
    import pathlib

    from fl_op.tuning.solver_profile import promote_best_params

    promote_best_params(
        pathlib.Path(best_params),
        output_path=pathlib.Path(out) if out else None,
        reviewed_by=reviewed_by,
        notes=notes,
        domain_id=domain_id,
        profile_id=profile_id,
        adapter_version=adapter_version,
        expires_at=expires_at,
    )


@click.command("query-contract")
@data_option
@schedule_option
@click.option(
    "--order",
    required=True,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Path to new order JSON file.",
)
def query_contract(data: str, schedule: str, order: str) -> None:
    """Evaluate feasibility and margin estimate for a new order."""
    from fl_op.solver.query_pipeline import run_query

    run_query(
        data_dir=str(resolve_data_dir(data)),
        schedule_dir=str(resolve_schedule_dir(schedule)),
        order_path=order,
    )


def register_solver_commands(cli: click.Group) -> None:
    for command in (solve, analyse, reschedule, tune, tune_promote, query_contract):
        cli.add_command(command)

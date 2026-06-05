"""Legacy solve/analyse/reschedule/query CLI commands."""

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
    for command in (solve, analyse, reschedule, query_contract):
        cli.add_command(command)

import logging
import os
import pathlib
import sys
from typing import Any, Callable, TypeVar


def _load_dotenv() -> None:
    """Load .env into os.environ (stdlib only, does not override existing vars)."""
    env_file = pathlib.Path(".env")
    if not env_file.exists():
        return
    with env_file.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


_load_dotenv()

import click

from fl_op.core.constants import (
    DEFAULT_GENERATE_DEPOTS,
    DEFAULT_GENERATE_IMPLEMENTS,
    DEFAULT_GENERATE_ORDERS,
    DEFAULT_GENERATE_VEHICLES,
)
from fl_op.core.paths import resolve_latest

logger = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])
INTERRUPTED_EXIT_CODE = 130

_LOG_LEVEL_ENV = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
_SEED_ENV: int | None = int(os.environ["SEED"]) if os.environ.get("SEED") else None


def _data_option(func: F) -> F:
    return click.option(
        "--data",
        required=True,
        type=str,
        help="Path to dataset directory, or 'latest' for the most recent generate-data run.",
    )(func)


def _schedule_option(func: F) -> F:
    return click.option(
        "--schedule",
        required=True,
        type=str,
        help="Path to solve output directory, or 'latest' for the most recent solve run.",
    )(func)


@click.group()
@click.option("--verbose", is_flag=True, default=False, help="Enable debug logging.")
def cli(verbose: bool) -> None:
    """fl-op: agricultural fleet optimization CLI."""
    level = logging.DEBUG if verbose else _LOG_LEVEL_ENV
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=level,
    )


@cli.command("generate-data")
@click.option(
    "--vehicles",
    default=DEFAULT_GENERATE_VEHICLES,
    show_default=True,
    type=int,
)
@click.option(
    "--implements",
    default=DEFAULT_GENERATE_IMPLEMENTS,
    show_default=True,
    type=int,
)
@click.option(
    "--orders",
    default=DEFAULT_GENERATE_ORDERS,
    show_default=True,
    type=int,
)
@click.option(
    "--depots",
    default=DEFAULT_GENERATE_DEPOTS,
    show_default=True,
    type=int,
)
@click.option(
    "--seed",
    default=_SEED_ENV,
    show_default=True,
    type=int,
    help="Random seed for reproducibility. Defaults to $SEED env var if set.",
)
@click.option(
    "--data-path",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Directory with real fleet CSVs (vehicles.csv, implements.csv, orders.csv, depots.csv).",
)
def generate_data(
    vehicles: int,
    implements: int,
    orders: int,
    depots: int,
    seed: int | None,
    data_path: str | None,
) -> None:
    """Generate synthetic (or augmented real) fleet dataset."""
    from fl_op.data.generator import run_generate

    run_generate(
        n_vehicles=vehicles,
        n_implements=implements,
        n_orders=orders,
        n_depots=depots,
        seed=seed,
        data_path=data_path,
    )


@cli.command("solve")
@_data_option
def solve(data: str) -> None:
    """Run full fleet scheduling solver."""
    from fl_op.solver.solve_pipeline import run_solve

    data_dir = resolve_latest(data, "generate-data")
    run_solve(data_dir=str(data_dir))


@cli.command("analyse")
@_schedule_option
def analyse(schedule: str) -> None:
    """Pretty-print statistics for a completed solver run."""
    from fl_op.solver.analysis import run_analyse

    schedule_dir = resolve_latest(schedule, "solve")
    run_analyse(schedule_dir=str(schedule_dir))


@cli.command("reschedule")
@_data_option
@_schedule_option
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

    data_dir = resolve_latest(data, "generate-data")
    schedule_dir = resolve_latest(schedule, "solve")
    run_reschedule(data_dir=str(data_dir), schedule_dir=str(schedule_dir), events_path=events)


@cli.command("query-contract")
@_data_option
@_schedule_option
@click.option(
    "--order",
    required=True,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Path to new order JSON file.",
)
def query_contract(data: str, schedule: str, order: str) -> None:
    """Evaluate feasibility and margin estimate for a new order against current schedule."""
    from fl_op.solver.query_pipeline import run_query

    data_dir = resolve_latest(data, "generate-data")
    schedule_dir = resolve_latest(schedule, "solve")
    run_query(data_dir=str(data_dir), schedule_dir=str(schedule_dir), order_path=order)


def _run_cli(command: click.Command, args: list[str] | None = None) -> None:
    """Run a Click command with consistent interrupt handling."""
    try:
        command.main(args=args, standalone_mode=False)
    except click.Abort:
        click.echo(
            "Interrupted: pipeline stopped before completing the current command.",
            err=True,
        )
        raise SystemExit(INTERRUPTED_EXIT_CODE) from None
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from None


def main() -> None:
    """Console-script entry point."""
    _run_cli(cli)


if __name__ == "__main__":
    main()

"""Root Click application."""

import logging

import click

from fl_op.cli.bootstrap import load_dotenv, log_level_from_env, run_cli

load_dotenv()

from fl_op.cli.data_commands import register_data_commands  # noqa: E402
from fl_op.cli.planning_commands import register_planning_commands  # noqa: E402
from fl_op.cli.solver_commands import register_solver_commands  # noqa: E402


@click.group()
@click.option("--verbose", is_flag=True, default=False, help="Enable debug logging.")
def cli(verbose: bool) -> None:
    """fl-op: agricultural fleet optimization CLI."""
    level = logging.DEBUG if verbose else log_level_from_env()
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=level,
    )


register_data_commands(cli)
register_solver_commands(cli)
register_planning_commands(cli)


def main() -> None:
    """Console-script entry point."""
    run_cli(cli)

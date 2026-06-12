"""Dataset generation CLI commands."""

import os

import click

from fl_op.contracts.registry import FileRegistry
from fl_op.core.constants import (
    DEFAULT_DATA_FORMAT,
    DEFAULT_GENERATE_DEPOTS,
    DEFAULT_GENERATE_IMPLEMENTS,
    DEFAULT_GENERATE_ORDERS,
    DEFAULT_GENERATE_VEHICLES,
)

_SEED_ENV: int | None = int(os.environ["SEED"]) if os.environ.get("SEED") else None


def _domain_help() -> str:
    try:
        domains = ", ".join(FileRegistry().domain_ids())
    except Exception:  # noqa: BLE001 - help text should not break CLI import
        domains = "registered domains"
    return (
        "Domain pack to generate data for. Counts map onto the domain's "
        f"entities; available generator domains: {domains}."
    )


@click.command("generate-data")
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
@click.option(
    "--format",
    "fmt",
    default=DEFAULT_DATA_FORMAT,
    show_default=True,
    type=click.Choice(["csv", "avro", "parquet"]),
    help="Physical format for generated tabular datasets.",
)
@click.option(
    "--domain",
    default="agricultural",
    show_default=True,
    help=_domain_help(),
)
def generate_data(
    vehicles: int,
    implements: int,
    orders: int,
    depots: int,
    seed: int | None,
    data_path: str | None,
    fmt: str,
    domain: str,
) -> None:
    """Generate a synthetic (or augmented real) dataset for a domain pack."""
    from fl_op.data.domain_generators import GenerationRequest, run_domain_generator

    try:
        run_domain_generator(
            domain,
            GenerationRequest(
                vehicles=vehicles,
                implements=implements,
                orders=orders,
                depots=depots,
                seed=seed,
                data_path=data_path,
                fmt=fmt,
            ),
        )
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise click.ClickException(str(exc)) from exc


def register_data_commands(cli: click.Group) -> None:
    cli.add_command(generate_data)

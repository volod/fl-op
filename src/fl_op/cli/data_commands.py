"""Dataset generation CLI commands."""

import os

import click

from fl_op.core.constants import (
    DEFAULT_DATA_FORMAT,
    DEFAULT_GENERATE_DEPOTS,
    DEFAULT_GENERATE_IMPLEMENTS,
    DEFAULT_GENERATE_ORDERS,
    DEFAULT_GENERATE_VEHICLES,
)

_SEED_ENV: int | None = int(os.environ["SEED"]) if os.environ.get("SEED") else None


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
    type=click.Choice(["agricultural", "construction"]),
    help=(
        "Domain pack to generate data for. Counts map onto the domain's "
        "entities (construction: vehicles=machines, implements=attachments, "
        "orders=jobs, depots=yards)."
    ),
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
    if domain == "construction":
        from fl_op.data.generator import run_generate_construction

        run_generate_construction(
            n_machines=vehicles,
            n_attachments=implements,
            n_jobs=orders,
            n_yards=depots,
            seed=seed,
            fmt=fmt,
        )
        return

    from fl_op.data.generator import run_generate

    run_generate(
        n_vehicles=vehicles,
        n_implements=implements,
        n_orders=orders,
        n_depots=depots,
        seed=seed,
        data_path=data_path,
        fmt=fmt,
    )


def register_data_commands(cli: click.Group) -> None:
    cli.add_command(generate_data)

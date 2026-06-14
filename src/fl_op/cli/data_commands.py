"""Dataset generation CLI commands."""

import json
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


def _default_domain() -> str:
    try:
        return FileRegistry().active_domain or "agricultural"
    except Exception:  # noqa: BLE001 - CLI defaults should not break import
        return "agricultural"


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
    default=None,
    show_default="registry active domain",
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
    domain: str | None,
) -> None:
    """Generate a synthetic (or augmented real) dataset for a domain pack."""
    from fl_op.data.domain_generators import GenerationRequest, run_domain_generator

    selected_domain = domain or _default_domain()
    try:
        run_domain_generator(
            selected_domain,
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


@click.command("domain-capabilities")
@click.option(
    "--domain",
    default=None,
    show_default="all generator domains",
    help="Report capabilities for one domain; omit to list every generator domain.",
)
def domain_capabilities(domain: str | None) -> None:
    """Print generator capability metadata as JSON.

    Capabilities describe what a domain pack produces: canonical entities, the
    contracts staged into a dataset, their source formats, and any extras the
    domain declares. They let downstream tooling discover a domain's outputs
    without inspecting the registry or running the generator.
    """
    from fl_op.data.domain_generators import (
        all_generator_capabilities,
        domain_generator_capabilities,
    )

    try:
        if domain:
            payload: object = domain_generator_capabilities(domain)
        else:
            payload = all_generator_capabilities()
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2, sort_keys=True))


def register_data_commands(cli: click.Group) -> None:
    cli.add_command(generate_data)
    cli.add_command(domain_capabilities)

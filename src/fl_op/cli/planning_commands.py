"""Declarative contract, snapshot, plan, and demo CLI commands."""

import click

from fl_op.cli.options import data_option, resolve_data_dir


@click.group("contracts")
def contracts_group() -> None:
    """Declarative data-contract operations (ODCS single source of truth)."""


@contracts_group.command("validate")
@click.option(
    "--write",
    is_flag=True,
    default=False,
    help="Persist recomputed fingerprints to the registry.",
)
def contracts_validate(write: bool) -> None:
    """Validate all contracts: structural fingerprint, ODCS metadata hash, generation readiness."""
    from fl_op.planning.runner import run_contracts_validate

    ok = run_contracts_validate(persist=write)
    if not ok:
        raise SystemExit(1)


@contracts_group.command("canonical-validate")
def contracts_canonical_validate() -> None:
    """Validate only the canonical optimization-model contracts and vocabulary."""
    from fl_op.planning.runner import run_canonical_validate

    ok = run_canonical_validate()
    if not ok:
        raise SystemExit(1)


@contracts_group.command("validate-domain")
@click.option("--domain", required=True, help="Domain pack id (e.g. construction).")
def contracts_validate_domain(domain: str) -> None:
    """Validate that a domain pack's mappings cover the canonical model completely."""
    from fl_op.planning.runner import run_domain_validate

    ok = run_domain_validate(domain)
    if not ok:
        raise SystemExit(1)


@contracts_group.command("generate")
@click.option(
    "--format",
    "fmt",
    required=True,
    type=click.Choice(["avro", "proto", "es", "parquet"]),
    help="Target format to generate.",
)
@click.option(
    "--out-dir",
    default=None,
    type=click.Path(file_okay=False, resolve_path=True),
    help="Output directory (default: contracts/generated/<format>).",
)
@click.option(
    "--contract",
    default=None,
    help="Generate only this contract id (default: all).",
)
def contracts_generate(fmt: str, out_dir: str | None, contract: str | None) -> None:
    """Generate physical schemas (Avro / Proto / ES) from ODCS contracts."""
    import pathlib

    from fl_op.contracts.schema_gen import run_generate

    out = pathlib.Path(out_dir) if out_dir else None
    ok = run_generate(fmt=fmt, out_dir=out, contract_id=contract)
    if not ok:
        raise SystemExit(1)


@contracts_group.command("check-generation")
@click.option(
    "--format",
    "fmt",
    required=True,
    type=click.Choice(["avro", "proto", "es", "parquet"]),
    help="Target format to check.",
)
@click.option(
    "--contract",
    default=None,
    help="Check only this contract id (default: all).",
)
def contracts_check_generation(fmt: str, contract: str | None) -> None:
    """Check that ODCS contracts have complete hints for the given target format."""
    import logging

    from fl_op.contracts.schema_gen import run_check_generation

    logger = logging.getLogger(__name__)
    ok, reports = run_check_generation(fmt=fmt, contract_id=contract)
    for report in reports:
        status = "ok" if report.ok else "FAIL"
        logger.info("[%s] %s [%s]", status, report.contract_id, fmt)
        for err in report.errors:
            logger.error("  %s", err)
    if not ok:
        raise SystemExit(1)


@click.group("snapshot")
def snapshot_group() -> None:
    """Immutable planning-snapshot operations."""


@snapshot_group.command("build")
@data_option
@click.option(
    "--mode",
    type=click.Choice(["periodic", "rolling"]),
    default="periodic",
    show_default=True,
)
@click.option(
    "--effective-at",
    default=None,
    help="ISO-8601 effective timestamp (default: now).",
)
def snapshot_build(data: str, mode: str, effective_at: str | None) -> None:
    """Map source data into canonical objects and build a reproducible snapshot."""
    from fl_op.planning.runner import run_snapshot_build

    run_snapshot_build(
        str(resolve_data_dir(data)),
        mode=mode,
        effective_at=effective_at,
    )


@click.group("plan")
def plan_group() -> None:
    """Run optimization adapters and publish canonical plans."""


@plan_group.command("periodic")
@data_option
def plan_periodic(data: str) -> None:
    """Periodic (batch) OR-Tools plan from an immutable snapshot."""
    from fl_op.planning.runner import run_plan_periodic

    run_plan_periodic(str(resolve_data_dir(data)))


@plan_group.command("rolling")
@data_option
@click.option(
    "--events",
    required=False,
    default=None,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Path to events.jsonl driving rolling replanning.",
)
@click.option(
    "--effective-at",
    default=None,
    help="ISO-8601 effective timestamp (default: now).",
)
def plan_rolling(data: str, events: str | None, effective_at: str | None) -> None:
    """Rolling (stream) OR-Tools dispatch producing immutable plan revisions."""
    from fl_op.planning.runner import run_plan_rolling

    run_plan_rolling(
        str(resolve_data_dir(data)),
        events_path=events,
        effective_at=effective_at,
    )


@click.command("demo")
@data_option
def demo(data: str) -> None:
    """Run the full contract -> snapshot -> batch + stream demonstration."""
    from fl_op.planning.runner import run_demo

    run_demo(str(resolve_data_dir(data)))


def register_planning_commands(cli: click.Group) -> None:
    for command in (contracts_group, snapshot_group, plan_group, demo):
        cli.add_command(command)

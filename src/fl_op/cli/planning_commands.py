"""Declarative contract, snapshot, plan, and demo CLI commands."""

import click

from fl_op.cli.options import data_option, resolve_data_dir


@click.group("contracts")
def contracts_group() -> None:
    """Declarative data-contract operations (Avro + ODCS + x-optimization)."""


@contracts_group.command("validate")
@click.option(
    "--write",
    is_flag=True,
    default=False,
    help="Persist recomputed fingerprints to the registry.",
)
def contracts_validate(write: bool) -> None:
    """Validate all contracts: round-trip, dual fingerprints, binding agreement."""
    from fl_op.planning.runner import run_contracts_validate

    ok = run_contracts_validate(persist=write)
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

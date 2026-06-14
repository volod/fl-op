"""Declarative contract, snapshot, plan, and demo CLI commands."""

import click

from fl_op.cli.options import data_option, resolve_data_dir
from fl_op.core.constants import PLAN_WATCH_MAX_CYCLES, PLAN_WATCH_POLL_INTERVAL_S

_OBJECTIVE_OPTION = click.option(
    "--objective",
    type=click.Choice(["cost", "time"]),
    default="cost",
    show_default=True,
    help=(
        "Optimization objective: cost keeps margin/energy-cost behavior; "
        "time minimizes travel/service/completion time."
    ),
)


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
    from fl_op.planning.contracts import run_contracts_validate

    ok = run_contracts_validate(persist=write)
    if not ok:
        raise SystemExit(1)


@contracts_group.command("canonical-validate")
def contracts_canonical_validate() -> None:
    """Validate only the canonical optimization-model contracts and vocabulary."""
    from fl_op.planning.contracts import run_canonical_validate

    ok = run_canonical_validate()
    if not ok:
        raise SystemExit(1)


@contracts_group.command("validate-domain")
@click.option("--domain", required=True, help="Domain pack id (e.g. construction).")
def contracts_validate_domain(domain: str) -> None:
    """Validate that a domain pack's mappings cover the canonical model completely."""
    from fl_op.planning.contracts import run_domain_validate

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


@contracts_group.command("evolution-check")
def contracts_evolution_check() -> None:
    """Check ODCS contracts against committed schema baselines (bump policy)."""
    from fl_op.planning.contracts import run_evolution_check

    ok = run_evolution_check()
    if not ok:
        raise SystemExit(1)


@contracts_group.command("evolution-freeze")
def contracts_evolution_freeze() -> None:
    """Record reviewed schema baselines for all ODCS contracts."""
    from fl_op.planning.contracts import run_evolution_freeze

    ok = run_evolution_freeze()
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
    from fl_op.planning.snapshots import run_snapshot_build

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
@_OBJECTIVE_OPTION
def plan_periodic(data: str, objective: str) -> None:
    """Periodic (batch) OR-Tools plan from an immutable snapshot."""
    from fl_op.planning.plans import run_plan_periodic

    run_plan_periodic(str(resolve_data_dir(data)), objective=objective)


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
@_OBJECTIVE_OPTION
def plan_rolling(
    data: str,
    events: str | None,
    effective_at: str | None,
    objective: str,
) -> None:
    """Rolling (stream) OR-Tools dispatch producing immutable plan revisions."""
    from fl_op.planning.plans import run_plan_rolling

    run_plan_rolling(
        str(resolve_data_dir(data)),
        events_path=events,
        effective_at=effective_at,
        objective=objective,
    )


@plan_group.command("watch")
@data_option
@click.option(
    "--events",
    required=False,
    default=None,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Events file polled each cycle (jsonl source); ignored for broker sources.",
)
@click.option(
    "--effective-at",
    default=None,
    help="ISO-8601 effective timestamp (default: now).",
)
@click.option(
    "--poll-interval",
    type=float,
    default=PLAN_WATCH_POLL_INTERVAL_S,
    show_default=True,
    help="Seconds to idle between drain cycles when no events are visible.",
)
@click.option(
    "--max-cycles",
    type=int,
    default=PLAN_WATCH_MAX_CYCLES,
    show_default=True,
    help="Drain cycles before stopping; 0 runs forever (daemon).",
)
@_OBJECTIVE_OPTION
def plan_watch(
    data: str,
    events: str | None,
    effective_at: str | None,
    poll_interval: float,
    max_cycles: int,
    objective: str,
) -> None:
    """Continuous watcher: one rolling session draining bounded batches forever."""
    from fl_op.planning.plans import run_plan_watch

    run_plan_watch(
        str(resolve_data_dir(data)),
        events_path=events,
        effective_at=effective_at,
        objective=objective,
        poll_interval_s=poll_interval,
        max_cycles=max_cycles,
    )


@plan_group.command("freshness")
@data_option
@click.option(
    "--plan",
    default="latest",
    show_default=True,
    help="Published plan run dir (plan-periodic or plan-rolling), or 'latest'.",
)
@click.option(
    "--replan",
    is_flag=True,
    default=False,
    help="Trigger a rolling replan automatically when the plan is stale.",
)
@click.option(
    "--events",
    required=False,
    default=None,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Events file for the triggered replan.",
)
def plan_freshness(data: str, plan: str, replan: bool, events: str | None) -> None:
    """Compare a plan's visibility horizon against the data visible now."""
    from fl_op.planning.plans import run_plan_freshness

    run_plan_freshness(
        str(resolve_data_dir(data)), plan_dir=plan, replan=replan, events_path=events
    )


@plan_group.command("diff-revisions")
@click.option(
    "--plan",
    default="latest",
    show_default=True,
    help="Rolling-plan run directory under .data/plan-rolling, or 'latest'.",
)
def plan_diff_revisions(plan: str) -> None:
    """Explain why every changed assignment moved between rolling revisions."""
    from fl_op.planning.revision_diff import run_revision_diff

    run_revision_diff(plan)


@click.command("demo")
@data_option
@_OBJECTIVE_OPTION
def demo(data: str, objective: str) -> None:
    """Run the full contract -> snapshot -> batch + stream demonstration."""
    from fl_op.planning.demo import run_demo

    run_demo(str(resolve_data_dir(data)), objective=objective)


def register_planning_commands(cli: click.Group) -> None:
    for command in (contracts_group, snapshot_group, plan_group, demo):
        cli.add_command(command)

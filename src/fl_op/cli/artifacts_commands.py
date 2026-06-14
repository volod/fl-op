"""Artifact-registry and provenance CLI commands."""

import click


@click.group("artifacts")
def artifacts_group() -> None:
    """Inspect artifact provenance: caches, run manifests, and tuned overlays."""


@artifacts_group.command("registry")
@click.option(
    "--write",
    is_flag=True,
    default=False,
    help="Persist the aggregated index to DATA_DIR/registry/artifact-registry.json.",
)
def artifacts_registry(write: bool) -> None:
    """Scan the data root and report cache, manifest, and tuned-overlay provenance."""
    from fl_op.provenance.registry import run_artifacts_registry

    run_artifacts_registry(write=write)


@artifacts_group.command("verify")
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Run directory containing a manifest.json to verify.",
)
def artifacts_verify(run_dir: str) -> None:
    """Re-hash a run's on-disk files and check them against its manifest."""
    import pathlib

    from fl_op.provenance.registry import run_artifacts_verify

    ok = run_artifacts_verify(pathlib.Path(run_dir))
    if not ok:
        raise SystemExit(1)


def register_artifacts_commands(cli: click.Group) -> None:
    cli.add_command(artifacts_group)

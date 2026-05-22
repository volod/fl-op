"""Shared CLI path helpers."""

import os
import pathlib

import click

DATA_ROOT = pathlib.Path(os.environ.get("DATA_DIR", ".data"))


def resolve_latest(
    path_or_latest: str,
    base_subdir: str,
) -> pathlib.Path:
    """Resolve a directory path, expanding the sentinel 'latest' token."""
    if path_or_latest.lower() == "latest":
        return _latest_run_dir(base_subdir)

    resolved = pathlib.Path(path_or_latest).resolve()
    _guard_path_traversal(resolved)
    return resolved


def _latest_run_dir(base_subdir: str) -> pathlib.Path:
    base = DATA_ROOT / base_subdir
    if not base.exists():
        raise click.BadParameter(
            f"No runs found under {base}. Run the command first.",
            param_hint="'latest'",
        )

    candidates = sorted(
        (d for d in base.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
    )
    if not candidates:
        raise click.BadParameter(
            f"No timestamp directories found under {base}.",
            param_hint="'latest'",
        )
    return candidates[-1]


def _guard_path_traversal(resolved: pathlib.Path) -> None:
    """Raise click.BadParameter if path escapes the project root."""
    project_root = pathlib.Path(".").resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError:
        raise click.BadParameter(
            f"Path {resolved} is outside the project directory. "
            "Path traversal is not allowed.",
            param_hint="path",
        )


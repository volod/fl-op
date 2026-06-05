"""CLI bootstrap helpers."""

import logging
import os
import pathlib

import click

INTERRUPTED_EXIT_CODE = 130


def load_dotenv() -> None:
    """Load .env into os.environ without overriding existing variables."""
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


def log_level_from_env() -> int:
    """Return the configured logging level from LOG_LEVEL."""
    return getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)


def run_cli(command: click.Command, args: list[str] | None = None) -> None:
    """Run a Click command with consistent interrupt handling."""
    try:
        command.main(args=args, standalone_mode=False)
    except (click.Abort, KeyboardInterrupt):
        click.echo(
            "Interrupted: pipeline stopped before completing the current command.",
            err=True,
        )
        raise SystemExit(INTERRUPTED_EXIT_CODE) from None
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from None

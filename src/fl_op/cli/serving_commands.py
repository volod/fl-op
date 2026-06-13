"""Serving CLI command: run the thin service API."""

import click

from fl_op.core.constants import SERVE_HOST, SERVE_PORT


@click.command("serve")
@click.option(
    "--host",
    default=SERVE_HOST,
    show_default=True,
    help="Bind address (loopback by default; 0.0.0.0 exposes the API).",
)
@click.option(
    "--port",
    default=SERVE_PORT,
    show_default=True,
    type=int,
    help="Bind port.",
)
def serve(host: str, port: int) -> None:
    """Serve feasibility checks and published plan retrieval over HTTP."""
    from fl_op.serving.api import run_serve

    run_serve(host=host, port=port)


def register_serving_commands(cli: click.Group) -> None:
    cli.add_command(serve)

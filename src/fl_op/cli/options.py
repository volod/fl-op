"""Shared Click options and path resolution for CLI commands."""

from typing import Any, Callable, TypeVar

import click

from fl_op.core.paths import resolve_latest

F = TypeVar("F", bound=Callable[..., Any])


def data_option(func: F) -> F:
    return click.option(
        "--data",
        required=True,
        type=str,
        help="Path to dataset directory, or 'latest' for the most recent generate-data run.",
    )(func)


def schedule_option(func: F) -> F:
    return click.option(
        "--schedule",
        required=True,
        type=str,
        help="Path to solve output directory, or 'latest' for the most recent solve run.",
    )(func)


def resolve_data_dir(data: str):
    return resolve_latest(data, "generate-data")


def resolve_schedule_dir(schedule: str):
    return resolve_latest(schedule, "solve")

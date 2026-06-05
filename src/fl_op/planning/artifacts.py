"""Shared artifact helpers for planning CLI commands."""

import json
import pathlib
from datetime import datetime, timezone
from typing import Any


def run_timestamp() -> str:
    """Return the repository-standard UTC timestamp for artifact directories."""
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")


def write_json(obj: Any, path: pathlib.Path) -> None:
    """Write an indented JSON artifact, creating the parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        json.dump(obj, fh, indent=2, default=str)


def model_json(model: Any) -> dict[str, Any]:
    """Convert a Pydantic model to the canonical artifact JSON shape."""
    return model.model_dump(mode="json", by_alias=True)

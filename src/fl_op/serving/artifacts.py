"""Read-only artifact storage for the serving API.

The default implementation is a filesystem root. It can be pointed at a shared
mount with SERVE_ARTIFACT_ROOT so several service instances expose the same
published plans and feasibility inputs while keeping the route contract stable.
"""

import json
import pathlib
from typing import Any, Protocol

from fl_op.core import constants, paths


class ArtifactStore(Protocol):
    """Read-only artifact access needed by serving/api.py."""

    root: pathlib.Path

    def list_run_ids(self, subdir: str) -> list[str]:
        ...

    def read_json(self, relative_path: str | pathlib.Path) -> dict[str, Any]:
        ...

    def exists(self, relative_path: str | pathlib.Path) -> bool:
        ...

    def local_path(self, relative_path: str | pathlib.Path) -> pathlib.Path:
        ...


class FilesystemArtifactStore:
    """ArtifactStore backed by a local or shared filesystem root."""

    def __init__(self, root: pathlib.Path | str | None = None) -> None:
        configured = root or constants.SERVE_ARTIFACT_ROOT or paths.DATA_ROOT
        self.root = pathlib.Path(configured).resolve()

    def list_run_ids(self, subdir: str) -> list[str]:
        base = self.local_path(subdir)
        if not base.is_dir():
            return []
        return sorted(d.name for d in base.iterdir() if d.is_dir())

    def read_json(self, relative_path: str | pathlib.Path) -> dict[str, Any]:
        path = self.local_path(relative_path)
        return json.loads(path.read_text())

    def exists(self, relative_path: str | pathlib.Path) -> bool:
        return self.local_path(relative_path).exists()

    def local_path(self, relative_path: str | pathlib.Path) -> pathlib.Path:
        path = (self.root / pathlib.PurePath(relative_path)).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Artifact path escapes root: {relative_path}") from exc
        return path


def default_artifact_store() -> FilesystemArtifactStore:
    return FilesystemArtifactStore()

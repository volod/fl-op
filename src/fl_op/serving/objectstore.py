"""Object-store artifact backend with cross-writer consistency.

The filesystem store lists run directories straight off the directory tree, so a
reader can observe a run another writer is still writing. Object stores have no
atomic directory rename to lean on, so this backend makes publication explicit:
a run becomes visible only once a **commit marker** object
(``OBJECT_STORE_COMMIT_MARKER``) appears under its prefix, and a publisher writes
that marker *last* (:func:`publish_run`). Listing and reading skip any run prefix
without the marker, so a half-published run is never served -- cross-writer
read-after-write consistency for newly published runs without locks.

The store reads through an injectable :class:`ObjectStoreClient`. One client
ships: :class:`LocalObjectStoreClient` (a filesystem-backed reference, no extra
dependency, used in tests and single-host setups). The protocol is the seam for
a future networked backend -- it is added as its own client implementation, so
no vendor SDK is bundled here. Routes that need local file inputs (feasibility)
call :meth:`local_path`, which materializes a committed run's objects into a
local cache and returns the directory; immutable committed runs are materialized
once and reused.
"""

import json
import logging
import pathlib
from typing import Any, Optional, Protocol

from fl_op.core import constants
from fl_op.core.paths import DATA_ROOT

logger = logging.getLogger(__name__)


class ObjectStoreClient(Protocol):
    """Minimal key/bytes object-store surface the artifact store needs."""

    def get_bytes(self, key: str) -> bytes:
        ...

    def put_bytes(self, key: str, data: bytes) -> None:
        ...

    def exists(self, key: str) -> bool:
        ...

    def list_keys(self, prefix: str) -> list[str]:
        ...


class LocalObjectStoreClient:
    """Filesystem-backed reference object store: each key is a file under root.

    Not a cache of another store -- it *is* the store, addressed by flat keys
    rather than nested directory handles, so it exercises the same commit-marker
    semantics an S3 backend does without any network dependency.
    """

    def __init__(self, root: pathlib.Path | str) -> None:
        self.root = pathlib.Path(root).resolve()

    def _path(self, key: str) -> pathlib.Path:
        path = (self.root / key).resolve()
        path.relative_to(self.root)  # raises ValueError on escape
        return path

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def put_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str) -> list[str]:
        keys = [
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file()
        ]
        return sorted(key for key in keys if key.startswith(prefix))


def _normalize_prefix(prefix: str) -> str:
    return prefix.strip("/")


def _safe_rel(relative_path: str | pathlib.Path) -> pathlib.PurePath:
    rel = pathlib.PurePath(relative_path)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError(f"unsafe artifact path: {relative_path}")
    return rel


class ObjectStoreArtifactStore:
    """Read-only artifact store over an object store, commit-marker gated."""

    def __init__(
        self,
        client: ObjectStoreClient,
        prefix: str = "",
        materialize_root: Optional[pathlib.Path] = None,
        commit_marker: str = constants.OBJECT_STORE_COMMIT_MARKER,
    ) -> None:
        self.client = client
        self._prefix = _normalize_prefix(prefix)
        self.commit_marker = commit_marker
        self.root = (
            materialize_root
            if materialize_root is not None
            else DATA_ROOT / constants.OBJECT_STORE_MATERIALIZE_DIRNAME
        ).resolve()

    def _key(self, relative_path: str | pathlib.Path) -> str:
        rel = pathlib.PurePath(relative_path).as_posix()
        return f"{self._prefix}/{rel}" if self._prefix else rel

    def _strip_prefix(self, key: str) -> str:
        if not self._prefix:
            return key
        head = f"{self._prefix}/"
        return key[len(head):] if key.startswith(head) else key

    def list_run_ids(self, subdir: str) -> list[str]:
        """Run ids directly under ``subdir`` that carry a commit marker."""
        base = self._key(subdir)
        committed: set[str] = set()
        for key in self.client.list_keys(base + "/"):
            rel = key[len(base) + 1:]
            parts = rel.split("/")
            if len(parts) == 2 and parts[1] == self.commit_marker:
                committed.add(parts[0])
        return sorted(committed)

    def read_json(self, relative_path: str | pathlib.Path) -> dict[str, Any]:
        return json.loads(self.client.get_bytes(self._key(relative_path)))

    def exists(self, relative_path: str | pathlib.Path) -> bool:
        return self.client.exists(self._key(relative_path))

    def local_path(self, relative_path: str | pathlib.Path) -> pathlib.Path:
        """Materialize a committed run prefix locally and return its directory.

        Feasibility passes the returned path to the query pipeline, which reads
        source files and ``schedule.json`` off local disk. Immutable committed
        runs are materialized once into ``root/<relative_path>`` and reused.
        """
        rel = _safe_rel(relative_path)
        target = (self.root / rel).resolve()
        target.relative_to(self.root)  # defense in depth against escape
        self._materialize(rel, target)
        return target

    def _materialize(self, rel: pathlib.PurePath, target: pathlib.Path) -> None:
        base = self._key(rel)
        for key in self.client.list_keys(base + "/"):
            local = self.root / self._strip_prefix(key)
            if local.exists():
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(self.client.get_bytes(key))
        if not target.exists():
            logger.debug("Materialized no objects under %s", base)


def publish_run(
    client: ObjectStoreClient,
    subdir: str,
    run_id: str,
    files: dict[str, bytes],
    prefix: str = "",
    commit_marker: str = constants.OBJECT_STORE_COMMIT_MARKER,
) -> None:
    """Publish a run's objects, then its commit marker last.

    Writing the marker last is the cross-writer consistency contract: a reader
    that sees the marker is guaranteed to also see every file written before it,
    and a reader racing the publish either sees no marker (and skips the run) or
    a fully-written run.
    """
    base = f"{_normalize_prefix(prefix)}/{subdir}/{run_id}".lstrip("/")
    for rel_path, data in files.items():
        client.put_bytes(f"{base}/{_safe_rel(rel_path).as_posix()}", data)
    client.put_bytes(f"{base}/{commit_marker}", b"")


def build_object_store_from_constants() -> ObjectStoreArtifactStore:
    """Construct the configured object-store artifact backend."""
    kind = (constants.SERVE_OBJECT_STORE_KIND or "local").strip().lower()
    if kind != "local":
        raise ValueError(
            f"unknown SERVE_OBJECT_STORE_KIND '{kind}'; only 'local' is built in "
            "(add a networked backend through its own ObjectStoreClient)"
        )
    root = constants.SERVE_OBJECT_STORE_LOCAL_ROOT
    if not root:
        raise ValueError(
            "SERVE_OBJECT_STORE_KIND=local requires SERVE_OBJECT_STORE_LOCAL_ROOT"
        )
    client: ObjectStoreClient = LocalObjectStoreClient(root)
    return ObjectStoreArtifactStore(
        client, prefix=constants.SERVE_OBJECT_STORE_PREFIX
    )

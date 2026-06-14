"""Artifact manifests: per-run provenance sidecars.

A manifest is a small ``manifest.json`` written next to a run's primary
artifacts. It records what produced the run (the artifact kind, the schema
version), when it was produced, which snapshot hashes it derives from, optional
scope metadata for tuned overlays, and a content digest of every file in the
run directory. The digest lets a later reader confirm the artifacts on disk are
the exact bytes the manifest describes.

Manifests are additive: existing artifact writers keep emitting their primary
JSON, and ``write_manifest`` drops a sidecar beside them.
"""

import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.core.constants import MANIFEST_SCHEMA_VERSION
from fl_op.provenance.namespace import (
    NAMESPACE_VERSION,
    content_hash,
)

MANIFEST_KIND = "ArtifactManifest"

# Files that describe a run rather than belong to it; excluded from the digest
# so writing the manifest never invalidates the manifest.
_EXCLUDED_FILENAMES = frozenset({"manifest.json"})


def _file_sha256(path: pathlib.Path) -> str:
    import hashlib

    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _scan_files(run_dir: pathlib.Path) -> list[dict[str, Any]]:
    """Digest every artifact file in ``run_dir`` (recursively), sorted by path."""
    entries: list[dict[str, Any]] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name in _EXCLUDED_FILENAMES:
            continue
        rel = path.relative_to(run_dir).as_posix()
        stat = path.stat()
        entries.append(
            {
                "path": rel,
                "size_bytes": stat.st_size,
                "sha256": _file_sha256(path),
            }
        )
    return entries


def build_manifest(
    run_dir: pathlib.Path,
    *,
    artifact_kind: str,
    schema_version: str,
    snapshot_hashes: Optional[list[str]] = None,
    scope: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble the manifest document for a finished run directory.

    ``manifest_hash`` is a content hash over the descriptive fields and the file
    digests, so two runs that produced byte-identical artifacts from the same
    inputs share a manifest hash.
    """
    files = _scan_files(run_dir)
    body: dict[str, Any] = {
        "kind": MANIFEST_KIND,
        "manifestSchemaVersion": MANIFEST_SCHEMA_VERSION,
        "namespaceVersion": NAMESPACE_VERSION,
        "artifactKind": artifact_kind,
        "artifactSchemaVersion": schema_version,
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "snapshotHashes": sorted(snapshot_hashes or []),
        "scope": scope or {},
        "files": files,
    }
    if extra:
        body["extra"] = extra
    # The hash covers everything except the volatile generation timestamp and
    # the hash field itself, so identical artifacts yield an identical hash.
    hashable = {k: v for k, v in body.items() if k != "generatedAt"}
    body["manifestHash"] = content_hash("artifact-manifest", hashable)
    return body


def write_manifest(
    run_dir: pathlib.Path,
    *,
    artifact_kind: str,
    schema_version: str,
    snapshot_hashes: Optional[list[str]] = None,
    scope: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> pathlib.Path:
    """Build and write ``manifest.json`` into ``run_dir``; return its path."""
    # Imported lazily: ``fl_op.planning`` imports this module, so a top-level
    # import here would create a package-initialization cycle.
    from fl_op.planning.artifacts import write_json

    manifest = build_manifest(
        run_dir,
        artifact_kind=artifact_kind,
        schema_version=schema_version,
        snapshot_hashes=snapshot_hashes,
        scope=scope,
        extra=extra,
    )
    target = run_dir / "manifest.json"
    write_json(manifest, target)
    return target


def load_manifest(run_dir: pathlib.Path) -> Optional[dict[str, Any]]:
    """Read a run's manifest, or None when absent/unreadable."""
    import json
    import logging

    target = run_dir / "manifest.json"
    if not target.exists():
        return None
    try:
        doc = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logging.getLogger(__name__).warning(
            "Ignoring unreadable manifest %s: %s", target, exc
        )
        return None
    if doc.get("kind") != MANIFEST_KIND:
        return None
    return doc


def verify_manifest(run_dir: pathlib.Path) -> list[str]:
    """Re-hash on-disk files and report mismatches against the manifest.

    Returns a list of human-readable problems; an empty list means every file
    recorded in the manifest is present with the expected digest.
    """
    doc = load_manifest(run_dir)
    if doc is None:
        return [f"no manifest found in {run_dir}"]

    problems: list[str] = []
    recorded = {entry["path"]: entry for entry in doc.get("files", [])}
    for rel, entry in recorded.items():
        path = run_dir / rel
        if not path.is_file():
            problems.append(f"missing file: {rel}")
            continue
        actual = _file_sha256(path)
        if actual != entry.get("sha256"):
            problems.append(f"digest mismatch: {rel}")

    on_disk = {
        item["path"] for item in _scan_files(run_dir)
    }
    for rel in sorted(on_disk - set(recorded)):
        problems.append(f"untracked file: {rel}")
    return problems


__all__ = [
    "MANIFEST_KIND",
    "build_manifest",
    "write_manifest",
    "load_manifest",
    "verify_manifest",
]

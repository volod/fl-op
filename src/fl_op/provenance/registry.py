"""Artifact registry: aggregate provenance across the data root.

The registry is a read-only scanner. It walks ``DATA_DIR`` and assembles three
provenance views without changing how any artifact is written:

* cache provenance -- per-namespace size, file counts, and last-modified time
  for the content-addressed caches (compat matrix, preprocessing, feasibility),
  inferred from the directory tree;
* artifact manifests -- every ``manifest.json`` sidecar dropped by a run, with
  its declared snapshot hashes and scope;
* tuned overlays -- the reviewed solver-parameter and monitoring-policy
  overlays, surfacing their selection metadata (scope, source snapshot hashes,
  expiry) so an operator can see which overlay a scoped run would select.

The aggregated index is what the ``artifacts`` CLI group renders and writes.
"""

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.core.constants import (
    ARTIFACT_MANIFEST_FILENAME,
    ARTIFACT_REGISTRY_DIRNAME,
    ARTIFACT_REGISTRY_FILENAME,
    CACHE_PROVENANCE_DIRNAMES,
    MANIFEST_SCHEMA_VERSION,
    TUNED_SOLVER_PARAMETERS_FILENAME,
)
from fl_op.core.paths import DATA_ROOT
from fl_op.planning.artifacts import write_json
from fl_op.provenance.manifest import load_manifest, verify_manifest
from fl_op.provenance.namespace import NAMESPACE_VERSION

logger = logging.getLogger(__name__)

_REVIEWED_SOLVER_KIND = "ReviewedTunedSolverProfile"


def _isoformat(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def scan_cache_provenance(root: pathlib.Path) -> list[dict[str, Any]]:
    """Summarize each content-addressed cache namespace under ``root``."""
    summaries: list[dict[str, Any]] = []
    for dirname in CACHE_PROVENANCE_DIRNAMES:
        cache_dir = root / dirname
        if not cache_dir.exists():
            summaries.append(
                {
                    "namespace": dirname,
                    "present": False,
                    "entry_count": 0,
                    "total_bytes": 0,
                    "last_modified": None,
                }
            )
            continue
        total_bytes = 0
        entry_count = 0
        latest_mtime = 0.0
        for path in cache_dir.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
            entry_count += 1
            total_bytes += stat.st_size
            latest_mtime = max(latest_mtime, stat.st_mtime)
        summaries.append(
            {
                "namespace": dirname,
                "present": True,
                "entry_count": entry_count,
                "total_bytes": total_bytes,
                "last_modified": _isoformat(latest_mtime) if latest_mtime else None,
            }
        )
    return summaries


def scan_manifests(root: pathlib.Path) -> list[dict[str, Any]]:
    """Find every artifact manifest sidecar under ``root``."""
    manifests: list[dict[str, Any]] = []
    for manifest_path in sorted(root.rglob(ARTIFACT_MANIFEST_FILENAME)):
        run_dir = manifest_path.parent
        doc = load_manifest(run_dir)
        if doc is None:
            continue
        manifests.append(
            {
                "path": run_dir.relative_to(root).as_posix(),
                "artifactKind": doc.get("artifactKind"),
                "manifestHash": doc.get("manifestHash"),
                "snapshotHashes": doc.get("snapshotHashes", []),
                "scope": doc.get("scope", {}),
                "generatedAt": doc.get("generatedAt"),
                "fileCount": len(doc.get("files", [])),
            }
        )
    return manifests


def _scan_solver_overlays(root: pathlib.Path) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = []
    tune_dir = root / "tune"
    if not tune_dir.exists():
        return overlays
    for path in sorted(tune_dir.rglob(TUNED_SOLVER_PARAMETERS_FILENAME)):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable tuned overlay %s: %s", path, exc)
            continue
        if doc.get("kind") != _REVIEWED_SOLVER_KIND:
            continue
        overlays.append(
            {
                "type": "solver-parameters",
                "path": path.relative_to(root).as_posix(),
                "scope": doc.get("scope", {}),
                "sourceSnapshotHashes": doc.get("source_snapshot_hashes", []),
                "reviewedAt": doc.get("reviewed_at"),
                "reviewedBy": doc.get("reviewed_by"),
            }
        )
    return overlays


def scan_tuned_overlays(root: pathlib.Path) -> list[dict[str, Any]]:
    """Surface reviewed tuned overlays and their selection metadata."""
    return _scan_solver_overlays(root)


def build_registry(root: Optional[pathlib.Path] = None) -> dict[str, Any]:
    """Assemble the aggregated artifact-registry index for ``root``."""
    base = root or DATA_ROOT
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "namespaceVersion": NAMESPACE_VERSION,
        "dataRoot": str(base),
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "cacheProvenance": scan_cache_provenance(base),
        "manifests": scan_manifests(base),
        "tunedOverlays": scan_tuned_overlays(base),
    }


def run_artifacts_registry(write: bool = False) -> dict[str, Any]:
    """Build the registry index, log a human-readable summary, optionally persist.

    Returns the index dict. When ``write`` is set, the index is also written to
    ``DATA_DIR/registry/artifact-registry.json``.
    """
    registry = build_registry()
    caches = registry["cacheProvenance"]
    manifests = registry["manifests"]
    overlays = registry["tunedOverlays"]

    logger.info("Artifact registry for %s", registry["dataRoot"])
    logger.info(
        "  namespace version %s, schema %s",
        registry["namespaceVersion"],
        registry["schemaVersion"],
    )
    logger.info("  caches:")
    for cache in caches:
        if cache["present"]:
            logger.info(
                "    %-28s %d entries, %d bytes (last %s)",
                cache["namespace"],
                cache["entry_count"],
                cache["total_bytes"],
                cache["last_modified"] or "-",
            )
        else:
            logger.info("    %-28s absent", cache["namespace"])
    logger.info("  manifests: %d run(s)", len(manifests))
    for entry in manifests:
        logger.info(
            "    %-40s %s",
            entry["path"],
            entry.get("artifactKind") or "-",
        )
    logger.info("  tuned overlays: %d", len(overlays))
    for overlay in overlays:
        logger.info(
            "    %-40s scope=%s",
            overlay["path"],
            overlay.get("scope") or {},
        )

    if write:
        target = DATA_ROOT / ARTIFACT_REGISTRY_DIRNAME / ARTIFACT_REGISTRY_FILENAME
        write_json(registry, target)
        logger.info("Wrote registry index -> %s", target)
    return registry


def run_artifacts_verify(run_dir: pathlib.Path) -> bool:
    """Verify a run's manifest against its on-disk bytes; log and return status."""
    problems = verify_manifest(run_dir)
    if not problems:
        logger.info("Manifest OK: every file in %s matches its digest", run_dir)
        return True
    logger.error("Manifest verification failed for %s:", run_dir)
    for problem in problems:
        logger.error("  %s", problem)
    return False

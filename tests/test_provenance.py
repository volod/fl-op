"""Provenance foundation: content hashing, manifests, and the registry scanner."""

import hashlib
import json
import pathlib

from fl_op.core.constants import (
    COMPAT_MATRIX_CACHE_DIRNAME,
    PROVENANCE_NAMESPACE_VERSION,
    SNAPSHOT_HASH_VERSION,
    TUNED_SOLVER_PARAMETERS_FILENAME,
)
from fl_op.provenance.manifest import (
    build_manifest,
    load_manifest,
    verify_manifest,
    write_manifest,
)
from fl_op.provenance.namespace import (
    canonical_json,
    content_hash,
)
from fl_op.snapshot.hashing import compute_snapshot_hash
from fl_op.provenance.registry import (
    build_registry,
    scan_cache_provenance,
    scan_manifests,
    scan_tuned_overlays,
)


def test_canonical_json_is_order_independent() -> None:
    """Key insertion order never changes the canonical encoding."""
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b == '{"a":2,"b":1}'


def test_content_hash_is_namespaced_and_versioned() -> None:
    """Equal payloads in different namespaces never collide; version is folded in."""
    payload = {"shared": True}
    assert content_hash("alpha", payload) != content_hash("beta", payload)

    framed = {
        "namespace": "alpha",
        "namespace_version": PROVENANCE_NAMESPACE_VERSION,
        "payload": payload,
    }
    expected = hashlib.sha256(canonical_json(framed).encode("utf-8")).hexdigest()
    assert content_hash("alpha", payload) == expected


def test_content_hash_version_overrides_global() -> None:
    """An explicit version decouples the digest from the global namespace version."""
    payload = {"shared": True}
    pinned = content_hash("alpha", payload, version="frozen")
    framed = {
        "namespace": "alpha",
        "namespace_version": "frozen",
        "payload": payload,
    }
    expected = hashlib.sha256(canonical_json(framed).encode("utf-8")).hexdigest()
    assert pinned == expected
    assert pinned != content_hash("alpha", payload)


def test_snapshot_hash_is_namespaced_and_pinned_to_snapshot_version() -> None:
    """Snapshot hashes carry the snapshot namespace and their own pinned version."""
    payload = {"x": 1, "y": [3, 2, 1]}
    expected = content_hash("snapshot", payload, version=SNAPSHOT_HASH_VERSION)
    assert compute_snapshot_hash(payload) == expected


def test_snapshot_hash_is_reproducible_and_content_sensitive() -> None:
    """Identical content yields an identical hash; any change yields a new one."""
    payload = {"x": 1, "y": [3, 2, 1]}
    assert compute_snapshot_hash(payload) == compute_snapshot_hash(dict(payload))
    assert compute_snapshot_hash(payload) != compute_snapshot_hash({"x": 2, "y": [3, 2, 1]})


def test_manifest_round_trip_and_hash_excludes_timestamp(tmp_path: pathlib.Path) -> None:
    """A manifest digests its files; identical bytes yield an identical manifestHash."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "snapshot.json").write_text('{"value": 1}')

    written = write_manifest(
        run,
        artifact_kind="PlanningSnapshot",
        schema_version="1.0",
        snapshot_hashes=["deadbeef"],
        scope={"planning_mode": "periodic"},
    )
    assert written == run / "manifest.json"

    doc = load_manifest(run)
    assert doc is not None
    assert doc["artifactKind"] == "PlanningSnapshot"
    assert doc["snapshotHashes"] == ["deadbeef"]
    assert doc["scope"] == {"planning_mode": "periodic"}
    assert [f["path"] for f in doc["files"]] == ["snapshot.json"]

    # manifestHash is independent of the volatile generatedAt timestamp.
    rebuilt = build_manifest(
        run,
        artifact_kind="PlanningSnapshot",
        schema_version="1.0",
        snapshot_hashes=["deadbeef"],
        scope={"planning_mode": "periodic"},
    )
    assert rebuilt["manifestHash"] == doc["manifestHash"]


def test_verify_manifest_detects_tamper_and_untracked(tmp_path: pathlib.Path) -> None:
    """verify_manifest catches a clean run, a mutated file, and an untracked file."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "snapshot.json").write_text('{"value": 1}')
    write_manifest(run, artifact_kind="PlanningSnapshot", schema_version="1.0")

    assert verify_manifest(run) == []

    (run / "snapshot.json").write_text('{"value": 2}')
    assert any("digest mismatch" in p for p in verify_manifest(run))

    (run / "snapshot.json").write_text('{"value": 1}')
    (run / "extra.json").write_text("{}")
    assert any("untracked file" in p for p in verify_manifest(run))


def test_verify_manifest_reports_missing_manifest(tmp_path: pathlib.Path) -> None:
    assert verify_manifest(tmp_path) == [f"no manifest found in {tmp_path}"]


def test_scan_cache_provenance_reports_presence(tmp_path: pathlib.Path) -> None:
    """Cache scan reports byte counts for present namespaces and flags absent ones."""
    cache_dir = tmp_path / COMPAT_MATRIX_CACHE_DIRNAME
    cache_dir.mkdir(parents=True)
    (cache_dir / "entry.json").write_text("0123456789")

    summaries = {c["namespace"]: c for c in scan_cache_provenance(tmp_path)}
    compat = summaries[COMPAT_MATRIX_CACHE_DIRNAME]
    assert compat["present"] is True
    assert compat["entry_count"] == 1
    assert compat["total_bytes"] == 10
    assert compat["last_modified"] is not None

    # A namespace with no directory is reported as absent rather than omitted.
    absent = [c for c in summaries.values() if not c["present"]]
    assert absent and all(c["entry_count"] == 0 for c in absent)


def test_scan_manifests_and_tuned_overlays(tmp_path: pathlib.Path) -> None:
    """The registry surfaces run manifests and reviewed tuned overlays with metadata."""
    run = tmp_path / "snapshot" / "20260101T000000"
    run.mkdir(parents=True)
    (run / "snapshot.json").write_text('{"value": 1}')
    write_manifest(
        run,
        artifact_kind="PlanningSnapshot",
        schema_version="1.0",
        snapshot_hashes=["abc123"],
        scope={"planning_mode": "rolling"},
    )

    overlay_dir = tmp_path / "tune" / "20260101T000000"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / TUNED_SOLVER_PARAMETERS_FILENAME).write_text(
        json.dumps(
            {
                "kind": "ReviewedTunedSolverProfile",
                "scope": {"domain": "agriculture"},
                "source_snapshot_hashes": ["abc123"],
                "reviewed_at": "2026-01-01T00:00:00+00:00",
                "reviewed_by": "operator",
            }
        )
    )

    manifests = scan_manifests(tmp_path)
    assert len(manifests) == 1
    assert manifests[0]["artifactKind"] == "PlanningSnapshot"
    assert manifests[0]["snapshotHashes"] == ["abc123"]
    assert manifests[0]["path"] == "snapshot/20260101T000000"

    overlays = scan_tuned_overlays(tmp_path)
    assert len(overlays) == 1
    assert overlays[0]["type"] == "solver-parameters"
    assert overlays[0]["scope"] == {"domain": "agriculture"}
    assert overlays[0]["sourceSnapshotHashes"] == ["abc123"]


def test_build_registry_aggregates_all_views(tmp_path: pathlib.Path) -> None:
    """build_registry stitches caches, manifests, and overlays into one index."""
    registry = build_registry(tmp_path)
    assert registry["namespaceVersion"] == PROVENANCE_NAMESPACE_VERSION
    assert registry["dataRoot"] == str(tmp_path)
    for key in ("cacheProvenance", "manifests", "tunedOverlays"):
        assert key in registry

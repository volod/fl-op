"""Object-store artifact backend: commit-marker consistency, materialization,
publishing, and backend dispatch."""

import json

import pytest

from fl_op.core import constants
from fl_op.serving.artifacts import default_artifact_store
from fl_op.serving.objectstore import (
    LocalObjectStoreClient,
    ObjectStoreArtifactStore,
    build_object_store_from_constants,
    publish_run,
)


@pytest.fixture
def client(tmp_path) -> LocalObjectStoreClient:
    return LocalObjectStoreClient(tmp_path / "bucket")


@pytest.fixture
def store(tmp_path, client) -> ObjectStoreArtifactStore:
    return ObjectStoreArtifactStore(client, materialize_root=tmp_path / "mat")


def test_only_committed_runs_are_listed(client, store) -> None:
    publish_run(
        client,
        "plan-periodic",
        "20260101T000000",
        {"plan.json": json.dumps({"plan_id": "committed"}).encode()},
    )
    # A half-published run: files present, no commit marker.
    client.put_bytes(
        "plan-periodic/20260102T000000/plan.json",
        json.dumps({"plan_id": "partial"}).encode(),
    )

    assert store.list_run_ids("plan-periodic") == ["20260101T000000"]


def test_read_committed_run(client, store) -> None:
    publish_run(
        client,
        "plan-periodic",
        "20260101T000000",
        {"plan.json": json.dumps({"plan_id": "p1"}).encode()},
    )
    assert store.read_json("plan-periodic/20260101T000000/plan.json") == {
        "plan_id": "p1"
    }


def test_marker_written_last_so_a_racing_reader_sees_a_whole_run(client) -> None:
    # Simulate a writer mid-publish: files written, marker not yet.
    client.put_bytes("solve/run-a/schedule.json", b"{}")
    store = ObjectStoreArtifactStore(client)
    assert store.list_run_ids("solve") == []  # invisible until committed
    client.put_bytes(f"solve/run-a/{constants.OBJECT_STORE_COMMIT_MARKER}", b"")
    assert store.list_run_ids("solve") == ["run-a"]


def test_local_path_materializes_run_subtree(client, store, tmp_path) -> None:
    publish_run(
        client,
        "generate-data",
        "20260101T000000",
        {
            "sources/sites.json": b"{}",
            "schedule.json": b"{}",
        },
    )
    local = store.local_path("generate-data/20260101T000000")
    assert local.is_dir()
    assert (local / "sources" / "sites.json").is_file()
    assert (local / "schedule.json").is_file()
    # Idempotent: a second call reuses the materialized files.
    assert store.local_path("generate-data/20260101T000000") == local


def test_prefix_scopes_keys(tmp_path) -> None:
    client = LocalObjectStoreClient(tmp_path / "bucket")
    publish_run(
        client,
        "plan-periodic",
        "r1",
        {"plan.json": b"{}"},
        prefix="tenant-a",
    )
    store = ObjectStoreArtifactStore(
        client, prefix="tenant-a", materialize_root=tmp_path / "mat"
    )
    assert store.list_run_ids("plan-periodic") == ["r1"]
    assert store.exists("plan-periodic/r1/plan.json")


def test_unsafe_relative_path_is_rejected(store) -> None:
    with pytest.raises(ValueError):
        store.local_path("../escape")


def test_local_object_store_rejects_key_escape(tmp_path) -> None:
    client = LocalObjectStoreClient(tmp_path / "bucket")
    with pytest.raises(ValueError):
        client.put_bytes("../outside.txt", b"x")


def test_default_artifact_store_dispatches_to_object_store(
    tmp_path, monkeypatch
) -> None:
    bucket = tmp_path / "bucket"
    LocalObjectStoreClient(bucket)  # ensure root exists
    publish_run(
        LocalObjectStoreClient(bucket),
        "plan-periodic",
        "r1",
        {"plan.json": b"{}"},
    )
    monkeypatch.setattr(constants, "SERVE_ARTIFACT_BACKEND", "object-store")
    monkeypatch.setattr(constants, "SERVE_OBJECT_STORE_KIND", "local")
    monkeypatch.setattr(constants, "SERVE_OBJECT_STORE_LOCAL_ROOT", str(bucket))
    monkeypatch.setattr(constants, "SERVE_OBJECT_STORE_PREFIX", "")

    store = default_artifact_store()
    assert isinstance(store, ObjectStoreArtifactStore)
    assert store.list_run_ids("plan-periodic") == ["r1"]


def test_default_artifact_store_rejects_unknown_backend(monkeypatch) -> None:
    monkeypatch.setattr(constants, "SERVE_ARTIFACT_BACKEND", "nonsense")
    with pytest.raises(ValueError, match="SERVE_ARTIFACT_BACKEND"):
        default_artifact_store()


def test_build_object_store_local_requires_root(monkeypatch) -> None:
    monkeypatch.setattr(constants, "SERVE_OBJECT_STORE_KIND", "local")
    monkeypatch.setattr(constants, "SERVE_OBJECT_STORE_LOCAL_ROOT", "")
    with pytest.raises(ValueError, match="SERVE_OBJECT_STORE_LOCAL_ROOT"):
        build_object_store_from_constants()

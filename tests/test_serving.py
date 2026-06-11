"""Thin service API: plan retrieval and feasibility endpoints."""

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from fl_op.core import paths
from fl_op.serving import api as serving_api


@pytest.fixture
def data_root(tmp_path, monkeypatch) -> pathlib.Path:
    """A fabricated $DATA_DIR with periodic and rolling plan runs."""
    root = tmp_path / ".data"

    periodic_old = root / "plan-periodic" / "20260101T000000"
    periodic_new = root / "plan-periodic" / "20260102T000000"
    for run_dir, plan_id in ((periodic_old, "plan-old"), (periodic_new, "plan-new")):
        run_dir.mkdir(parents=True)
        (run_dir / "plan.json").write_text(json.dumps({"plan_id": plan_id}))

    rolling = root / "plan-rolling" / "20260103T000000"
    for n in range(2):
        rev_dir = rolling / "revisions" / f"{n:03d}"
        rev_dir.mkdir(parents=True)
        (rev_dir / "plan.json").write_text(json.dumps({"revision": n}))
    (rolling / "revisions_summary.json").write_text(
        json.dumps({"revisions": [{"revision": 0}, {"revision": 1}]})
    )

    monkeypatch.setattr(paths, "DATA_ROOT", root)
    return root


@pytest.fixture
def client(data_root) -> TestClient:
    return TestClient(serving_api.create_app())


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_periodic_runs_newest_last(client) -> None:
    response = client.get("/plans/periodic")
    assert response.status_code == 200
    assert response.json() == {
        "mode": "periodic",
        "runs": ["20260101T000000", "20260102T000000"],
    }


def test_get_latest_periodic_plan(client) -> None:
    response = client.get("/plans/periodic/latest")
    assert response.status_code == 200
    assert response.json() == {"plan_id": "plan-new"}


def test_get_periodic_plan_by_run_id(client) -> None:
    response = client.get("/plans/periodic/20260101T000000")
    assert response.status_code == 200
    assert response.json() == {"plan_id": "plan-old"}


def test_get_rolling_plan_returns_newest_revision(client) -> None:
    response = client.get("/plans/rolling/latest")
    assert response.status_code == 200
    assert response.json() == {"revision": 1}


def test_list_rolling_revisions(client) -> None:
    response = client.get("/plans/rolling/20260103T000000/revisions")
    assert response.status_code == 200
    assert response.json()["revisions"] == [{"revision": 0}, {"revision": 1}]


def test_get_single_rolling_revision(client) -> None:
    response = client.get("/plans/rolling/latest/revisions/0")
    assert response.status_code == 200
    assert response.json() == {"revision": 0}


def test_unknown_run_id_is_404(client) -> None:
    assert client.get("/plans/periodic/20990101T000000").status_code == 404
    assert client.get("/plans/rolling/latest/revisions/9").status_code == 404


def test_invalid_mode_is_rejected(client) -> None:
    assert client.get("/plans/quarterly").status_code == 422


def test_traversal_run_id_is_rejected(client) -> None:
    response = client.get("/plans/periodic/..%2F..%2Fetc")
    assert response.status_code in (400, 404)


def test_feasibility_resolves_latest_dirs(client, data_root, monkeypatch) -> None:
    (data_root / "generate-data" / "20260101T000000").mkdir(parents=True)
    (data_root / "solve" / "20260101T000000").mkdir(parents=True)

    calls: dict[str, str] = {}

    def fake_evaluate(data_dir: str, schedule_dir: str, order: dict) -> dict:
        calls["data_dir"] = data_dir
        calls["schedule_dir"] = schedule_dir
        calls["task_id"] = order["order_id"]
        return {"task_id": order["order_id"], "feasible": True, "candidates": []}

    monkeypatch.setattr(serving_api, "evaluate_query", fake_evaluate)

    response = client.post("/feasibility", json={"order": {"order_id": "o-1"}})
    assert response.status_code == 200
    assert response.json()["feasible"] is True
    assert calls["data_dir"].endswith("generate-data/20260101T000000")
    assert calls["schedule_dir"].endswith("solve/20260101T000000")
    assert calls["task_id"] == "o-1"


def test_feasibility_without_dataset_is_client_error(client) -> None:
    response = client.post("/feasibility", json={"order": {"order_id": "o-1"}})
    assert response.status_code == 400

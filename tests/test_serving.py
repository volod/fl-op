"""Thin service API: plan retrieval and feasibility endpoints."""

import json
import pathlib
from typing import Any

import anyio
import pytest
from starlette import testclient as starlette_testclient

from fl_op.core import constants
from fl_op.core import paths
from fl_op.serving import api as serving_api
from fl_op.serving.artifacts import FilesystemArtifactStore

httpx = starlette_testclient.httpx


class AsgiClient:
    """Synchronous test helper over ASGITransport.

    Starlette's blocking TestClient portal can hang under the restricted
    sandbox used for these tests. ASGITransport exercises the same FastAPI app
    without that portal.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    def get(self, url: str, **kwargs: Any):
        return anyio.run(self._request, "GET", url, kwargs)

    def post(self, url: str, **kwargs: Any):
        return anyio.run(self._request, "POST", url, kwargs)

    async def _request(self, method: str, url: str, kwargs: dict[str, Any]):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, url, **kwargs)


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
    monkeypatch.setattr(constants, "SERVE_ARTIFACT_ROOT", "")
    monkeypatch.setattr(constants, "SERVE_AUTH_TOKEN", "")
    return root


@pytest.fixture
def client(data_root) -> AsgiClient:
    return AsgiClient(serving_api.create_app())


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


def test_bearer_auth_protects_plan_and_feasibility_routes(data_root) -> None:
    client = AsgiClient(serving_api.create_app(auth_token="secret-token"))

    assert client.get("/health").status_code == 200
    assert client.get("/plans/periodic").status_code == 401
    assert (
        client.get(
            "/plans/periodic",
            headers={"Authorization": "Bearer wrong-token"},
        ).status_code
        == 401
    )

    response = client.get(
        "/plans/periodic",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    assert response.json()["runs"] == ["20260101T000000", "20260102T000000"]


def test_shared_artifact_root_serves_plans(tmp_path) -> None:
    shared_root = tmp_path / "shared-artifacts"
    run_dir = shared_root / "plan-periodic" / "20260201T000000"
    run_dir.mkdir(parents=True)
    (run_dir / "plan.json").write_text(json.dumps({"plan_id": "shared-plan"}))

    client = AsgiClient(
        serving_api.create_app(
            artifact_store=FilesystemArtifactStore(shared_root),
        )
    )

    response = client.get("/plans/periodic/latest")
    assert response.status_code == 200
    assert response.json() == {"plan_id": "shared-plan"}


def test_feasibility_accepts_artifact_relative_run_paths(
    data_root,
    monkeypatch,
) -> None:
    (data_root / "generate-data" / "20260101T000000").mkdir(parents=True)
    (data_root / "solve" / "20260101T000000").mkdir(parents=True)
    calls: dict[str, str] = {}

    def fake_evaluate(data_dir: str, schedule_dir: str, order: dict) -> dict:
        calls["data_dir"] = data_dir
        calls["schedule_dir"] = schedule_dir
        return {"task_id": order["order_id"], "feasible": True, "candidates": []}

    monkeypatch.setattr(serving_api, "evaluate_query", fake_evaluate)
    client = AsgiClient(serving_api.create_app())

    response = client.post(
        "/feasibility",
        json={
            "order": {"order_id": "o-1"},
            "data": "generate-data/20260101T000000",
            "schedule": "solve/20260101T000000",
        },
    )

    assert response.status_code == 200
    assert calls["data_dir"].endswith("generate-data/20260101T000000")
    assert calls["schedule_dir"].endswith("solve/20260101T000000")


def test_feasibility_rejects_escaped_artifact_paths(client) -> None:
    response = client.post(
        "/feasibility",
        json={
            "order": {"order_id": "o-1"},
            "data": "../generate-data/20260101T000000",
            "schedule": "latest",
        },
    )
    assert response.status_code == 400


def test_nonlocal_serve_requires_auth_token(monkeypatch) -> None:
    monkeypatch.setattr(constants, "SERVE_AUTH_TOKEN", "")
    with pytest.raises(ValueError, match="SERVE_AUTH_TOKEN"):
        serving_api.run_serve("0.0.0.0", 8000)

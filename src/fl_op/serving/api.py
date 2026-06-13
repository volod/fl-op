"""Thin service API over published plans and query-contract feasibility.

Read-only by design: the API exposes what the planning pipelines already
publish under ``$DATA_DIR`` (periodic plans, rolling revisions) plus the
query-contract feasibility evaluation; it never mutates datasets or plans.

Endpoints:
  GET  /health                                liveness probe
  GET  /plans/{mode}                          published run ids, newest last
  GET  /plans/{mode}/{run_id}                 plan document ('latest' allowed;
                                              rolling: the newest revision)
  GET  /plans/rolling/{run_id}/revisions      rolling revision summary
  GET  /plans/rolling/{run_id}/revisions/{n}  one rolling revision's plan
  POST /feasibility                           query-contract evaluation for a
                                              new order against a schedule
"""

import ipaddress
import json
import logging
import pathlib
import secrets
from enum import Enum
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from fl_op.core import constants
from fl_op.serving.artifacts import ArtifactStore, default_artifact_store
from fl_op.solver.query_pipeline import evaluate_query

logger = logging.getLogger(__name__)

LATEST = "latest"

# Sentinel for "run directory" path segments: anything that is not a plain
# directory name (separators, '.' or '..') is rejected before touching the
# filesystem.
_PLAN_FILENAME = "plan.json"
_REVISIONS_DIRNAME = "revisions"
_REVISIONS_SUMMARY_FILENAME = "revisions_summary.json"


class PlanMode(str, Enum):
    PERIODIC = "periodic"
    ROLLING = "rolling"


class FeasibilityRequest(BaseModel):
    """Query-contract request: a new order evaluated against published state."""

    order: dict[str, Any]
    data: str = Field(
        default=LATEST,
        description=(
            "Dataset run id, artifact-relative generate-data path, or 'latest'."
        ),
    )
    schedule: str = Field(
        default=LATEST,
        description="Solve run id, artifact-relative solve path, or 'latest'.",
    )


def _plan_subdir(mode: PlanMode) -> pathlib.PurePath:
    return pathlib.PurePath(f"plan-{mode.value}")


def _is_plain_artifact_id(value: str) -> bool:
    return pathlib.PurePath(value).name == value and value not in {"", ".", ".."}


def _list_runs(store: ArtifactStore, mode: PlanMode) -> list[str]:
    return store.list_run_ids(str(_plan_subdir(mode)))


def _resolve_run_rel(
    store: ArtifactStore,
    mode: PlanMode,
    run_id: str,
) -> pathlib.PurePath:
    if run_id != LATEST and not _is_plain_artifact_id(run_id):
        raise HTTPException(status_code=400, detail=f"invalid run id '{run_id}'")
    runs = _list_runs(store, mode)
    if not runs:
        raise HTTPException(
            status_code=404, detail=f"no published {mode.value} plan runs"
        )
    if run_id == LATEST:
        run_id = runs[-1]
    if run_id not in runs:
        raise HTTPException(
            status_code=404, detail=f"unknown {mode.value} plan run '{run_id}'"
        )
    return _plan_subdir(mode) / run_id


def _read_json(store: ArtifactStore, relative_path: pathlib.PurePath) -> dict[str, Any]:
    if not store.exists(relative_path):
        raise HTTPException(
            status_code=404, detail=f"missing artifact {relative_path.name}"
        )
    try:
        return store.read_json(relative_path)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500, detail=f"invalid artifact JSON {relative_path.name}"
        ) from exc


def _rolling_revision_ids(
    store: ArtifactStore,
    run_rel: pathlib.PurePath,
) -> list[str]:
    return store.list_run_ids(str(run_rel / _REVISIONS_DIRNAME))


def _resolve_latest_artifact_dir(
    store: ArtifactStore,
    value: str,
    subdir: str,
) -> pathlib.Path:
    if value.lower() == LATEST:
        runs = store.list_run_ids(subdir)
        if not runs:
            raise HTTPException(
                status_code=400, detail=f"No runs found under {subdir}"
            )
        return store.local_path(pathlib.PurePath(subdir) / runs[-1])
    if _is_plain_artifact_id(value):
        return store.local_path(pathlib.PurePath(subdir) / value)

    candidate = pathlib.PurePath(value)
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise HTTPException(status_code=400, detail=f"invalid artifact path '{value}'")
    if not candidate.parts or candidate.parts[0] != subdir:
        raise HTTPException(
            status_code=400,
            detail=f"artifact path must be a run id or live under {subdir}",
        )
    return store.local_path(candidate)


def _auth_dependency(token: str):
    async def require_auth(
        authorization: str | None = Header(default=None),
    ) -> None:
        if not token:
            return
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not supplied:
            raise HTTPException(
                status_code=401,
                detail="missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(
                status_code=401,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_auth


def create_app(
    artifact_store: ArtifactStore | None = None,
    auth_token: str | None = None,
) -> FastAPI:
    store = artifact_store or default_artifact_store()
    token = constants.SERVE_AUTH_TOKEN if auth_token is None else auth_token
    protected = [Depends(_auth_dependency(token))]
    app = FastAPI(
        title="fl-op serving API",
        description=(
            "Query-contract feasibility checks and published plan retrieval."
        ),
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/plans/{mode}", dependencies=protected)
    async def list_plans(mode: PlanMode) -> dict[str, Any]:
        return {"mode": mode.value, "runs": _list_runs(store, mode)}

    @app.get("/plans/{mode}/{run_id}", dependencies=protected)
    async def get_plan(mode: PlanMode, run_id: str) -> dict[str, Any]:
        run_rel = _resolve_run_rel(store, mode, run_id)
        if mode is PlanMode.PERIODIC:
            return _read_json(store, run_rel / _PLAN_FILENAME)
        revisions = _rolling_revision_ids(store, run_rel)
        if not revisions:
            raise HTTPException(
                status_code=404, detail=f"run '{run_rel.name}' has no revisions"
            )
        return _read_json(
            store, run_rel / _REVISIONS_DIRNAME / revisions[-1] / _PLAN_FILENAME
        )

    @app.get("/plans/rolling/{run_id}/revisions", dependencies=protected)
    async def list_revisions(run_id: str) -> dict[str, Any]:
        run_rel = _resolve_run_rel(store, PlanMode.ROLLING, run_id)
        return _read_json(store, run_rel / _REVISIONS_SUMMARY_FILENAME)

    @app.get("/plans/rolling/{run_id}/revisions/{number}", dependencies=protected)
    async def get_revision(run_id: str, number: int) -> dict[str, Any]:
        run_rel = _resolve_run_rel(store, PlanMode.ROLLING, run_id)
        revisions = _rolling_revision_ids(store, run_rel)
        if number < 0 or number >= len(revisions):
            raise HTTPException(
                status_code=404,
                detail=f"run '{run_rel.name}' has no revision {number}",
            )
        return _read_json(
            store, run_rel / _REVISIONS_DIRNAME / revisions[number] / _PLAN_FILENAME
        )

    @app.post("/feasibility", dependencies=protected)
    async def feasibility(request: FeasibilityRequest) -> dict[str, Any]:
        try:
            data_dir = _resolve_latest_artifact_dir(
                store, request.data, "generate-data"
            )
            schedule_dir = _resolve_latest_artifact_dir(
                store, request.schedule, "solve"
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - resolution errors are client errors
            raise HTTPException(status_code=400, detail=str(exc))
        try:
            return evaluate_query(str(data_dir), str(schedule_dir), request.order)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    return app


def _is_nonlocal_host(host: str) -> bool:
    normalized = host.strip().strip("[]")
    if normalized == "localhost":
        return False
    try:
        return not ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return True


def run_serve(host: str, port: int) -> None:
    """Run the service API with uvicorn (blocking)."""
    if _is_nonlocal_host(host) and not constants.SERVE_AUTH_TOKEN:
        raise ValueError(
            "SERVE_AUTH_TOKEN is required when binding fl-op serve outside loopback"
        )
    import uvicorn

    logger.info("Serving fl-op API on http://%s:%d", host, port)
    uvicorn.run(create_app(), host=host, port=port, log_config=None)

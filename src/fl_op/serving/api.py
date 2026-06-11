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

import json
import logging
import pathlib
from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from fl_op.core import paths
from fl_op.solver.query_pipeline import evaluate_query

logger = logging.getLogger(__name__)

LATEST = "latest"

# Sentinel for "run directory" path segments: anything that is not a plain
# directory name (separators, '..') is rejected before touching the filesystem.
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
        description="Dataset run directory, or 'latest' for the newest generate-data run.",
    )
    schedule: str = Field(
        default=LATEST,
        description="Solve run directory, or 'latest' for the newest solve run.",
    )


def _plans_base(mode: PlanMode) -> pathlib.Path:
    return paths.DATA_ROOT / f"plan-{mode.value}"


def _list_runs(mode: PlanMode) -> list[str]:
    base = _plans_base(mode)
    if not base.exists():
        return []
    runs = sorted(d for d in base.iterdir() if d.is_dir())
    return [d.name for d in runs]


def _resolve_run_dir(mode: PlanMode, run_id: str) -> pathlib.Path:
    if run_id != LATEST and pathlib.PurePath(run_id).name != run_id:
        raise HTTPException(status_code=400, detail=f"invalid run id '{run_id}'")
    runs = _list_runs(mode)
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
    return _plans_base(mode) / run_id


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"missing artifact {path.name}")
    return json.loads(path.read_text())


def _rolling_revision_dirs(run_dir: pathlib.Path) -> list[pathlib.Path]:
    revisions = run_dir / _REVISIONS_DIRNAME
    if not revisions.exists():
        return []
    return sorted(d for d in revisions.iterdir() if d.is_dir())


def create_app() -> FastAPI:
    app = FastAPI(
        title="fl-op serving API",
        description=(
            "Query-contract feasibility checks and published plan retrieval."
        ),
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/plans/{mode}")
    def list_plans(mode: PlanMode) -> dict[str, Any]:
        return {"mode": mode.value, "runs": _list_runs(mode)}

    @app.get("/plans/{mode}/{run_id}")
    def get_plan(mode: PlanMode, run_id: str) -> dict[str, Any]:
        run_dir = _resolve_run_dir(mode, run_id)
        if mode is PlanMode.PERIODIC:
            return _read_json(run_dir / _PLAN_FILENAME)
        revisions = _rolling_revision_dirs(run_dir)
        if not revisions:
            raise HTTPException(
                status_code=404, detail=f"run '{run_dir.name}' has no revisions"
            )
        return _read_json(revisions[-1] / _PLAN_FILENAME)

    @app.get("/plans/rolling/{run_id}/revisions")
    def list_revisions(run_id: str) -> dict[str, Any]:
        run_dir = _resolve_run_dir(PlanMode.ROLLING, run_id)
        return _read_json(run_dir / _REVISIONS_SUMMARY_FILENAME)

    @app.get("/plans/rolling/{run_id}/revisions/{number}")
    def get_revision(run_id: str, number: int) -> dict[str, Any]:
        run_dir = _resolve_run_dir(PlanMode.ROLLING, run_id)
        revisions = _rolling_revision_dirs(run_dir)
        if number < 0 or number >= len(revisions):
            raise HTTPException(
                status_code=404,
                detail=f"run '{run_dir.name}' has no revision {number}",
            )
        return _read_json(revisions[number] / _PLAN_FILENAME)

    @app.post("/feasibility")
    def feasibility(request: FeasibilityRequest) -> dict[str, Any]:
        try:
            data_dir = paths.resolve_latest(request.data, "generate-data")
            schedule_dir = paths.resolve_latest(request.schedule, "solve")
        except Exception as exc:  # noqa: BLE001 - resolution errors are client errors
            raise HTTPException(status_code=400, detail=str(exc))
        try:
            return evaluate_query(str(data_dir), str(schedule_dir), request.order)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    return app


def run_serve(host: str, port: int) -> None:
    """Run the service API with uvicorn (blocking)."""
    import uvicorn

    logger.info("Serving fl-op API on http://%s:%d", host, port)
    uvicorn.run(create_app(), host=host, port=port, log_config=None)

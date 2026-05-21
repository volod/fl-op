"""Load solve artifacts for analysis."""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SolveArtifacts:
    path: pathlib.Path
    schedule: list[dict[str, Any]]
    infeasible: list[dict[str, Any]]
    kpis: dict[str, Any]
    metadata: dict[str, Any]
    telemetry: dict[str, Any]


def load_solve_artifacts(schedule_dir: str) -> SolveArtifacts:
    path = pathlib.Path(schedule_dir)
    schedule_doc = _read_json(path / "schedule.json")
    kpis = _read_json(path / "schedule_kpis.json")
    infeasible_doc = _read_json(path / "infeasible_orders.json")

    return SolveArtifacts(
        path=path,
        schedule=schedule_doc.get("schedule", []),
        infeasible=infeasible_doc.get("infeasible_orders", []),
        kpis=kpis,
        metadata=kpis.get("run_metadata", {}),
        telemetry=kpis.get("run_telemetry", {}),
    )


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required artifact is missing: {path}")
    with path.open() as fh:
        return json.load(fh)

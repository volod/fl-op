"""Reviewed tuned solver-parameter artifact.

The checked-in OptimizationProfile remains the reviewed baseline. A tuning run
can be promoted separately into ``DATA_DIR/tune/solver-parameters-tuned.json``;
periodic and rolling adapters layer that artifact on top of the profile's
allocation policy unless a caller passes explicit ``SolverParameters``.
"""

import dataclasses
import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.core.constants import (
    ARTIFACT_SCHEMA_VERSION,
    TUNED_SOLVER_PARAMETERS_FILENAME,
)
from fl_op.core.paths import DATA_ROOT
from fl_op.planning.artifacts import write_json
from fl_op.solver.parameters import SolverParameters

logger = logging.getLogger(__name__)

_ARTIFACT_KIND = "ReviewedTunedSolverProfile"
_PARAMETER_FIELDS = {field.name for field in dataclasses.fields(SolverParameters)}
_INT_FIELDS = {
    field.name
    for field in dataclasses.fields(SolverParameters)
    if field.type is int or field.type == "int"
}


def default_tuned_solver_profile_path() -> pathlib.Path:
    return DATA_ROOT / "tune" / TUNED_SOLVER_PARAMETERS_FILENAME


def _coerce_parameters(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only SolverParameters fields, with light type coercion."""
    params: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _PARAMETER_FIELDS:
            logger.warning("Ignoring unknown tuned solver parameter '%s'", key)
            continue
        try:
            params[key] = int(value) if key in _INT_FIELDS else float(value)
        except (TypeError, ValueError):
            logger.warning("Ignoring unusable tuned solver parameter %s=%r", key, value)
    return params


def load_tuned_solver_parameters(
    path: Optional[pathlib.Path] = None,
) -> dict[str, Any]:
    """Read reviewed solver-parameter overrides; empty when absent/unreadable."""
    target = path or default_tuned_solver_profile_path()
    if not target.exists():
        return {}
    try:
        doc = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable tuned solver profile %s: %s", target, exc)
        return {}
    if doc.get("kind") != _ARTIFACT_KIND:
        logger.warning("Ignoring %s: unexpected kind %r", target, doc.get("kind"))
        return {}
    raw = doc.get("solverParameters") or {}
    if not isinstance(raw, dict):
        logger.warning("Ignoring %s: solverParameters is not an object", target)
        return {}
    return _coerce_parameters(raw)


def apply_tuned_solver_parameters(
    base: SolverParameters,
    overrides: dict[str, Any],
) -> SolverParameters:
    """Layer reviewed overrides on top of a base SolverParameters object."""
    if not overrides:
        return base
    updates = _coerce_parameters(overrides)
    if not updates:
        return base
    logger.info("Applying tuned solver parameters: %s", updates)
    return dataclasses.replace(base, **updates)


def solver_parameters_for_profile(
    profile: Any,
    explicit: Optional[SolverParameters] = None,
    tuned_path: Optional[pathlib.Path] = None,
) -> SolverParameters:
    """Effective SolverParameters for a profile-aware planning run.

    Explicit caller parameters win. Otherwise the profile contributes the
    allocation count-vs-margin knob and the reviewed tuned artifact contributes
    operational solver knobs.
    """
    if explicit is not None:
        return explicit
    base = SolverParameters(
        assignment_count_priority=profile.allocationPolicy.countPriority
    )
    return apply_tuned_solver_parameters(
        base, load_tuned_solver_parameters(tuned_path)
    )


def promote_best_params(
    best_params_path: pathlib.Path,
    output_path: Optional[pathlib.Path] = None,
    reviewed_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> pathlib.Path:
    """Promote a tuning run's best_params.json into the reviewed overlay."""
    try:
        source = json.loads(best_params_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Could not read best params from {best_params_path}: {exc}"
        ) from exc
    raw_params = source.get("best_params")
    if not isinstance(raw_params, dict):
        raise ValueError(f"{best_params_path} does not contain a best_params object")
    params = _coerce_parameters(raw_params)
    if not params:
        raise ValueError(f"{best_params_path} contains no valid solver parameters")
    target = output_path or default_tuned_solver_profile_path()
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": _ARTIFACT_KIND,
        "reviewed": True,
        "reviewed_at": datetime.now(tz=timezone.utc).isoformat(),
        "reviewed_by": reviewed_by or "",
        "notes": notes or "",
        "source_best_params": str(best_params_path),
        "source_snapshot_hashes": source.get("snapshot_hashes")
        or ([source["snapshot_hash"]] if source.get("snapshot_hash") else []),
        "source_n_trials": source.get("n_trials", 0),
        "source_seed": source.get("seed"),
        "source_objectives": {
            "best_objective": source.get("best_objective"),
            "baseline_objective": source.get("baseline_objective"),
            "improvement_over_baseline": source.get("improvement_over_baseline"),
        },
        "solverParameters": params,
    }
    write_json(artifact, target)
    logger.info("Promoted tuned solver parameters -> %s", target)
    return target

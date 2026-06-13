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
    ADAPTER_VERSION,
    ARTIFACT_SCHEMA_VERSION,
    TUNED_SOLVER_PARAMETERS_FILENAME,
)
from fl_op.core.paths import DATA_ROOT
from fl_op.planning.artifacts import write_json
from fl_op.solver.parameters import SolverParameters

logger = logging.getLogger(__name__)

_ARTIFACT_KIND = "ReviewedTunedSolverProfile"
_PROFILE_DOMAIN = {
    "drone-logistics": "drone_logistics",
    "agricultural-custom-services": "agricultural",
    "construction-earthworks": "construction",
    "roadside-infrastructure": "roadside",
}
_PARAMETER_FIELDS = {field.name for field in dataclasses.fields(SolverParameters)}
_INT_FIELDS = {
    field.name
    for field in dataclasses.fields(SolverParameters)
    if field.type is int or field.type == "int"
}


def default_tuned_solver_profile_path() -> pathlib.Path:
    return DATA_ROOT / "tune" / TUNED_SOLVER_PARAMETERS_FILENAME


def scoped_tuned_solver_profile_path(
    domain_id: str,
    profile_id: str,
    adapter_version: str,
) -> pathlib.Path:
    return (
        DATA_ROOT
        / "tune"
        / _slug(domain_id)
        / _slug(profile_id)
        / _slug(adapter_version)
        / TUNED_SOLVER_PARAMETERS_FILENAME
    )


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
    *,
    domain_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    adapter_version: Optional[str] = None,
    now: Optional[datetime] = None,
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
    if not _scope_matches(
        doc,
        domain_id=domain_id,
        profile_id=profile_id,
        adapter_version=adapter_version,
        now=now,
        path=target,
    ):
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
    domain_id: Optional[str] = None,
    adapter_version: Optional[str] = None,
) -> SolverParameters:
    """Effective SolverParameters for a profile-aware planning run.

    Explicit caller parameters win. Otherwise the profile contributes the
    allocation count-vs-margin knob and the reviewed tuned artifact contributes
    operational solver knobs.
    """
    if explicit is not None:
        return explicit
    base_updates: dict[str, Any] = {}
    profile_id = getattr(getattr(profile, "metadata", None), "id", "")
    domain_id = domain_id or _PROFILE_DOMAIN.get(profile_id, "")
    adapter_version = adapter_version or ADAPTER_VERSION
    if profile_id == "drone-logistics":
        from fl_op.data.drone_logistics_tuning import (
            drone_solver_parameter_overrides,
            load_drone_logistics_tuning,
        )

        base_updates.update(
            drone_solver_parameter_overrides(load_drone_logistics_tuning())
        )
    base = SolverParameters(
        assignment_count_priority=profile.allocationPolicy.countPriority,
        **_coerce_parameters(base_updates),
    )
    if tuned_path is not None:
        overrides = load_tuned_solver_parameters(tuned_path)
    elif profile_id == "drone-logistics" and domain_id:
        overrides = load_tuned_solver_parameters(
            scoped_tuned_solver_profile_path(
                domain_id, profile_id, adapter_version
            ),
            domain_id=domain_id,
            profile_id=profile_id,
            adapter_version=adapter_version,
        )
    else:
        overrides = load_tuned_solver_parameters(default_tuned_solver_profile_path())
    return apply_tuned_solver_parameters(base, overrides)


def promote_best_params(
    best_params_path: pathlib.Path,
    output_path: Optional[pathlib.Path] = None,
    reviewed_by: Optional[str] = None,
    notes: Optional[str] = None,
    domain_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    adapter_version: Optional[str] = None,
    expires_at: Optional[str] = None,
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
    if output_path is not None:
        target = output_path
    elif domain_id and profile_id and adapter_version:
        target = scoped_tuned_solver_profile_path(
            domain_id, profile_id, adapter_version
        )
    else:
        target = default_tuned_solver_profile_path()
    reviewed_at = datetime.now(tz=timezone.utc).isoformat()
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": _ARTIFACT_KIND,
        "reviewed": True,
        "reviewed_at": reviewed_at,
        "reviewed_by": reviewed_by or "",
        "notes": notes or "",
        "scope": {
            "domain": domain_id or "",
            "profile_id": profile_id or "",
            "adapter_version": adapter_version or "",
            "valid_from": reviewed_at,
            "expires_at": expires_at or "",
        },
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


def _scope_matches(
    doc: dict[str, Any],
    *,
    domain_id: Optional[str],
    profile_id: Optional[str],
    adapter_version: Optional[str],
    now: Optional[datetime],
    path: pathlib.Path,
) -> bool:
    scope = doc.get("scope") or {}
    checks = {
        "domain": domain_id,
        "profile_id": profile_id,
        "adapter_version": adapter_version,
    }
    for key, expected in checks.items():
        if expected and scope.get(key) != expected:
            logger.warning(
                "Ignoring %s: %s scope %r does not match %r",
                path,
                key,
                scope.get(key),
                expected,
            )
            return False
    expires_at = scope.get("expires_at") or ""
    if expires_at:
        try:
            expiry = datetime.fromisoformat(str(expires_at))
        except ValueError:
            logger.warning("Ignoring %s: invalid expires_at %r", path, expires_at)
            return False
        current = now or datetime.now(tz=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if current > expiry:
            logger.warning("Ignoring %s: tuned solver overlay expired at %s", path, expiry)
            return False
    return True


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)

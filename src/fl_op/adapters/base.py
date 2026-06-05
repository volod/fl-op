"""Shared adapter helpers: result normalization and profile validation."""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fl_op.adapters.reason_codes import to_reason_code
from fl_op.adapters.spi import AdapterManifest, ValidationReport
from fl_op.canonical.bundle import compute_bundle_id
from fl_op.canonical.plan import Assignment, UnassignedTask
from fl_op.core.constants import MAPPING_VERSION

if TYPE_CHECKING:
    from fl_op.contracts.profile import OptimizationProfile

logger = logging.getLogger(__name__)


def _parse_ts(value: str) -> datetime:
    if not value:
        return datetime.now(tz=timezone.utc)
    return datetime.fromisoformat(value)


def dispatch_to_assignment(dp: dict[str, Any]) -> Assignment:
    """Convert a legacy DispatchPackage dict into a canonical Assignment."""
    vehicle_id = dp.get("vehicle_id", "")
    implement_id = dp.get("implement_id", "")
    operator_id = dp.get("operator_id", "")
    bundle_id = compute_bundle_id([vehicle_id, implement_id], [], MAPPING_VERSION)
    return Assignment(
        assignment_id=dp.get("dispatch_id", f"assign-{dp.get('order_id', '')}"),
        task_id=dp.get("order_id", ""),
        bundle_id=bundle_id,
        asset_ids=[a for a in (vehicle_id, implement_id) if a],
        operator_ids=[operator_id] if operator_id else [],
        planned_start=_parse_ts(dp.get("scheduled_start", "")),
        planned_finish=_parse_ts(dp.get("scheduled_end", "")),
        expected_margin_eur=float(dp.get("estimated_margin_eur", 0.0)),
        explanation_ref=f"explain://assignment/{dp.get('order_id', '')}",
    )


def infeasible_to_unassigned(inf: dict[str, Any]) -> UnassignedTask:
    """Convert a legacy InfeasibleOrder dict into a canonical UnassignedTask."""
    reason = inf.get("reason", "unknown")
    return UnassignedTask(
        task_id=inf.get("order_id", ""),
        reason_code=to_reason_code(reason),
        details={"legacy_reason": reason, "detail": inf.get("detail", ""),
                 "cluster_id": inf.get("cluster_id", "")},
        explanation_ref=f"explain://unassigned/{inf.get('order_id', '')}",
    )


def validate_profile_against_features(
    profile: "OptimizationProfile",
    supported_constraints: set[str],
) -> ValidationReport:
    """A profile may only run if every enforced constraint is supported (spec 21.2)."""
    unsupported = [
        c.id for c in profile.constraints
        if c.enforced and c.id not in supported_constraints
    ]
    return ValidationReport(
        ok=not unsupported,
        unsupported_constraints=unsupported,
        messages=(
            [] if not unsupported
            else [f"adapter does not support enforced constraints: {unsupported}"]
        ),
    )

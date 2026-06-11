"""Canonical OperationalBundle abstraction."""

import hashlib
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from fl_op.canonical.asset import Capability
from fl_op.canonical.common import TimeInterval


def compute_bundle_id(
    asset_ids: list[str],
    operator_ids: list[str],
    configuration_version: str,
) -> str:
    """Deterministic bundle identity.

    bundleId = hash(sorted(assetIds) + sorted(operatorIds) + configurationVersion)
    """
    payload = "|".join(
        ["A:" + ",".join(sorted(asset_ids))]
        + ["O:" + ",".join(sorted(operator_ids))]
        + ["C:" + configuration_version]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"bundle-{digest}"


class BundleFeasibilitySummary(BaseModel):
    """Compact, exact summary of the snapshot's feasible bundle space.

    Replaces the formerly capped materialized bundle list: counts are computed
    vectorised over the full prime-mover x related-equipment cross product, so
    they are exact at any fleet size while the snapshot stays small. Full
    bundles are enumerated lazily on demand (`snapshot.bundles.iter_bundles`).
    The solver performs its own compatibility filtering, so this summary is an
    explanation artifact, never an assignment input.
    """

    model_config = ConfigDict(frozen=True)

    n_prime_movers: int = 0
    n_related_equipment: int = 0
    # Exact count of power-feasible (prime mover, related equipment) pairs.
    n_feasible_pairs: int = 0
    # Feasible pair count per operation type the pair can perform.
    pairs_by_operation: dict[str, int] = Field(default_factory=dict)
    # Resources no feasible pair can use (explanation: dead capacity).
    n_unmatched_prime_movers: int = 0
    n_unmatched_related_equipment: int = 0


class OperationalBundle(BaseModel):
    """A schedulable combination of resources (prime mover + implement + operator)."""

    model_config = ConfigDict(frozen=True)

    bundle_id: str
    bundle_type: str
    asset_ids: list[str] = Field(default_factory=list)
    operator_ids: list[str] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    current_location_ref: Optional[str] = None
    availability: list[TimeInterval] = Field(default_factory=list)
    bundle_status: str = "feasible"
    configuration_duration_minutes: int = 0
    source_snapshot_id: Optional[str] = None

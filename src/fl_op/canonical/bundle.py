"""Canonical OperationalBundle abstraction (spec 11.4, 18.4)."""

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
    """Deterministic bundle identity (spec 18.4).

    bundleId = hash(sorted(assetIds) + sorted(operatorIds) + configurationVersion)
    """
    payload = "|".join(
        ["A:" + ",".join(sorted(asset_ids))]
        + ["O:" + ",".join(sorted(operator_ids))]
        + ["C:" + configuration_version]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"bundle-{digest}"


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

"""Immutable PlanningSnapshot abstraction.

A snapshot is the single artifact a solver adapter is allowed to consume; no
adapter reads raw source data. The snapshot carries both the
canonical objects (used for hashing, quality, explanation) and a non-canonical
`solver_payload` bridge: the dict-shaped rows the existing OR-Tools solver chain
already expects. The bridge is excluded from the reproducible snapshot hash.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from fl_op.canonical.asset import Asset
from fl_op.canonical.bundle import OperationalBundle
from fl_op.canonical.commitment import Commitment, InventoryPosition
from fl_op.canonical.common import (
    QualityFinding,
    QualitySummary,
    TimeInterval,
    VersionDimensions,
)
from fl_op.canonical.enums import PlanningMode
from fl_op.canonical.forecast import Forecast
from fl_op.canonical.location import Location
from fl_op.canonical.task import Task

# Keys excluded from the reproducible snapshot hash: per-run identifiers and the
# non-canonical solver bridge payload.
HASH_EXCLUDED_FIELDS = ("snapshot_id", "generated_at", "solver_payload")


class PlanningSnapshot(BaseModel):
    """Immutable solver-ready state."""

    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    effective_at: datetime
    generated_at: datetime
    planning_mode: PlanningMode
    planning_horizon: TimeInterval
    version_dimensions: VersionDimensions

    assets: list[Asset] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    bundles: list[OperationalBundle] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    inventory: list[InventoryPosition] = Field(default_factory=list)
    forecasts: list[Forecast] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)
    manual_overrides: list[dict[str, Any]] = Field(default_factory=list)

    quality_findings: list[QualityFinding] = Field(default_factory=list)
    quality_summary: QualitySummary = Field(default_factory=QualitySummary)
    snapshot_hash: str = ""
    lineage_ref: str = ""

    # Non-canonical bridge to the existing dict-based solver chain. Excluded from
    # the snapshot hash; never treated as the semantic source of truth.
    solver_payload: dict[str, Any] = Field(default_factory=dict, repr=False)

    def canonical_content(self) -> dict[str, Any]:
        """Return the hashable canonical content (excludes per-run + bridge fields)."""
        data = self.model_dump(mode="json", by_alias=True)
        for key in HASH_EXCLUDED_FIELDS:
            data.pop(key, None)
        data.pop("snapshot_hash", None)
        return data

    def task_index(self) -> dict[str, Task]:
        return {t.task_id: t for t in self.tasks}

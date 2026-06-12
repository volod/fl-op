"""Canonical plan-output contract."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from fl_op.canonical.common import (
    QualitySummary,
    RiskSummary,
    VersionDimensions,
)
from fl_op.canonical.enums import (
    CorrectiveActionType,
    PlanningMode,
    PlanStatus,
    ReasonCode,
    ReservationStatus,
)


class CorrectiveAction(BaseModel):
    """A self-repair a rolling revision applied: plans must survive being wrong.

    Records why an in-flight assignment was released (asset loss), why a
    derived service task was withdrawn (prognosis contradicted by newer
    readings), or why one was escalated (asset degraded faster than forecast).
    """

    model_config = ConfigDict(frozen=True)

    action: CorrectiveActionType
    task_id: str
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class Assignment(BaseModel):
    """A task assigned to a bundle in a plan."""

    model_config = ConfigDict(frozen=True)

    assignment_id: str
    task_id: str
    bundle_id: str
    asset_ids: list[str] = Field(default_factory=list)
    operator_ids: list[str] = Field(default_factory=list)
    planned_start: datetime
    planned_finish: datetime
    route_ref: Optional[str] = None
    material_reservation_refs: list[str] = Field(default_factory=list)
    is_frozen: bool = False
    is_pinned: bool = False
    expected_revenue_eur: float = 0.0
    expected_cost_eur: float = 0.0
    expected_margin_eur: float = 0.0
    # Plan-instability tracking.
    previous_bundle_id: Optional[str] = None
    previous_start_time: Optional[datetime] = None
    change_penalty: int = 0
    explanation_ref: str = ""


class UnassignedTask(BaseModel):
    """A task that could not be assigned, with a normalized reason."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    reason_code: ReasonCode
    details: dict[str, Any] = Field(default_factory=dict)
    recommended_action: Optional[str] = None
    explanation_ref: str = ""


class MaterialReservation(BaseModel):
    """A material reservation produced by a plan."""

    model_config = ConfigDict(frozen=True)

    reservation_id: str
    task_id: str
    material_type: str
    inventory_location_ref: str
    quantity: float
    canonical_unit: str
    reserved_from: Optional[datetime] = None
    reserved_to: Optional[datetime] = None
    status: ReservationStatus = ReservationStatus.PROVISIONAL


class Plan(BaseModel):
    """A normalized dispatch plan / revision envelope.

    A rolling-plan update creates a new immutable revision; published revisions
    are never mutated.
    """

    model_config = ConfigDict(frozen=True)

    plan_id: str
    revision_id: str
    parent_revision_id: Optional[str] = None
    origin_plan_id: str
    planning_mode: PlanningMode
    snapshot_id: str
    snapshot_hash: str = ""
    version_dimensions: VersionDimensions
    adapter_id: str
    adapter_version: str
    solver_version: str = ""
    generated_at: datetime
    effective_from: datetime
    effective_to: Optional[datetime] = None
    status: PlanStatus = PlanStatus.DRAFT

    assignments: list[Assignment] = Field(default_factory=list)
    unassigned_tasks: list[UnassignedTask] = Field(default_factory=list)
    material_reservations: list[MaterialReservation] = Field(default_factory=list)
    corrective_actions: list[CorrectiveAction] = Field(default_factory=list)

    # The snapshot's visibility horizon per source contract at solve time:
    # data visible beyond these is newer than what the plan considered, the
    # signal watermark-driven replan triggering compares against.
    source_watermarks: dict[str, datetime] = Field(default_factory=dict)

    score: dict[str, Any] = Field(default_factory=dict)
    quality_summary: QualitySummary = Field(default_factory=QualitySummary)
    risk_summary: RiskSummary = Field(default_factory=RiskSummary)
    lineage_ref: str = ""

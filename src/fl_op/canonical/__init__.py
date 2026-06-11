"""Solver-neutral canonical planning model.

Source-system vocabulary (vehicle, implement, order) is mapped into these stable
abstractions so that no optimization logic depends on source field names.
"""

from fl_op.canonical.asset import Asset, Capability, GeoLocation
from fl_op.canonical.bundle import (
    BundleFeasibilitySummary,
    OperationalBundle,
    compute_bundle_id,
)
from fl_op.canonical.commitment import Commitment, InventoryPosition
from fl_op.canonical.cost import CostRate
from fl_op.canonical.common import (
    GeoPoint,
    QualityFinding,
    QualitySummary,
    RiskSummary,
    TimeInterval,
    VersionDimensions,
)
from fl_op.canonical.enums import (
    AssetMobility,
    CommitmentHardness,
    CorrectiveActionType,
    HealthStatus,
    PlanningMode,
    PlanStatus,
    QualitySeverity,
    ReasonCode,
    ReservationStatus,
)
from fl_op.canonical.forecast import Forecast
from fl_op.canonical.location import Location
from fl_op.canonical.observation import Observation
from fl_op.canonical.plan import (
    Assignment,
    CorrectiveAction,
    MaterialReservation,
    Plan,
    UnassignedTask,
)
from fl_op.canonical.snapshot import PlanningSnapshot
from fl_op.canonical.task import MaterialRequirement, Task, TaskRequirement
from fl_op.canonical.travel import TravelLink

__all__ = [
    "Asset",
    "Capability",
    "GeoLocation",
    "GeoPoint",
    "OperationalBundle",
    "BundleFeasibilitySummary",
    "compute_bundle_id",
    "Task",
    "TaskRequirement",
    "MaterialRequirement",
    "Forecast",
    "Location",
    "Observation",
    "Commitment",
    "InventoryPosition",
    "TravelLink",
    "CostRate",
    "TimeInterval",
    "VersionDimensions",
    "QualityFinding",
    "QualitySummary",
    "RiskSummary",
    "PlanningSnapshot",
    "Plan",
    "Assignment",
    "CorrectiveAction",
    "CorrectiveActionType",
    "UnassignedTask",
    "MaterialReservation",
    "PlanningMode",
    "PlanStatus",
    "ReasonCode",
    "ReservationStatus",
    "AssetMobility",
    "CommitmentHardness",
    "HealthStatus",
    "QualitySeverity",
]

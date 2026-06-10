"""Enumerations for the canonical, solver-neutral planning model."""

from enum import Enum


class PlanningMode(str, Enum):
    """Planning mode of a snapshot or plan."""

    PERIODIC = "periodic"
    ROLLING = "rolling"


class TaskStatus(str, Enum):
    """Lifecycle status of a canonical task (domain-neutral)."""

    PENDING = "pending"
    STARTED = "started"
    COMPLETED = "completed"
    INFEASIBLE = "infeasible"


class AssetMobility(str, Enum):
    """Mobility class of a canonical asset.

    Stationary assets (road sensors, field stations, fixed equipment) are never
    scheduled as movable resources; they are monitored and serviced in place.
    """

    MOBILE = "mobile"
    STATIONARY = "stationary"
    PORTABLE = "portable"


class HealthStatus(str, Enum):
    """Normalized health state of an asset, fed by observations or snapshots."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class PlanStatus(str, Enum):
    """Lifecycle status of a plan."""

    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class CommitmentHardness(str, Enum):
    """Hardness of a contractual commitment."""

    HARD = "hard"
    MEDIUM = "medium"
    SOFT = "soft"


class ReservationStatus(str, Enum):
    """Status of a material reservation."""

    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    CONSUMED = "consumed"
    RELEASED = "released"


class ReasonCode(str, Enum):
    """Normalized reason codes for unassigned tasks."""

    NO_COMPATIBLE_BUNDLE = "NO_COMPATIBLE_BUNDLE"
    INSUFFICIENT_POWER = "INSUFFICIENT_POWER"
    NO_AVAILABLE_OPERATOR = "NO_AVAILABLE_OPERATOR"
    NO_AVAILABLE_ASSET = "NO_AVAILABLE_ASSET"
    NO_VALID_WEATHER_WINDOW = "NO_VALID_WEATHER_WINDOW"
    INSUFFICIENT_MATERIAL = "INSUFFICIENT_MATERIAL"
    CONTRACT_WINDOW_INFEASIBLE = "CONTRACT_WINDOW_INFEASIBLE"
    LOCATION_DATA_INVALID = "LOCATION_DATA_INVALID"
    FIELD_GEOMETRY_INVALID = "FIELD_GEOMETRY_INVALID"
    QUALITY_POLICY_BLOCK = "QUALITY_POLICY_BLOCK"
    MANUAL_OVERRIDE_CONFLICT = "MANUAL_OVERRIDE_CONFLICT"
    OPTIMIZATION_TRADEOFF = "OPTIMIZATION_TRADEOFF"
    UNKNOWN = "UNKNOWN"


class CorrectiveActionType(str, Enum):
    """Kind of corrective action a rolling revision applied to survive being wrong."""

    # An assignment lost one of its assets mid-plan; the task was released for
    # re-solve instead of staying bound to a dead bundle.
    REASSIGNED_AFTER_ASSET_LOSS = "reassigned-after-asset-loss"
    # A derived service task was withdrawn because newer readings contradict
    # the prognosis (false positive).
    SERVICE_WITHDRAWN = "service-withdrawn"
    # A derived service task was escalated because the asset degraded faster
    # than forecast (false negative).
    SERVICE_ESCALATED = "service-escalated"


class QualitySeverity(str, Enum):
    """Severity of a quality finding."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

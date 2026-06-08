"""Enumerations for the canonical, solver-neutral planning model."""

from enum import Enum


class PlanningMode(str, Enum):
    """Planning mode of a snapshot or plan."""

    PERIODIC = "periodic"
    ROLLING = "rolling"


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


class QualitySeverity(str, Enum):
    """Severity of a quality finding."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

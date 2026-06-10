"""Shared value objects used across the canonical model."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from fl_op.canonical.enums import QualitySeverity


class TimeInterval(BaseModel):
    """A closed-open real-world time interval."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: Optional[datetime] = None


class GeoPoint(BaseModel):
    """A WGS-84 coordinate."""

    model_config = ConfigDict(frozen=True)

    lat: float
    lon: float


class VersionDimensions(BaseModel):
    """Governance version dimensions stamped onto snapshots and plans."""

    model_config = ConfigDict(frozen=True)

    contract_versions: dict[str, str] = Field(default_factory=dict)
    avro_schema_versions: dict[str, str] = Field(default_factory=dict)
    mapping_versions: dict[str, str] = Field(default_factory=dict)
    quality_policy_versions: dict[str, str] = Field(default_factory=dict)
    optimization_profile_version: str = ""
    adapter_compatibility_version: str = ""
    integer_scaling_policy_version: str = ""


class QualityFinding(BaseModel):
    """A single data-quality finding produced during mapping/snapshot build."""

    model_config = ConfigDict(frozen=True)

    quality_finding_id: str
    rule_id: str
    severity: QualitySeverity
    entity_ref: str
    field_ref: Optional[str] = None
    detected_at: datetime
    action_applied: str
    original_value: Optional[Any] = None
    normalized_value: Optional[Any] = None
    planning_impact: str = ""
    source_ref: str = ""


class QualitySummary(BaseModel):
    """Aggregate quality picture attached to a snapshot or plan."""

    model_config = ConfigDict(frozen=True)

    n_findings: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)
    n_entities_excluded: int = 0
    n_imputed: int = 0
    # Source contract id -> share of bad observation readings (outliers plus
    # fault-suspected series), from the statistical assessment.
    observation_error_rates: dict[str, float] = Field(default_factory=dict)


class RiskSummary(BaseModel):
    """Aggregate risk picture attached to a plan."""

    model_config = ConfigDict(frozen=True)

    n_contract_deadlines_at_risk: int = 0
    n_weather_restricted_tasks: int = 0
    total_penalty_exposure_eur: float = 0.0

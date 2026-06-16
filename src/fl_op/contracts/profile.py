"""OptimizationProfile loader and validator.

The profile is a declarative document describing input contracts, bundle
generation roles, constraints, and a lexicographic objective hierarchy. It is
validated structurally here; adapter capability validation (whether a given
adapter supports every enforced constraint) is performed in the adapters layer.
"""

import logging
import pathlib
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from fl_op.core.constants import (
    BATTERY_CRITICAL_THRESHOLD_PCT,
    BATTERY_FORECAST_HORIZON_DAYS,
    BATTERY_LOW_THRESHOLD_PCT,
    COMPOSITE_BATTERY_HEADROOM_PCT,
    COMPOSITE_HEALTH_THRESHOLD,
    COMPOSITE_SERVICE_HEADROOM_DAYS,
    COMPOSITE_WEIGHT_BATTERY,
    COMPOSITE_WEIGHT_DRIFT,
    COMPOSITE_WEIGHT_HEALTH,
    COMPOSITE_WEIGHT_SERVICE,
    EQUIPMENT_SERVICE_OPERATION,
    GLOBAL_ASSIGNMENT_COUNT_PRIORITY,
    MIN_OBSERVATION_CONFIDENCE,
    MONITOR_MOBILE_ASSETS,
    SERVICE_TASK_DEADLINE_DAYS,
    SERVICE_TASK_DURATION_MINUTES,
    SERVICE_TASK_ESCALATED_DEADLINE_DAYS,
    SERVICE_TASK_ESCALATED_PRIORITY_CLASS,
    SERVICE_TASK_NOMINAL_AREA_HA,
    SERVICE_TASK_PENALTY_EUR_PER_DAY,
    SERVICE_TASK_PRIORITY_CLASS,
    WEATHER_RAIN_MAX_MM,
    WEATHER_SOIL_MOISTURE_MAX_PCT,
    WEATHER_WIND_MAX_MS,
    XOPT_API_VERSION,
)

logger = logging.getLogger(__name__)


class ProfileMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    version: str
    semanticModelRef: str


class PlanningModeBinding(BaseModel):
    id: str
    adapter: str


class ConstraintSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    severity: str
    enforced: bool = False


class ObjectiveSpec(BaseModel):
    mode: str
    priorities: list[str]


class MonitoringPolicyOverride(BaseModel):
    """Partial monitoring policy for one asset type; unset fields inherit."""

    model_config = ConfigDict(extra="allow")

    batteryLowThresholdPct: Optional[float] = None
    batteryCriticalThresholdPct: Optional[float] = None
    batteryForecastHorizonDays: Optional[float] = None
    minObservationConfidence: Optional[float] = None
    compositeHealthThreshold: Optional[float] = None
    compositeWeightBattery: Optional[float] = None
    compositeWeightHealth: Optional[float] = None
    compositeWeightService: Optional[float] = None
    compositeWeightDrift: Optional[float] = None
    compositeBatteryHeadroomPct: Optional[float] = None
    compositeServiceHeadroomDays: Optional[float] = None
    serviceOperationType: Optional[str] = None
    servicePriorityClass: Optional[int] = None
    serviceDeadlineDays: Optional[int] = None
    servicePenaltyPerDayEur: Optional[float] = None
    serviceNominalAreaHa: Optional[float] = None
    serviceDurationMinutes: Optional[float] = None
    escalatedPriorityClass: Optional[int] = None
    escalatedDeadlineDays: Optional[int] = None
    monitorMobileAssets: Optional[bool] = None


class MonitoringPolicySpec(BaseModel):
    """Stationary-equipment monitoring policy carried by the profile.

    Defaults are the engine-wide constants; a domain profile overrides them in
    its ``monitoring`` section, and per-asset-type overrides
    (``assetTypeOverrides``) layer on top for individual station classes.
    """

    model_config = ConfigDict(extra="allow")

    batteryLowThresholdPct: float = BATTERY_LOW_THRESHOLD_PCT
    batteryCriticalThresholdPct: float = BATTERY_CRITICAL_THRESHOLD_PCT
    batteryForecastHorizonDays: float = BATTERY_FORECAST_HORIZON_DAYS
    minObservationConfidence: float = MIN_OBSERVATION_CONFIDENCE
    compositeHealthThreshold: float = COMPOSITE_HEALTH_THRESHOLD
    # Composite-score signal weights and headrooms (engine-constant defaults).
    compositeWeightBattery: float = COMPOSITE_WEIGHT_BATTERY
    compositeWeightHealth: float = COMPOSITE_WEIGHT_HEALTH
    compositeWeightService: float = COMPOSITE_WEIGHT_SERVICE
    compositeWeightDrift: float = COMPOSITE_WEIGHT_DRIFT
    compositeBatteryHeadroomPct: float = COMPOSITE_BATTERY_HEADROOM_PCT
    compositeServiceHeadroomDays: float = COMPOSITE_SERVICE_HEADROOM_DAYS
    serviceOperationType: str = EQUIPMENT_SERVICE_OPERATION
    servicePriorityClass: int = SERVICE_TASK_PRIORITY_CLASS
    serviceDeadlineDays: int = SERVICE_TASK_DEADLINE_DAYS
    servicePenaltyPerDayEur: float = SERVICE_TASK_PENALTY_EUR_PER_DAY
    serviceNominalAreaHa: float = SERVICE_TASK_NOMINAL_AREA_HA
    serviceDurationMinutes: float = SERVICE_TASK_DURATION_MINUTES
    escalatedPriorityClass: int = SERVICE_TASK_ESCALATED_PRIORITY_CLASS
    escalatedDeadlineDays: int = SERVICE_TASK_ESCALATED_DEADLINE_DAYS
    # Whether predictive monitoring also covers mobile assets (prime movers,
    # drones); stationary equipment is always monitored. Tunable per asset type.
    monitorMobileAssets: bool = MONITOR_MOBILE_ASSETS
    assetTypeOverrides: dict[str, MonitoringPolicyOverride] = Field(default_factory=dict)
    # Instance-level overrides keyed by asset id (a single critical station),
    # layered on top of the per-asset-type overrides.
    assetOverrides: dict[str, MonitoringPolicyOverride] = Field(default_factory=dict)

    def for_asset_type(self, asset_type: str) -> "MonitoringPolicySpec":
        """Effective policy for one asset type: base merged with its override."""
        return self._merged(self.assetTypeOverrides.get(asset_type))

    def for_asset(self, asset_type: str, asset_id: str) -> "MonitoringPolicySpec":
        """Effective policy for one asset instance.

        Layering order: base policy, then the asset-type override, then the
        instance override, so a single critical station can tighten (or relax)
        its class defaults without redefining them.
        """
        return self.for_asset_type(asset_type)._merged(
            self.assetOverrides.get(asset_id)
        )

    def _merged(
        self, override: Optional[MonitoringPolicyOverride]
    ) -> "MonitoringPolicySpec":
        if override is None:
            return self
        updates = {
            name: value
            for name, value in override.model_dump().items()
            if value is not None
        }
        return self.model_copy(update=updates)

    def composed_with(
        self, other: "MonitoringPolicySpec"
    ) -> "MonitoringPolicySpec":
        """Layer another domain's monitoring policy onto this primary one.

        Scalar thresholds keep this (primary) profile's values; the per-asset-type
        and per-asset override maps union, this profile winning on the rare key
        collision. Asset-type classes are domain-disjoint in practice, so a shared
        fleet inherits each pack's station-class tuning without either clobbering
        the other.
        """
        asset_type_overrides = {
            **other.assetTypeOverrides,
            **self.assetTypeOverrides,
        }
        asset_overrides = {**other.assetOverrides, **self.assetOverrides}
        return self.model_copy(
            update={
                "assetTypeOverrides": asset_type_overrides,
                "assetOverrides": asset_overrides,
            }
        )


class WeatherPolicySpec(BaseModel):
    """Weather-window limits and per-operation sensitivity.

    ``sensitivity`` names the weather dimensions (wind, rain, soil-moisture)
    each operation type cares about; operations without an entry are never
    weather-blocked. Limits default to the engine-wide safety constants.
    """

    model_config = ConfigDict(extra="allow")

    maxWindMs: float = WEATHER_WIND_MAX_MS
    maxRainMmPerH: float = WEATHER_RAIN_MAX_MM
    maxSoilMoisturePct: float = WEATHER_SOIL_MOISTURE_MAX_PCT
    sensitivity: dict[str, list[str]] = Field(default_factory=dict)
    requireForecastCoverage: bool = False

    def composed_with(self, other: "WeatherPolicySpec") -> "WeatherPolicySpec":
        """Layer another domain's weather policy onto this primary one.

        Limits take the stricter (lower) bound of either pack so a shared
        weather window never violates the more cautious domain. Per-operation
        sensitivity maps union, this profile winning on any shared operation
        type (operation types are domain-disjoint in practice). Conservative
        forecast coverage is OR-ed: if either pack demands it, the shared
        policy demands it (the safer stance wins).
        """
        sensitivity = {**other.sensitivity, **self.sensitivity}
        return self.model_copy(
            update={
                "maxWindMs": min(self.maxWindMs, other.maxWindMs),
                "maxRainMmPerH": min(self.maxRainMmPerH, other.maxRainMmPerH),
                "maxSoilMoisturePct": min(
                    self.maxSoilMoisturePct, other.maxSoilMoisturePct
                ),
                "sensitivity": sensitivity,
                "requireForecastCoverage": (
                    self.requireForecastCoverage or other.requireForecastCoverage
                ),
            }
        )


class MaterialDemandSpec(BaseModel):
    """Consumable demand an operation type places on depot inventory."""

    model_config = ConfigDict(extra="allow")

    material: str
    perAreaHa: float


class AllocationPolicySpec(BaseModel):
    """Pre-allocation objective tuning.

    ``countPriority`` blends the global assignment objective between
    allocating as many bundles as the limits admit (1.0, the engine default:
    scores only break ties) and maximizing summed candidate scores regardless
    of allocation count (0.0): a domain preferring fewer, higher-margin
    allocations lowers it.
    """

    model_config = ConfigDict(extra="allow")

    countPriority: float = GLOBAL_ASSIGNMENT_COUNT_PRIORITY


class PlanningDefaults(BaseModel):
    model_config = ConfigDict(extra="allow")

    periodicHorizonDays: Optional[int] = None
    rollingHorizonHours: Optional[int] = None
    freezeWindowMinutes: Optional[int] = None
    maxAssignmentRoutingIterations: Optional[int] = None


class OptimizationProfile(BaseModel):
    """Top-level OptimizationProfile document."""

    model_config = ConfigDict(extra="allow")

    apiVersion: str
    kind: str
    metadata: ProfileMetadata
    inputContracts: list[str] = Field(default_factory=list)
    planningModes: list[PlanningModeBinding] = Field(default_factory=list)
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    objectives: Optional[ObjectiveSpec] = None
    planningDefaults: Optional[PlanningDefaults] = None
    monitoring: MonitoringPolicySpec = Field(default_factory=MonitoringPolicySpec)
    weatherPolicy: WeatherPolicySpec = Field(default_factory=WeatherPolicySpec)
    materialDemand: dict[str, MaterialDemandSpec] = Field(default_factory=dict)
    allocationPolicy: AllocationPolicySpec = Field(default_factory=AllocationPolicySpec)
    outputContracts: list[str] = Field(default_factory=list)

    def adapter_for_mode(self, mode: str) -> Optional[str]:
        for pm in self.planningModes:
            if pm.id == mode:
                return pm.adapter
        return None

    def enforced_constraints(self) -> list[str]:
        return [c.id for c in self.constraints if c.enforced]

    def composed_with(self, other: "OptimizationProfile") -> "OptimizationProfile":
        """Compose another domain's profile onto this primary one.

        Used when a shared-fleet snapshot spans multiple active domains: this
        profile (the first selected domain) supplies the base identity, scalar
        defaults, and objective hierarchy, while the secondary profile's
        domain-specific policy detail is layered in. Merge rules per section:

        * ``inputContracts`` / ``outputContracts`` / ``planningModes``: union,
          preserving this profile's order then appending unseen entries.
        * ``constraints``: union by id, with ``enforced=True`` winning a
          conflict so no domain's hard constraint is silently relaxed.
        * ``monitoring`` / ``weatherPolicy``: delegated to the policy specs'
          own ``composed_with`` (stricter limits, unioned overrides).
        * ``materialDemand``: union, this profile winning on shared keys.
        * ``allocationPolicy``: keeps this profile's tuning.

        The merge is associative left-to-right, so folding a domain list yields a
        deterministic composite independent of how the fold is bracketed.
        """
        input_contracts = _union_preserving_order(
            self.inputContracts, other.inputContracts
        )
        output_contracts = _union_preserving_order(
            self.outputContracts, other.outputContracts
        )
        planning_modes = list(self.planningModes)
        seen_modes = {pm.id for pm in planning_modes}
        for pm in other.planningModes:
            if pm.id not in seen_modes:
                planning_modes.append(pm)
                seen_modes.add(pm.id)
        constraints = _merge_constraints(self.constraints, other.constraints)
        material_demand = {**other.materialDemand, **self.materialDemand}
        return self.model_copy(
            update={
                "inputContracts": input_contracts,
                "outputContracts": output_contracts,
                "planningModes": planning_modes,
                "constraints": constraints,
                "monitoring": self.monitoring.composed_with(other.monitoring),
                "weatherPolicy": self.weatherPolicy.composed_with(
                    other.weatherPolicy
                ),
                "materialDemand": material_demand,
            }
        )


def _union_preserving_order(primary: list[str], secondary: list[str]) -> list[str]:
    merged = list(primary)
    seen = set(primary)
    for item in secondary:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


def _merge_constraints(
    primary: list[ConstraintSpec], secondary: list[ConstraintSpec]
) -> list[ConstraintSpec]:
    """Union constraints by id; an enforced constraint wins a conflict."""
    by_id: dict[str, ConstraintSpec] = {}
    order: list[str] = []
    for spec in [*primary, *secondary]:
        existing = by_id.get(spec.id)
        if existing is None:
            by_id[spec.id] = spec
            order.append(spec.id)
        elif spec.enforced and not existing.enforced:
            by_id[spec.id] = spec
    return [by_id[cid] for cid in order]


def load_profile(path: pathlib.Path) -> OptimizationProfile:
    """Load and validate an OptimizationProfile YAML document."""
    doc = yaml.safe_load(path.read_text())
    profile = OptimizationProfile.model_validate(doc)
    if profile.kind != "OptimizationProfile":
        raise ValueError(f"Profile {path} has unexpected kind '{profile.kind}'")
    if profile.apiVersion != XOPT_API_VERSION:
        logger.warning(
            "Profile %s apiVersion '%s' differs from expected '%s'",
            path,
            profile.apiVersion,
            XOPT_API_VERSION,
        )
    return profile

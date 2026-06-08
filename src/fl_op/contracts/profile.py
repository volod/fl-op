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

from fl_op.core.constants import XOPT_API_VERSION

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
    outputContracts: list[str] = Field(default_factory=list)

    def adapter_for_mode(self, mode: str) -> Optional[str]:
        for pm in self.planningModes:
            if pm.id == mode:
                return pm.adapter
        return None

    def enforced_constraints(self) -> list[str]:
        return [c.id for c in self.constraints if c.enforced]


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

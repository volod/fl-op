"""Canonical Asset and Capability abstractions."""

from datetime import datetime
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from fl_op.canonical.common import TimeInterval


class Capability(BaseModel):
    """A measurable or categorical ability of an asset."""

    model_config = ConfigDict(frozen=True)

    capability_id: str
    semantic_term: str
    value: Union[float, int, str, bool, list[Any], dict[str, Any], None]
    canonical_unit: Optional[str] = None
    confidence: Optional[float] = None
    source_ref: str = ""


class Asset(BaseModel):
    """A physical or logical resource that may participate in task execution.

    Source vocabulary (vehicle, implement, operator, depot) is mapped onto this
    single abstraction with distinct `roles`.
    """

    model_config = ConfigDict(frozen=True)

    asset_id: str
    asset_type: str
    roles: list[str] = Field(default_factory=list)
    status: str = "available"
    capabilities: list[Capability] = Field(default_factory=list)
    location: Optional["GeoLocation"] = None
    home_depot_ref: Optional[str] = None
    availability: list[TimeInterval] = Field(default_factory=list)
    name: str = ""
    source_ref: str = ""
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None

    def capability(self, semantic_term: str) -> Optional[Capability]:
        for cap in self.capabilities:
            if cap.semantic_term == semantic_term:
                return cap
        return None

    def capability_value(self, semantic_term: str) -> Any:
        cap = self.capability(semantic_term)
        return cap.value if cap is not None else None


class GeoLocation(BaseModel):
    """An asset's current location reference."""

    model_config = ConfigDict(frozen=True)

    lat: float
    lon: float
    location_ref: Optional[str] = None


Asset.model_rebuild()

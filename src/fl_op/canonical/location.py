"""Canonical Location abstraction.

Field parcels and depots are both mapped onto Location. Depot material balances
are represented separately as InventoryPosition records on the snapshot.
"""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from fl_op.canonical.common import TimeInterval


class Location(BaseModel):
    """A point of interest: field-entry point, depot, or loading station."""

    model_config = ConfigDict(frozen=True)

    location_id: str
    location_type: str  # field | depot | loading-station
    lat: float
    lon: float
    name: str = ""
    area_ha: Optional[float] = None
    soil_type: str = ""
    polygon: list[list[float]] = Field(default_factory=list)
    # Operation types prohibited at this location (restricted zone).
    restricted_operations: list[str] = Field(default_factory=list)
    # Intervals during which no execution may start here (time-restricted area).
    restriction_windows: list[TimeInterval] = Field(default_factory=list)
    # Recharge/refuel station capacity (depots/hubs): aggregate charger power and
    # the number of parallel charging bays (charging queue capacity). Absent for
    # non-station locations.
    charging_power_kw: Optional[float] = None
    charging_slots: Optional[int] = None
    source_ref: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

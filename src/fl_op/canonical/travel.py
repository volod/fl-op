"""Canonical TravelLink abstraction.

One directed travel-network edge between two locations (distance-matrix entry
or road-graph arc). The network may be sparse: location pairs without a link
fall back to haversine distance and asset travel speed.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TravelLink(BaseModel):
    """A directed travel edge with a measured or modelled travel time."""

    model_config = ConfigDict(frozen=True)

    link_id: str
    from_location_ref: str
    to_location_ref: str
    travel_time_s: float
    distance_km: Optional[float] = None
    network_mode: str = "any"
    # Ordered [lat, lon] vertices of the travelled network path. Optional for
    # distance-matrix sources that carry measures but no spatial geometry.
    route_geometry: list[list[float]] = Field(default_factory=list)
    # Directed toll charged to traverse the edge (EUR); 0 means untolled.
    toll_eur: float = 0.0
    source_ref: str = ""

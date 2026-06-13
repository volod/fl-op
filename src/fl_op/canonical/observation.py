"""Canonical Observation abstraction.

An observation is a measured value reported about an entity: a sensor reading,
a telemetry sample, or a manual inspection result. Historical batches and
realtime streamed readings share this one shape; the engine's monitoring policy
consumes the latest observation per (entity, metric) pair.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class Observation(BaseModel):
    """A single measured value about an asset or location at a point in time."""

    model_config = ConfigDict(frozen=True)

    observation_id: str
    entity_ref: str
    metric: str
    value: Optional[float] = None
    state_value: str = ""
    unit: Optional[str] = None
    observed_at: Optional[datetime] = None
    # When the reading arrived at the platform; makes arrival order exact
    # across restarts (None falls back to source row order).
    ingested_at: Optional[datetime] = None
    # Source-declared quality marker (ok, suspect, bad); the statistical
    # assessment converts it into a confidence factor.
    quality_flag: str = ""
    confidence: Optional[float] = None
    source_ref: str = ""
    # Windowed-downsampling aggregates: when this reading represents a whole
    # downsampling window, the window's extremes and mean travel with it so
    # spiky metrics keep their extremes after aggregation. None on readings
    # that were never aggregated.
    window_min: Optional[float] = None
    window_mean: Optional[float] = None
    window_max: Optional[float] = None
    window_n: Optional[int] = None

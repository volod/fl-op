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
    # Source-declared quality marker (ok, suspect, bad); the statistical
    # assessment converts it into a confidence factor.
    quality_flag: str = ""
    confidence: Optional[float] = None
    source_ref: str = ""

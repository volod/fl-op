"""Canonical Forecast abstraction."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from fl_op.canonical.common import TimeInterval


class Forecast(BaseModel):
    """A weather (or other) forecast for a location and time interval."""

    model_config = ConfigDict(frozen=True)

    forecast_id: str
    forecast_type: str = "weather"
    location: Optional[dict[str, float]] = None
    issued_at: Optional[datetime] = None
    forecast_for: Optional[TimeInterval] = None
    value: dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = None
    source_ref: str = ""

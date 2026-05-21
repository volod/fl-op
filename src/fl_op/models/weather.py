from datetime import datetime

from pydantic import BaseModel, Field

from fl_op.core.constants import (
    WEATHER_RAIN_MAX_MM,
    WEATHER_SOIL_MOISTURE_MAX_PCT,
    WEATHER_WIND_MAX_MS,
)


class WeatherWindow(BaseModel):
    """A time window during which weather conditions are either safe or unsafe."""

    window_id: str
    valid_from: datetime
    valid_to: datetime
    wind_ms: float = Field(ge=0)
    rain_mm_per_h: float = Field(ge=0)
    soil_moisture_pct: float = Field(ge=0, le=100)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)

    @property
    def is_operable(self) -> bool:
        """True when all weather thresholds are within safe operating limits."""
        return (
            self.wind_ms <= WEATHER_WIND_MAX_MS
            and self.rain_mm_per_h <= WEATHER_RAIN_MAX_MM
            and self.soil_moisture_pct <= WEATHER_SOIL_MOISTURE_MAX_PCT
        )

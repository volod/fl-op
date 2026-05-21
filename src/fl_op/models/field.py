from pydantic import BaseModel, Field, field_validator


class Field_(BaseModel):
    """Agricultural field definition. Named Field_ to avoid shadowing pydantic.Field."""

    field_id: str
    name: str
    area_ha: float = Field(gt=0, le=50000)
    # Polygon vertices as list of [lat, lon] pairs
    polygon: list[list[float]] = Field(default_factory=list)
    centroid_lat: float = Field(ge=-90.0, le=90.0)
    centroid_lon: float = Field(ge=-180.0, le=180.0)
    soil_type: str = ""

    @field_validator("polygon")
    @classmethod
    def polygon_vertex_limit(cls, v: list[list[float]]) -> list[list[float]]:
        if len(v) > 1000:
            raise ValueError("polygon vertex count must not exceed 1000")
        return v

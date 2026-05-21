from pydantic import BaseModel, Field


class Depot(BaseModel):
    depot_id: str
    name: str
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    fuel_available_l: float = Field(ge=0)
    fertilizer_available_kg: float = Field(ge=0)

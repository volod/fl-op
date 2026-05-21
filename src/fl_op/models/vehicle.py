from pydantic import BaseModel, Field

from fl_op.models.enums import VehicleType


class Vehicle(BaseModel):
    vehicle_id: str
    vehicle_type: VehicleType
    # Rated continuous power output in kilowatts
    rated_power_kw: float = Field(gt=0)
    fuel_tank_l: float = Field(gt=0)
    fuel_consumption_l_per_h: float = Field(gt=0)
    # Current location at schedule start
    current_lat: float = Field(ge=-90.0, le=90.0)
    current_lon: float = Field(ge=-180.0, le=180.0)
    depot_id: str
    # Average field travel speed in km/h
    travel_speed_kmh: float = Field(gt=0, default=15.0)

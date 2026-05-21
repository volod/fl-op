import ast
from typing import Any

from pydantic import BaseModel, Field, field_validator

from fl_op.models.enums import ImplementType, OperationType


class Implement(BaseModel):
    implement_id: str
    implement_type: ImplementType
    compatible_operations: list[OperationType]
    # Continuous power draw in kilowatts (checked against vehicle rated_power_kw + margin)
    required_power_kw: float = Field(gt=0)
    # Working width in metres, used for area-rate estimation
    working_width_m: float = Field(gt=0)
    # Operating speed range in km/h
    min_speed_kmh: float = Field(gt=0)
    max_speed_kmh: float = Field(gt=0)
    # Capacity for fertilizer applicators (kg); 0 for non-fertilizer implements
    fertilizer_capacity_kg: float = Field(ge=0, default=0.0)
    depot_id: str

    @field_validator("compatible_operations", mode="before")
    @classmethod
    def parse_operations_list(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                return ast.literal_eval(v)
            except (ValueError, SyntaxError):
                return [v]
        return v

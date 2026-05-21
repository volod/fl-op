from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from fl_op.models.order import Order

_MAX_CONTRACT_YEARS = 5


class Contract(BaseModel):
    contract_id: str
    client_name: str
    start_date: datetime
    end_date: datetime
    # Total contracted value in EUR
    total_value_eur: float = Field(ge=0)
    # General penalty per day of delay (may be overridden per order)
    default_penalty_per_day_eur: float = Field(ge=0)
    orders: list[Order] = Field(default_factory=list)

    @field_validator("end_date")
    @classmethod
    def validate_date_range(cls, v: datetime, info: object) -> datetime:
        data = getattr(info, "data", {})
        start = data.get("start_date")
        if start is not None:
            delta_years = (v - start).days / 365.25
            if delta_years > _MAX_CONTRACT_YEARS:
                raise ValueError(
                    f"Contract span {delta_years:.1f} yr exceeds maximum {_MAX_CONTRACT_YEARS} yr"
                )
            if v <= start:
                raise ValueError("end_date must be after start_date")
        return v

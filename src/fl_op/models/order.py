from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from fl_op.models.enums import OperationType, OrderStatus

if TYPE_CHECKING:
    from fl_op.models.contract import Contract


class Order(BaseModel):
    order_id: str
    contract_id: str
    field_id: str
    operation_type: OperationType
    area_ha: float = Field(gt=0)
    # ISO-8601 deadline; hard time-window constraint for OR-Tools
    deadline: datetime
    # Penalty incurred per calendar day the deadline is missed
    penalty_per_day_eur: float = Field(ge=0)
    priority: int = Field(ge=1, le=10, default=5)
    status: OrderStatus = OrderStatus.PENDING
    # Estimated yield revenue if operation is completed on time
    estimated_revenue_eur: float = Field(ge=0, default=0.0)
    # Forward reference resolved by model_rebuild() in models/__init__.py
    contract: Contract | None = None

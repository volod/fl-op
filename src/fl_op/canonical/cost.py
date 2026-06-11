"""Canonical CostRate abstraction.

A priced resource rate (fuel, consumable material) with an optional validity
window. Engine cost constants remain the fallback for rate types no mapped
source prices.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CostRate(BaseModel):
    """Price of one unit of a canonical resource, valid in a time window."""

    model_config = ConfigDict(frozen=True)

    cost_rate_id: str
    # Canonical resource code the engine interprets ("fuel", "fertilizer").
    rate_type: str
    unit_price_eur: float
    # Unit one unit-price buys (L, kg, ...).
    per_unit: str = ""
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    source_ref: str = ""

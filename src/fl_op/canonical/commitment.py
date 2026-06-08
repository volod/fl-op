"""Canonical Commitment and InventoryPosition abstractions."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from fl_op.canonical.enums import CommitmentHardness


class Commitment(BaseModel):
    """A contractual obligation attached to a task."""

    model_config = ConfigDict(frozen=True)

    commitment_id: str
    contract_id: str
    task_id: Optional[str] = None
    commitment_type: str
    hardness: CommitmentHardness = CommitmentHardness.MEDIUM
    value: dict[str, Any] = Field(default_factory=dict)
    penalty_rule_ref: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


class InventoryPosition(BaseModel):
    """Available and reserved material at a location."""

    model_config = ConfigDict(frozen=True)

    inventory_location_ref: str
    material_type: str
    available_quantity: float
    canonical_unit: str
    reserved_quantity: float = 0.0

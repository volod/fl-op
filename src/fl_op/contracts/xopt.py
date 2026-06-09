"""Pydantic models for the canonical field-binding metadata.

A field binding ties one physical source column to a canonical attribute path
(plus its semantic term, unit, and missing-value policy). These are parsed from
the per-domain mapping documents by fl_op.contracts.mapping_loader and consumed
by the mapping engine. Models are permissive: unknown keys are preserved so that
schema evolution does not silently drop metadata.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MissingValuePolicy(str, Enum):
    """Action taken when a bound source field is missing or unparseable."""

    REJECT_FOR_PLANNING = "reject-for-planning"
    ACCEPT_WITH_WARNING = "accept-with-warning"
    ACCEPT_WITH_PENALTY = "accept-with-penalty"
    FALLBACK_TO_CONSERVATIVE_VALUE = "fallback-to-conservative-value"
    IMPUTE = "impute"
    QUARANTINE = "quarantine"
    MANUAL_REVIEW = "manual-review"


class XOptFieldMeta(BaseModel):
    """Field-level x-optimization binding."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    extension_version: str = Field(alias="extensionVersion")
    semantic_term: str = Field(alias="semanticTerm")
    binding: str
    canonical_unit: Optional[str] = Field(default=None, alias="canonicalUnit")
    quantity_kind: Optional[str] = Field(default=None, alias="quantityKind")
    planning_use: list[str] = Field(default_factory=list, alias="planningUse")
    quality_policy_ref: Optional[str] = Field(default=None, alias="qualityPolicyRef")
    missing_value_policy: Optional[MissingValuePolicy] = Field(
        default=None, alias="missingValuePolicy"
    )


class FieldBinding(BaseModel):
    """A resolved binding from one source field to a canonical attribute path.

    Produced by the loaders and consumed by the mapping engine. `source_field`
    is the physical column/key name; `meta` carries the declarative semantics.
    """

    model_config = ConfigDict(frozen=True)

    source_field: str
    meta: XOptFieldMeta

    @property
    def binding(self) -> str:
        return self.meta.binding

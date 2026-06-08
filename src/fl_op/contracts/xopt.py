"""Pydantic models for the x-optimization extension objects.

These describe the metadata embedded in Avro schemas (record- and field-level)
and in ODCS contracts (via customProperties). They are intentionally permissive:
unknown keys are preserved so that schema evolution does not silently drop
metadata, while the typed fields document the binding contract used by the
mapping engine.
"""

from enum import Enum
from typing import Any, Optional

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


class XOptRecordMeta(BaseModel):
    """Record-level x-optimization metadata."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    extension_version: str = Field(alias="extensionVersion")
    semantic_entity: str = Field(alias="semanticEntity")
    data_product_role: Optional[str] = Field(default=None, alias="dataProductRole")
    asset_role: Optional[str] = Field(default=None, alias="assetRole")
    entity_key_field: Optional[str] = Field(default=None, alias="entityKeyField")
    event_time_field: Optional[str] = Field(default=None, alias="eventTimeField")
    valid_from_field: Optional[str] = Field(default=None, alias="validFromField")
    valid_to_field: Optional[str] = Field(default=None, alias="validToField")


class XOptContractProfile(BaseModel):
    """Root-level ODCS xOptimization contract profile."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    extension_version: str = Field(alias="extensionVersion")
    semantic_model_ref: str = Field(alias="semanticModelRef")
    data_product_role: Optional[str] = Field(default=None, alias="dataProductRole")
    asset_role: Optional[str] = Field(default=None, alias="assetRole")
    avro_schema_ref: Optional[str] = Field(default=None, alias="avroSchemaRef")
    mapping_version: Optional[str] = Field(default=None, alias="mappingVersion")
    permitted_planning_uses: list[str] = Field(
        default_factory=list, alias="permittedPlanningUses"
    )
    migration_policy_ref: Optional[str] = Field(default=None, alias="migrationPolicyRef")
    default_quality_policy_ref: Optional[str] = Field(
        default=None, alias="defaultQualityPolicyRef"
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


def extract_xopt_block(node: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return the raw x-optimization dict from an Avro record/field node, if present."""
    block = node.get("x-optimization")
    return block if isinstance(block, dict) else None

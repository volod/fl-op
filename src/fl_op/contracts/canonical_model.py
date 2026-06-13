"""Loader for the canonical optimization-model contracts.

The canonical model is the single source of truth for what the optimization engine
consumes. It is declared by:

  contracts/canonical/model.yaml            - semantic-term vocabulary + entity index
  contracts/canonical/odcs/<entity>.odcs.yaml - per-entity ODCS DataContract

Each canonical field carries a ``canonicalBinding`` custom property
(``binding`` + ``semanticTerm`` + optional unit/quantityKind/planningUse). This
loader flattens those into a queryable ``CanonicalModel`` used by validation to
check that a domain maps completely onto the canonical contract.
"""

import logging
import pathlib
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from fl_op.core.constants import (
    CANONICAL_BINDING_PROPERTY,
    CANONICAL_ENTITY_PROPERTY,
    CANONICAL_MODEL_FILENAME,
)
from fl_op.core.paths import CANONICAL_ROOT

logger = logging.getLogger(__name__)


class SemanticTerm(BaseModel):
    """A controlled-vocabulary meaning a mapping may bind a physical field to."""

    model_config = ConfigDict(extra="allow")

    term: str
    value_type: Optional[str] = None
    quantity_kind: Optional[str] = None
    canonical_unit: Optional[str] = None


class CanonicalField(BaseModel):
    """A single canonical field declared by an entity contract."""

    model_config = ConfigDict(frozen=True)

    entity: str
    name: str
    binding: str
    semantic_term: str
    required: bool = False
    canonical_unit: Optional[str] = None
    quantity_kind: Optional[str] = None
    planning_use: list[str] = Field(default_factory=list)


class CanonicalModel:
    """Parsed canonical optimization model: entities, fields, and vocabulary."""

    def __init__(
        self,
        model_ref: str,
        semantic_terms: dict[str, SemanticTerm],
        fields: list[CanonicalField],
    ) -> None:
        self.model_ref = model_ref
        self.semantic_terms = semantic_terms
        self.fields = fields
        self._by_entity: dict[str, list[CanonicalField]] = {}
        for fld in fields:
            self._by_entity.setdefault(fld.entity, []).append(fld)

    def entities(self) -> list[str]:
        return list(self._by_entity)

    def fields_for(self, entity: str) -> list[CanonicalField]:
        return self._by_entity.get(entity, [])

    def allowed_bindings(self, entity: str) -> set[str]:
        return {f.binding for f in self.fields_for(entity)}

    def required_bindings(self, entity: str) -> set[str]:
        return {f.binding for f in self.fields_for(entity) if f.required}

    def field_by_binding(self, entity: str, binding: str) -> Optional[CanonicalField]:
        for f in self.fields_for(entity):
            if f.binding == binding:
                return f
        return None

    def has_term(self, term: str) -> bool:
        return term in self.semantic_terms


def _find_custom_value(custom_properties: Any, property_name: str) -> Optional[dict[str, Any]]:
    """Return the value dict of a named ODCS customProperty, if present."""
    if not isinstance(custom_properties, list):
        return None
    for cp in custom_properties:
        if isinstance(cp, dict) and cp.get("property") == property_name:
            value = cp.get("value")
            if isinstance(value, dict):
                return value
    return None


def _load_entity_fields(entity: str, doc: dict[str, Any]) -> list[CanonicalField]:
    fields: list[CanonicalField] = []
    for schema_obj in doc.get("schema", []):
        if not isinstance(schema_obj, dict):
            continue
        for prop in schema_obj.get("properties", []):
            if not isinstance(prop, dict):
                continue
            binding_value = _find_custom_value(
                prop.get("customProperties"), CANONICAL_BINDING_PROPERTY
            )
            if binding_value is None:
                continue
            fields.append(
                CanonicalField(
                    entity=entity,
                    name=prop["name"],
                    binding=binding_value["binding"],
                    semantic_term=binding_value["semanticTerm"],
                    required=bool(prop.get("required", False)),
                    canonical_unit=binding_value.get("canonicalUnit"),
                    quantity_kind=binding_value.get("quantityKind"),
                    planning_use=list(binding_value.get("planningUse") or []),
                )
            )
    return fields


def _entity_of(doc: dict[str, Any]) -> Optional[str]:
    value = _find_custom_value(doc.get("customProperties"), CANONICAL_ENTITY_PROPERTY)
    return value.get("entity") if value else None


def load_canonical_model(root: Optional[pathlib.Path] = None) -> CanonicalModel:
    """Load and parse the canonical optimization model from its contract root."""
    root = root or CANONICAL_ROOT
    index_path = root / CANONICAL_MODEL_FILENAME
    if not index_path.exists():
        raise FileNotFoundError(f"Canonical model index not found: {index_path}")

    index: dict[str, Any] = yaml.safe_load(index_path.read_text())
    model_ref = (index.get("metadata") or {}).get("canonicalModelRef", "")

    semantic_terms: dict[str, SemanticTerm] = {}
    for term, spec in (index.get("semanticTerms") or {}).items():
        spec = spec or {}
        semantic_terms[term] = SemanticTerm(
            term=term,
            value_type=spec.get("valueType"),
            quantity_kind=spec.get("quantityKind"),
            canonical_unit=spec.get("canonicalUnit"),
        )

    fields: list[CanonicalField] = []
    for entity, entry in (index.get("entities") or {}).items():
        contract_rel = (entry or {}).get("contract")
        if not contract_rel:
            continue
        contract_path = root / contract_rel
        doc = yaml.safe_load(contract_path.read_text())
        declared_entity = _entity_of(doc) or entity
        fields.extend(_load_entity_fields(declared_entity, doc))

    logger.debug(
        "Loaded canonical model %s: %d entities, %d fields, %d semantic terms",
        model_ref,
        len({f.entity for f in fields}),
        len(fields),
        len(semantic_terms),
    )
    return CanonicalModel(model_ref, semantic_terms, fields)

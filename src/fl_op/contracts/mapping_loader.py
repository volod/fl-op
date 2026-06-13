"""Loader for per-domain canonical mapping documents.

A mapping document (``*.mapping.yaml``, ``kind: CanonicalMapping``) declares how a
physical domain schema projects onto the canonical optimization model. It carries
record-level metadata (domain, source contract, canonical entity, asset role) and
a ``fieldMappings`` list, each entry binding one physical source field to a
canonical binding path + semantic term.

The mapping document is the authority for all semantic bindings. Field mappings
are parsed into the ``FieldBinding`` shape the mapping engine consumes.
"""

import logging
import pathlib
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from fl_op.contracts.xopt import FieldBinding, XOptFieldMeta
from fl_op.core.constants import XOPT_EXTENSION_VERSION

logger = logging.getLogger(__name__)


class CanonicalMapping(BaseModel):
    """A parsed physical-to-canonical mapping document."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    domain: str
    source_contract: str
    canonical_entity: str
    canonical_model_ref: str
    asset_role: Optional[str] = None
    mapping_version: Optional[str] = None
    data_product_role: Optional[str] = None
    permitted_planning_uses: list[str] = Field(default_factory=list)
    # Value-level normalization for observation mappings: raw source metric
    # code -> canonical metric code the monitoring policy interprets.
    metric_codes: dict[str, str] = Field(default_factory=dict)
    bindings: list[FieldBinding] = Field(default_factory=list)


def _parse_field_mappings(field_mappings: Any) -> list[FieldBinding]:
    bindings: list[FieldBinding] = []
    if not isinstance(field_mappings, list):
        return bindings
    for fm in field_mappings:
        if not isinstance(fm, dict):
            continue
        meta_dict = {k: v for k, v in fm.items() if k != "sourceField"}
        # Field mappings omit the extension version for brevity; inject it so
        # every binding still carries its extension provenance.
        meta_dict.setdefault("extensionVersion", XOPT_EXTENSION_VERSION)
        bindings.append(
            FieldBinding(
                source_field=fm["sourceField"],
                meta=XOptFieldMeta.model_validate(meta_dict),
            )
        )
    return bindings


def load_mapping(path: pathlib.Path) -> CanonicalMapping:
    """Load and parse a CanonicalMapping document."""
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"Mapping document {path} is not a mapping document")
    meta = doc.get("metadata") or {}
    mapping = CanonicalMapping(
        domain=meta.get("domain", ""),
        source_contract=meta.get("sourceContract", ""),
        canonical_entity=meta.get("canonicalEntity", ""),
        canonical_model_ref=meta.get("canonicalModelRef", ""),
        asset_role=meta.get("assetRole"),
        mapping_version=meta.get("mappingVersion"),
        data_product_role=meta.get("dataProductRole"),
        permitted_planning_uses=list(meta.get("permittedPlanningUses") or []),
        metric_codes=dict(meta.get("metricCodes") or {}),
        bindings=_parse_field_mappings(doc.get("fieldMappings")),
    )
    logger.debug(
        "Loaded mapping %s -> %s (%d field mappings)",
        mapping.source_contract,
        mapping.canonical_entity,
        len(mapping.bindings),
    )
    return mapping


def mapping_metadata_blocks(path: pathlib.Path) -> dict[str, Any]:
    """Return the raw mapping document for fingerprinting (record + field blocks)."""
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"Mapping document {path} is not a mapping document")
    return doc

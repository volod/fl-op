"""Load ODCS contracts and extract their xOptimization bindings.

An ODCS contract carries the same binding information as the Avro schema, but
expressed through `customProperties` with `property: xOptimization`. The loader
flattens these into the same FieldBinding shape used by the Avro loader so the
two can be cross-checked during validation.
"""

import logging
import pathlib
from typing import Any, Optional

import yaml

from fl_op.contracts.xopt import (
    FieldBinding,
    XOptContractProfile,
    XOptFieldMeta,
)
from fl_op.core.constants import XOPT_ODCS_PROPERTY

logger = logging.getLogger(__name__)


def _find_xopt_value(custom_properties: Any) -> Optional[dict[str, Any]]:
    """Return the value dict of the xOptimization customProperty, if present."""
    if not isinstance(custom_properties, list):
        return None
    for cp in custom_properties:
        if isinstance(cp, dict) and cp.get("property") == XOPT_ODCS_PROPERTY:
            value = cp.get("value")
            if isinstance(value, dict):
                return value
    return None


class OdcsContract:
    """A parsed ODCS contract plus its extracted xOptimization metadata."""

    def __init__(self, doc: dict[str, Any], source_path: pathlib.Path) -> None:
        self.doc = doc
        self.source_path = source_path
        self.id: str = doc.get("id", "")
        self.version: str = doc.get("version", "")
        self.status: str = doc.get("status", "")

        root_value = _find_xopt_value(doc.get("customProperties"))
        self.profile: XOptContractProfile | None = (
            XOptContractProfile.model_validate(root_value) if root_value else None
        )

        self.bindings: list[FieldBinding] = []
        for schema_obj in doc.get("schema", []):
            if not isinstance(schema_obj, dict):
                continue
            for prop in schema_obj.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                value = _find_xopt_value(prop.get("customProperties"))
                if value is None:
                    continue
                self.bindings.append(
                    FieldBinding(
                        source_field=prop["name"],
                        meta=XOptFieldMeta.model_validate(value),
                    )
                )

    def binding_map(self) -> dict[str, str]:
        """Return {source_field: binding} for cross-checking against Avro."""
        return {b.source_field: b.binding for b in self.bindings}


def load_odcs_contract(path: pathlib.Path) -> OdcsContract:
    """Load and parse an ODCS YAML contract."""
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"ODCS contract {path} is not a mapping document")
    contract = OdcsContract(doc, path)
    logger.debug(
        "Loaded ODCS contract %s v%s (%d bindings)",
        contract.id,
        contract.version,
        len(contract.bindings),
    )
    return contract

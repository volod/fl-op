"""Load physical ODCS data contracts.

A physical ODCS contract describes only the raw domain schema (field names,
types, and schema-generation hints). All optimization semantics live in the
per-domain canonical mapping documents (see fl_op.contracts.mapping_loader), so
this loader simply parses and holds the ODCS document for schema generation.
"""

import logging
import pathlib
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class OdcsContract:
    """A parsed physical ODCS contract document."""

    def __init__(self, doc: dict[str, Any], source_path: pathlib.Path) -> None:
        self.doc = doc
        self.source_path = source_path
        self.id: str = doc.get("id", "")
        self.version: str = doc.get("version", "")
        self.status: str = doc.get("status", "")

    def field_names(self) -> list[str]:
        """Return the physical field names declared by this contract."""
        names: list[str] = []
        for schema_obj in self.doc.get("schema", []):
            if not isinstance(schema_obj, dict):
                continue
            for prop in schema_obj.get("properties", []):
                if isinstance(prop, dict) and "name" in prop:
                    names.append(prop["name"])
        return names


def load_odcs_contract(path: pathlib.Path) -> OdcsContract:
    """Load and parse a physical ODCS YAML contract."""
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"ODCS contract {path} is not a mapping document")
    contract = OdcsContract(doc, path)
    logger.debug("Loaded ODCS contract %s v%s", contract.id, contract.version)
    return contract

"""Load generated Avro schemas for structural validation and fingerprinting.

Generated Avro schemas are pure physical format descriptors: they carry field
names, types, nullability, aliases, and defaults. They contain no x-optimization
metadata. All semantic information lives in the ODCS contracts.
"""

import copy
import json
import logging
import pathlib
from typing import Any

import fastavro

from fl_op.contracts.fingerprint import avro_parsing_fingerprint

logger = logging.getLogger(__name__)


class AvroContractSchema:
    """A parsed Avro record schema, used for structural fingerprinting and ser/de."""

    def __init__(self, schema_json: dict[str, Any], source_path: pathlib.Path) -> None:
        self.schema_json = schema_json
        self.source_path = source_path
        self.name: str = schema_json.get("name", "")
        self.namespace: str = schema_json.get("namespace", "")
        self.fields: list[dict[str, Any]] = list(schema_json.get("fields", []))

    @property
    def avro_parsing_fingerprint(self) -> str:
        return avro_parsing_fingerprint(self.schema_json)


def load_avro_schema(path: pathlib.Path) -> AvroContractSchema:
    """Load and validate an Avro .avsc file."""
    schema_json = json.loads(path.read_text())
    fastavro.parse_schema(copy.deepcopy(schema_json), _force=True)
    schema = AvroContractSchema(schema_json, path)
    logger.debug("Loaded Avro schema %s (%d fields)", schema.name, len(schema.fields))
    return schema

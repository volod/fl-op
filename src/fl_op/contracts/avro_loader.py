"""Load Avro schemas and extract x-optimization bindings.

The raw `.avsc` JSON is the authority for both the structural fingerprint and
the optimization metadata. fastavro is used to confirm the schema parses (and to
exercise the metadata round-trip in tests), but binding extraction works on the
raw JSON so it never depends on library preservation behavior.
"""

import copy
import json
import logging
import pathlib
from typing import Any

import fastavro

from fl_op.contracts.fingerprint import both_fingerprints
from fl_op.contracts.xopt import (
    FieldBinding,
    XOptFieldMeta,
    XOptRecordMeta,
    extract_xopt_block,
)

logger = logging.getLogger(__name__)


class AvroContractSchema:
    """A parsed Avro record schema plus its extracted x-optimization metadata."""

    def __init__(self, schema_json: dict[str, Any], source_path: pathlib.Path) -> None:
        self.schema_json = schema_json
        self.source_path = source_path
        self.name: str = schema_json.get("name", "")
        self.namespace: str = schema_json.get("namespace", "")

        record_block = extract_xopt_block(schema_json)
        self.record_meta: XOptRecordMeta | None = (
            XOptRecordMeta.model_validate(record_block) if record_block else None
        )

        self.bindings: list[FieldBinding] = []
        for field in schema_json.get("fields", []):
            block = extract_xopt_block(field)
            if block is None:
                continue
            self.bindings.append(
                FieldBinding(
                    source_field=field["name"],
                    meta=XOptFieldMeta.model_validate(block),
                )
            )

    @property
    def fingerprints(self) -> dict[str, str]:
        return both_fingerprints(self.schema_json)

    def roundtrip_metadata(self) -> dict[str, Any]:
        """Parse via fastavro, serialize back to JSON, reparse; return the result.

        Used by the round-trip conformance test to prove metadata preservation.
        """
        parsed = fastavro.parse_schema(copy.deepcopy(self.schema_json), _force=True)
        # Strip fastavro-internal keys before serializing back to portable JSON.
        portable = json.loads(json.dumps(parsed, default=str))
        for internal in ("__fastavro_parsed", "__named_schemas"):
            portable.pop(internal, None)
        for field in portable.get("fields", []):
            field.pop("__fastavro_parsed", None)
        return portable


def load_avro_schema(path: pathlib.Path) -> AvroContractSchema:
    """Load and parse an Avro `.avsc` file into an AvroContractSchema."""
    schema_json = json.loads(path.read_text())
    # Validate it is a well-formed Avro schema; parse on a copy because
    # fastavro.parse_schema mutates its input in place.
    fastavro.parse_schema(copy.deepcopy(schema_json), _force=True)
    schema = AvroContractSchema(schema_json, path)
    logger.debug(
        "Loaded Avro schema %s (%d bindings)", schema.name, len(schema.bindings)
    )
    return schema

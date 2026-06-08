"""Generate clean physical Avro schemas from ODCS contracts.

Generated schemas contain no x-optimization metadata. They carry only the
structural information needed for Avro serialization: field names, types,
nullability, aliases, defaults, and Avro logical types.

Logical type mapping:
  quantityKind: timestamp  -> {"type":"long", "logicalType":"timestamp-millis"}
  logicalType: date        -> {"type":"int",  "logicalType":"date"}
  (all other fields)       -> physicalType mapped via PHYSICAL_TYPE_TO_AVRO
"""

import json
from typing import Any

from fl_op.contracts.gen.base import (
    PHYSICAL_TYPE_TO_AVRO,
    GenerationError,
    GeneratorBase,
    iter_schema_properties,
    semantic_hints,
)

_AVRO_TIMESTAMP_MILLIS: dict[str, Any] = {"type": "long", "logicalType": "timestamp-millis"}
_AVRO_DATE: dict[str, Any] = {"type": "int", "logicalType": "date"}


class AvroGenerator(GeneratorBase):
    FORMAT_KEY = "avro"

    _REQUIRED_SCHEMA_KEYS = ("namespace", "recordName", "recordDoc")

    def generate(self, odcs_doc: dict[str, Any], contract_id: str) -> str:
        hints = self.get_schema_gen_hints(odcs_doc)
        for key in self._REQUIRED_SCHEMA_KEYS:
            if not hints.get(key):
                raise GenerationError(
                    f"{contract_id}: schemaGeneration.avro missing required key '{key}'"
                )

        fields = []
        for prop in iter_schema_properties(odcs_doc):
            fields.append(self._build_field(prop, contract_id))

        schema: dict[str, Any] = {
            "type": "record",
            "name": hints["recordName"],
            "namespace": hints["namespace"],
            "doc": hints["recordDoc"],
            "fields": fields,
        }
        return json.dumps(schema, indent=2, ensure_ascii=True)

    def _build_field(self, prop: dict[str, Any], contract_id: str) -> dict[str, Any]:
        name = prop.get("name", "")
        physical_type = prop.get("physicalType", "")
        if physical_type not in PHYSICAL_TYPE_TO_AVRO:
            raise GenerationError(
                f"{contract_id}: field '{name}' has unknown physicalType '{physical_type}'"
            )
        sh = semantic_hints(prop)
        required = prop.get("required", True)
        field_hints = self.get_field_gen_hints(prop)

        # Determine base Avro type — use logical types for datetime semantics.
        if sh["is_timestamp"]:
            base: Any = _AVRO_TIMESTAMP_MILLIS
        elif sh["is_date"]:
            base = _AVRO_DATE
        else:
            base = PHYSICAL_TYPE_TO_AVRO[physical_type]

        field: dict[str, Any] = {"name": name}

        if "aliases" in field_hints:
            field["aliases"] = field_hints["aliases"]

        if "default" in field_hints:
            field["type"] = base
            field["default"] = field_hints["default"]
        elif not required:
            field["type"] = ["null", base]
            field["default"] = None
        else:
            field["type"] = base

        field["doc"] = prop.get("description", "")
        return field

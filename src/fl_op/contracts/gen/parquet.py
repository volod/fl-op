"""ODCS-to-Parquet Arrow schema descriptor generator.

Generates a JSON descriptor documenting the canonical pyarrow type for each
field. Native Arrow logical types are used where applicable:

  quantityKind: timestamp  -> timestamp[ms, tz=UTC]   (pa.timestamp('ms','UTC'))
  logicalType: date        -> date32                   (pa.date32())
  lat / lon coordinates    -> double (float64)
  (other fields)           -> PHYSICAL_TYPE_TO_PARQUET mapping
"""

import json
from typing import Any

from fl_op.contracts.gen.base import (
    PHYSICAL_TYPE_TO_PARQUET,
    GeneratorBase,
    iter_schema_properties,
    semantic_hints,
)

_ARROW_TIMESTAMP = "timestamp[ms, tz=UTC]"
_ARROW_DATE = "date32"


class ParquetGenerator(GeneratorBase):
    """Generates an Arrow type descriptor JSON for a Parquet dataset."""

    FORMAT_KEY = "parquet"

    def get_schema_gen_hints(self, odcs_doc: dict[str, Any]) -> dict[str, Any]:
        # No schemaGeneration hints required for Parquet; types derived from
        # physicalType and semantic signals.
        return {}

    def generate(self, odcs_doc: dict[str, Any], contract_id: str) -> str:
        schema_objs = odcs_doc.get("schema", [])
        entity_name = (
            schema_objs[0].get("name", contract_id)
            if schema_objs and isinstance(schema_objs[0], dict)
            else contract_id
        )

        fields = []
        for prop in iter_schema_properties(odcs_doc):
            ptype = prop.get("physicalType", "string")
            sh = semantic_hints(prop)

            if sh["is_timestamp"]:
                arrow_type = _ARROW_TIMESTAMP
            elif sh["is_date"]:
                arrow_type = _ARROW_DATE
            else:
                arrow_type = PHYSICAL_TYPE_TO_PARQUET.get(ptype, "large_string")

            fields.append(
                {
                    "name": prop.get("name", ""),
                    "arrow_type": arrow_type,
                    "nullable": not prop.get("required", False),
                    "description": prop.get("description", ""),
                }
            )

        descriptor = {
            "entity": entity_name,
            "contract_id": contract_id,
            "fields": fields,
        }
        return json.dumps(descriptor, indent=2, ensure_ascii=True)

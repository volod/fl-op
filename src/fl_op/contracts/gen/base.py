"""Shared type maps, helpers, and base class for ODCS-to-format generators."""

import abc
from typing import Any, Optional

from fl_op.core.constants import FIELD_GEN_PROPERTY, SCHEMA_GEN_PROPERTY, XOPT_ODCS_PROPERTY


PHYSICAL_TYPE_TO_AVRO: dict[str, str] = {
    "string": "string",
    "double": "double",
    "float": "float",
    "int": "int",
    "integer": "int",
    "long": "long",
    "boolean": "boolean",
    "bool": "boolean",
    "bytes": "bytes",
}

PHYSICAL_TYPE_TO_PROTO: dict[str, str] = {
    "string": "string",
    "double": "double",
    "float": "float",
    "int": "int32",
    "integer": "int32",
    "long": "int64",
    "boolean": "bool",
    "bool": "bool",
    "bytes": "bytes",
}

PHYSICAL_TYPE_TO_ES: dict[str, str] = {
    "string": "keyword",
    "double": "double",
    "float": "float",
    "int": "integer",
    "integer": "integer",
    "long": "long",
    "boolean": "boolean",
    "bool": "boolean",
    "bytes": "binary",
}

PHYSICAL_TYPE_TO_PARQUET: dict[str, str] = {
    "string": "large_string",
    "double": "float64",
    "float": "float32",
    "int": "int32",
    "integer": "int32",
    "long": "int64",
    "boolean": "bool_",
    "bool": "bool_",
    "bytes": "large_binary",
}

VALID_ES_TYPES: frozenset[str] = frozenset(
    {
        "keyword", "text", "integer", "long", "float", "double",
        "boolean", "date", "binary", "object", "nested", "geo_point",
    }
)

# Maximum character length for keyword fields before ES truncates and ignores.
ES_KEYWORD_IGNORE_ABOVE: int = 256

# ES date field format covering ISO-8601 strings and epoch-millisecond integers.
ES_DATE_FORMAT: str = "strict_date_optional_time||epoch_millis"

PROTO3_RESERVED_WORDS: frozenset[str] = frozenset(
    {
        "syntax", "import", "package", "option", "message", "enum", "service",
        "rpc", "returns", "stream", "oneof", "map", "extensions", "reserved",
        "to", "max", "repeated", "optional", "required",
    }
)


def semantic_hints(prop: dict[str, Any]) -> dict[str, bool]:
    """Return a dict of semantic flags derived from a field's xOptimization block.

    Flags:
      is_latitude   -- field is a WGS-84 latitude coordinate
      is_longitude  -- field is a WGS-84 longitude coordinate
      is_timestamp  -- field is an absolute point-in-time (datetime + timezone)
      is_date       -- field is a calendar date (no time component)
    """
    xopt: dict[str, Any] = {}
    custom_properties = prop.get("customProperties")
    if isinstance(custom_properties, list):
        for cp in custom_properties:
            if isinstance(cp, dict) and cp.get("property") == "xOptimization":
                v = cp.get("value")
                if isinstance(v, dict):
                    xopt = v
                break

    term: str = str(xopt.get("semanticTerm", ""))
    quantity_kind: str = str(xopt.get("quantityKind", ""))
    logical_type: str = str(prop.get("logicalType", ""))

    return {
        "is_latitude": term.endswith(":latitude") or term.endswith(":lat"),
        "is_longitude": term.endswith(":longitude") or term.endswith(":lon"),
        "is_timestamp": quantity_kind == "timestamp" or logical_type in ("datetime", "timestamp"),
        "is_date": logical_type == "date" and quantity_kind != "timestamp",
    }


def find_property(custom_properties: Any, name: str) -> Optional[dict[str, Any]]:
    """Return the value of a named customProperty from a ODCS customProperties list."""
    if not isinstance(custom_properties, list):
        return None
    for item in custom_properties:
        if isinstance(item, dict) and item.get("property") == name:
            value = item.get("value")
            if isinstance(value, dict):
                return value
    return None


def iter_schema_properties(odcs_doc: dict[str, Any]):
    """Yield each field property dict from schema[0].properties."""
    for schema_obj in odcs_doc.get("schema", []):
        if not isinstance(schema_obj, dict):
            continue
        for prop in schema_obj.get("properties", []):
            if isinstance(prop, dict):
                yield prop


class GenerationError(ValueError):
    """Raised when an ODCS contract is missing required generation hints."""


class GeneratorBase(abc.ABC):
    """Abstract base for ODCS-to-format generators."""

    FORMAT_KEY: str

    def get_schema_gen_hints(self, odcs_doc: dict[str, Any]) -> dict[str, Any]:
        """Extract schemaGeneration hints for this format; raise if absent."""
        schema_objs = odcs_doc.get("schema", [])
        if not schema_objs:
            raise GenerationError("ODCS document has no 'schema' section")
        schema_obj = schema_objs[0]
        root_gen = find_property(schema_obj.get("customProperties"), SCHEMA_GEN_PROPERTY)
        if root_gen is None:
            raise GenerationError(
                f"Missing '{SCHEMA_GEN_PROPERTY}' customProperty in schema[0]"
            )
        hints = root_gen.get(self.FORMAT_KEY)
        if not isinstance(hints, dict):
            raise GenerationError(
                f"Missing or invalid '{self.FORMAT_KEY}' block inside {SCHEMA_GEN_PROPERTY}"
            )
        return hints

    def get_field_gen_hints(self, prop: dict[str, Any]) -> dict[str, Any]:
        """Extract fieldGeneration hints for this format from a field property dict."""
        field_gen = find_property(prop.get("customProperties"), FIELD_GEN_PROPERTY)
        if field_gen is None:
            return {}
        return field_gen.get(self.FORMAT_KEY) or {}

    def get_xopt(self, prop: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Extract the xOptimization block from a field's customProperties."""
        return find_property(prop.get("customProperties"), XOPT_ODCS_PROPERTY)

    @abc.abstractmethod
    def generate(self, odcs_doc: dict[str, Any], contract_id: str) -> str:
        """Generate the physical schema as a string; raise GenerationError on failure."""

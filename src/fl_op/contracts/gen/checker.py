"""Generation-readiness checker for ODCS contracts.

Validates that every contract has the schemaGeneration and fieldGeneration
customProperties required to drive schema generation for a given format.
"""

import dataclasses
from typing import Any

from fl_op.contracts.gen.base import (
    FIELD_GEN_PROPERTY,
    PHYSICAL_TYPE_TO_AVRO,
    PHYSICAL_TYPE_TO_ES,
    PHYSICAL_TYPE_TO_PARQUET,
    PHYSICAL_TYPE_TO_PROTO,
    PROTO3_RESERVED_WORDS,
    SCHEMA_GEN_PROPERTY,
    VALID_ES_TYPES,
    find_property,
    iter_schema_properties,
)


@dataclasses.dataclass
class FieldCheckResult:
    field_name: str
    errors: list[str] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclasses.dataclass
class GenerationCheckReport:
    contract_id: str
    fmt: str
    field_results: list[FieldCheckResult] = dataclasses.field(default_factory=list)
    schema_errors: list[str] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.schema_errors and all(f.ok for f in self.field_results)

    @property
    def errors(self) -> list[str]:
        all_errors = list(self.schema_errors)
        for fr in self.field_results:
            for err in fr.errors:
                all_errors.append(f"field '{fr.field_name}': {err}")
        return all_errors


def _type_map_for_format(fmt: str) -> dict[str, str]:
    if fmt == "avro":
        return PHYSICAL_TYPE_TO_AVRO
    if fmt == "proto":
        return PHYSICAL_TYPE_TO_PROTO
    if fmt == "parquet":
        return PHYSICAL_TYPE_TO_PARQUET
    return PHYSICAL_TYPE_TO_ES


def _check_schema_level(odcs_doc: dict[str, Any], fmt: str) -> list[str]:
    errors: list[str] = []
    schema_objs = odcs_doc.get("schema", [])
    if not schema_objs:
        errors.append("no 'schema' section found")
        return errors

    schema_obj = schema_objs[0]
    if fmt == "parquet":
        return errors

    root_gen = find_property(schema_obj.get("customProperties"), SCHEMA_GEN_PROPERTY)
    if root_gen is None:
        errors.append(f"missing '{SCHEMA_GEN_PROPERTY}' customProperty in schema[0]")
        return errors

    fmt_hints = root_gen.get(fmt)
    if not isinstance(fmt_hints, dict):
        errors.append(f"missing or invalid '{fmt}' block inside {SCHEMA_GEN_PROPERTY}")
        return errors

    if fmt == "avro":
        for key in ("namespace", "recordName", "recordDoc"):
            if not fmt_hints.get(key):
                errors.append(f"schemaGeneration.avro.{key} is missing or empty")
    elif fmt == "proto":
        for key in ("package", "messageName"):
            if not fmt_hints.get(key):
                errors.append(f"schemaGeneration.proto.{key} is missing or empty")
    elif fmt == "es":
        if not fmt_hints.get("indexName"):
            errors.append("schemaGeneration.es.indexName is missing or empty")

    return errors


def _check_field(prop: dict[str, Any], fmt: str, seen_field_numbers: set[int]) -> list[str]:
    errors: list[str] = []
    name = prop.get("name", "")
    physical_type = prop.get("physicalType", "")
    type_map = _type_map_for_format(fmt)

    if not physical_type:
        errors.append("physicalType is missing")
    elif physical_type not in type_map:
        errors.append(f"physicalType '{physical_type}' is not in the {fmt} type map")

    field_gen = find_property(prop.get("customProperties"), FIELD_GEN_PROPERTY)
    fmt_hints = (field_gen or {}).get(fmt) or {}

    if fmt == "proto":
        fn = fmt_hints.get("fieldNumber")
        if not isinstance(fn, int) or fn < 1:
            errors.append("fieldGeneration.proto.fieldNumber is missing or invalid")
        elif fn in seen_field_numbers:
            errors.append(f"fieldGeneration.proto.fieldNumber {fn} is already used by another field")
        else:
            seen_field_numbers.add(fn)
        if name in PROTO3_RESERVED_WORDS:
            errors.append(f"field name '{name}' conflicts with a proto3 reserved word")

    if fmt == "es":
        es_type_override = fmt_hints.get("type")
        if es_type_override and es_type_override not in VALID_ES_TYPES:
            errors.append(f"fieldGeneration.es.type '{es_type_override}' is not a valid ES mapping type")

    return errors


def check_generation(
    odcs_doc: dict[str, Any],
    contract_id: str,
    fmt: str,
) -> GenerationCheckReport:
    """Validate that odcs_doc has complete generation hints for the given format."""
    report = GenerationCheckReport(contract_id=contract_id, fmt=fmt)
    report.schema_errors = _check_schema_level(odcs_doc, fmt)
    if report.schema_errors:
        return report

    seen_field_numbers: set[int] = set()
    for prop in iter_schema_properties(odcs_doc):
        field_name = prop.get("name", "")
        field_errors = _check_field(prop, fmt, seen_field_numbers)
        report.field_results.append(FieldCheckResult(field_name=field_name, errors=field_errors))

    return report

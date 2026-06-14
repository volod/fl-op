"""Physical output schemas generated from the canonical plan contract.

Downstream consumers receive plan artifacts (plan.json payloads) without this
codebase. Generating Avro and Parquet descriptors from the same contract that
gates publication lets them validate the documents they receive, exactly as
input contracts get physical schemas. Field names follow the artifact payload
(plan_contract's binding-to-payload table), so the generated schema validates
plan.json as written, not the contract's binding vocabulary.
"""

import json
import logging
import pathlib
from typing import Any, Optional

import yaml

from fl_op.contracts.plan_contract import (
    _PLAN_BINDING_PATHS,
    _RECORD_PATH_SEPARATOR,
)
from fl_op.core.paths import CONTRACTS_ROOT

logger = logging.getLogger(__name__)

PLAN_CONTRACT_ID = "canonical-plan"
PLAN_OUTPUT_FORMATS = ("avro", "parquet")

_PLAN_ODCS_RELPATH = pathlib.Path("canonical/odcs/plan.odcs.yaml")
_AVRO_NAMESPACE = "org.fl_op.canonical"

# Payload timestamps are ISO-8601 strings in plan.json, so the schema keeps
# them as strings: the artifact validates as written.
_AVRO_BY_LOGICAL: dict[str, Any] = {
    "string": "string",
    "number": "double",
    "integer": "long",
    "array": {"type": "array", "items": "string"},
    "object": {
        "type": "map",
        "values": ["null", "string", "double", "long", "boolean"],
    },
}
_ARROW_BY_LOGICAL: dict[str, str] = {
    "string": "large_string",
    "number": "float64",
    "integer": "int64",
    "array": "list<large_string>",
    "object": "map<large_string, large_string>",
}

# Payload list field -> generated nested record name.
_RECORD_NAMES = {
    "assignments": "PlanAssignment",
    "unassigned_tasks": "PlanUnassignedTask",
    "material_reservations": "PlanMaterialReservation",
    "corrective_actions": "PlanCorrectiveAction",
}

# Payload object field -> generated nested record name.
_OBJECT_NAMES = {
    "score": "PlanScore",
    "quality_summary": "PlanQualitySummary",
    "risk_summary": "PlanRiskSummary",
}

# (payload_name, logicalType, required) for one schema field.
_FieldSpec = tuple[str, str, bool]


def _plan_fields(
    contracts_root: pathlib.Path,
) -> tuple[
    list[_FieldSpec],
    dict[str, list[_FieldSpec]],
    dict[str, list[_FieldSpec]],
]:
    """Plan-envelope fields and per-list record fields, in payload vocabulary.

    Joins the plan ODCS contract (types, requiredness) with the publication
    validator's binding-to-payload table, so generation and validation can
    never drift apart.
    """
    doc = yaml.safe_load((contracts_root / _PLAN_ODCS_RELPATH).read_text())
    by_binding: dict[str, dict[str, Any]] = {}
    for schema_obj in doc.get("schema", []):
        for prop in schema_obj.get("properties", []) or []:
            for cp in prop.get("customProperties", []) or []:
                if cp.get("property") == "canonicalBinding":
                    by_binding[cp["value"]["binding"]] = prop

    top: list[_FieldSpec] = []
    objects: dict[str, list[_FieldSpec]] = {}
    records: dict[str, list[_FieldSpec]] = {}
    for binding, path in _PLAN_BINDING_PATHS.items():
        prop = by_binding.get(binding)
        if prop is None:
            continue
        logical = str(prop.get("logicalType", "string"))
        required = bool(prop.get("required", False))
        if _RECORD_PATH_SEPARATOR in path:
            list_field, record_field = path.split(_RECORD_PATH_SEPARATOR, 1)
            records.setdefault(list_field, []).append(
                (record_field, logical, required)
            )
        elif "." in path:
            object_field, nested_field = path.split(".", 1)
            objects.setdefault(object_field, []).append(
                (nested_field, logical, required)
            )
        else:
            top.append((path, logical, required))
    return top, objects, records


def _avro_field(name: str, logical: str, required: bool) -> dict[str, Any]:
    base = _AVRO_BY_LOGICAL.get(logical, "string")
    if required:
        return {"name": name, "type": base}
    return {"name": name, "type": ["null", base], "default": None}


def generate_plan_avro(contracts_root: Optional[pathlib.Path] = None) -> str:
    """Avro schema of the plan artifact: the envelope with nested record arrays."""
    root = contracts_root or CONTRACTS_ROOT
    top, objects, records = _plan_fields(root)
    fields = [_avro_field(*spec) for spec in top]
    for object_field in sorted(objects):
        record = {
            "type": "record",
            "name": _OBJECT_NAMES.get(object_field, object_field.title()),
            "fields": [_avro_field(*spec) for spec in objects[object_field]],
        }
        fields.append(
            {
                "name": object_field,
                "type": record,
            }
        )
    for list_field in sorted(records):
        record = {
            "type": "record",
            "name": _RECORD_NAMES.get(list_field, list_field.title()),
            "fields": [_avro_field(*spec) for spec in records[list_field]],
        }
        fields.append(
            {
                "name": list_field,
                "type": {"type": "array", "items": record},
                "default": [],
            }
        )
    schema = {
        "type": "record",
        "name": "Plan",
        "namespace": _AVRO_NAMESPACE,
        "doc": (
            "Canonical plan artifact (plan.json payload shape), generated "
            "from the canonical-plan ODCS contract."
        ),
        "fields": fields,
    }
    return json.dumps(schema, indent=2, ensure_ascii=True)


def generate_plan_parquet(contracts_root: Optional[pathlib.Path] = None) -> str:
    """Arrow type descriptor of the plan artifact, nested lists as structs."""
    root = contracts_root or CONTRACTS_ROOT
    top, objects, records = _plan_fields(root)
    fields: list[dict[str, Any]] = [
        {
            "name": name,
            "arrow_type": _ARROW_BY_LOGICAL.get(logical, "large_string"),
            "nullable": not required,
        }
        for name, logical, required in top
    ]
    for object_field in sorted(objects):
        fields.append(
            {
                "name": object_field,
                "arrow_type": f"struct<{_OBJECT_NAMES.get(object_field, object_field)}>",
                "nullable": False,
                "struct_fields": [
                    {
                        "name": name,
                        "arrow_type": _ARROW_BY_LOGICAL.get(logical, "large_string"),
                        "nullable": not required,
                    }
                    for name, logical, required in objects[object_field]
                ],
            }
        )
    for list_field in sorted(records):
        fields.append(
            {
                "name": list_field,
                "arrow_type": f"list<struct<{_RECORD_NAMES.get(list_field, list_field)}>>",
                "nullable": False,
                "struct_fields": [
                    {
                        "name": name,
                        "arrow_type": _ARROW_BY_LOGICAL.get(logical, "large_string"),
                        "nullable": not required,
                    }
                    for name, logical, required in records[list_field]
                ],
            }
        )
    descriptor = {
        "contract": PLAN_CONTRACT_ID,
        "entity": "plan",
        "fields": fields,
    }
    return json.dumps(descriptor, indent=2, ensure_ascii=True)


def write_plan_schema(
    fmt: str,
    out_dir: pathlib.Path,
    contracts_root: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    """Write the plan output schema for one format; return the output path."""
    if fmt == "avro":
        content, ext = generate_plan_avro(contracts_root), ".avsc"
    elif fmt == "parquet":
        content, ext = generate_plan_parquet(contracts_root), ".parquet.json"
    else:
        raise ValueError(f"No plan output schema generator for format '{fmt}'")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{PLAN_CONTRACT_ID}{ext}"
    out_path.write_text(content, encoding="utf-8")
    logger.info("Generated %s plan output schema -> %s", fmt, out_path)
    return out_path

"""Per-row binding accumulation for source-to-canonical mapping."""

from typing import Any, Optional

from fl_op.canonical.asset import Capability
from fl_op.contracts.xopt import FieldBinding
from fl_op.mapping.bindings import BindingTable
from fl_op.mapping.policies import apply_missing_value_policy
from fl_op.mapping.records import coerce_value
from fl_op.mapping.result import MappingResult
from fl_op.mapping.units import convert_to_canonical


def accumulate_row(
    table: BindingTable,
    row: dict[str, Any],
    result: MappingResult,
) -> Optional[dict[str, Any]]:
    """Resolve every binding for one row; return an accumulator or None if dropped."""
    key_field = table.entity_key_field or ""
    entity_ref = str(row.get(key_field, "<unknown>"))
    acc: dict[str, Any] = {"_capabilities": [], "_inventory": []}
    seq = 0

    for binding in table.bindings:
        raw = row.get(binding.source_field)
        seq += 1
        outcome = apply_missing_value_policy(
            raw_value=raw,
            policy=binding.meta.missing_value_policy,
            entity_ref=entity_ref,
            field_ref=binding.source_field,
            quantity_kind=binding.meta.quantity_kind,
            quality_policy_ref=binding.meta.quality_policy_ref,
            finding_seq=seq,
        )
        if outcome.finding is not None:
            result.findings.append(outcome.finding)
        if outcome.drop_entity:
            result.excluded.setdefault(table.contract_id, []).append(entity_ref)
            return None
        resolved = outcome.value
        if resolved is None:
            continue

        value = coerce_value(binding.meta, resolved)
        if isinstance(value, float) and binding.meta.canonical_unit:
            value = convert_to_canonical(value, binding.meta.canonical_unit)

        route_value(acc, binding, value)

    return acc


def route_value(acc: dict[str, Any], binding: FieldBinding, value: Any) -> None:
    """Route a coerced value into the accumulator based on its binding path."""
    tokens = binding.meta.binding.split(".")
    if "capabilities" in tokens or "availability" in tokens:
        acc["_capabilities"].append(
            Capability(
                capability_id=f"{tokens[-1]}",
                semantic_term=binding.meta.semantic_term,
                value=value,
                canonical_unit=binding.meta.canonical_unit,
            )
        )
        return
    if tokens[:2] == ["location", "inventory"] or tokens[:2] == ["asset", "inventory"]:
        acc["_inventory"].append((tokens[-1], binding.meta.canonical_unit, value))
        return
    set_path(acc, tokens[1:], value)


def set_path(acc: dict[str, Any], tokens: list[str], value: Any) -> None:
    """Set a nested value in the accumulator dict following dotted tokens."""
    node = acc
    for tok in tokens[:-1]:
        node = node.setdefault(tok, {})
    node[tokens[-1]] = value

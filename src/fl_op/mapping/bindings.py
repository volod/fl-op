"""Resolve x-optimization bindings for a dataset into a usable BindingTable.

The Avro schema is the authority for bindings (spec 9.1). The BindingTable
exposes both the forward direction (source field -> canonical path), used by the
mapping engine, and the reverse direction (canonical path -> source field), used
by the snapshot solver-payload projector to reconstruct solver rows from
canonical objects.
"""

from dataclasses import dataclass

from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.xopt import FieldBinding, XOptRecordMeta


@dataclass
class BindingTable:
    contract_id: str
    canonical_entity: str
    record_meta: XOptRecordMeta | None
    bindings: list[FieldBinding]

    def by_source_field(self) -> dict[str, FieldBinding]:
        return {b.source_field: b for b in self.bindings}

    def by_binding_path(self) -> dict[str, FieldBinding]:
        return {b.meta.binding: b for b in self.bindings}

    @property
    def entity_key_field(self) -> str | None:
        return self.record_meta.entity_key_field if self.record_meta else None

    @property
    def asset_role(self) -> str | None:
        return self.record_meta.asset_role if self.record_meta else None


def load_binding_table(registry: FileRegistry, contract_id: str) -> BindingTable:
    """Build a BindingTable for a registered contract from its Avro schema."""
    entry = registry.get_entry(contract_id)
    avro = registry.get_avro(contract_id)
    return BindingTable(
        contract_id=contract_id,
        canonical_entity=entry.canonical_entity or "",
        record_meta=avro.record_meta,
        bindings=list(avro.bindings),
    )

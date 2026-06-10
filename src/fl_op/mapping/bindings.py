"""Resolve a contract's canonical mapping into a usable BindingTable.

The canonical mapping document (contracts/domains/<domain>/mappings/<contract>.mapping.yaml)
is the authority for all semantic bindings. The BindingTable exposes both the
forward direction (source field -> canonical path), used by the mapping engine,
and the reverse direction (canonical path -> source field), used by the snapshot
solver-payload projector to reconstruct solver rows from canonical objects.
"""

from dataclasses import dataclass, field
from typing import Optional

from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.xopt import FieldBinding


@dataclass
class BindingTable:
    contract_id: str
    canonical_entity: str
    asset_role: Optional[str]
    bindings: list[FieldBinding]
    # Raw source metric code -> canonical metric code (observation mappings).
    metric_codes: dict[str, str] = field(default_factory=dict)

    def by_source_field(self) -> dict[str, FieldBinding]:
        return {b.source_field: b for b in self.bindings}

    def by_binding_path(self) -> dict[str, FieldBinding]:
        return {b.meta.binding: b for b in self.bindings}

    @property
    def entity_key_field(self) -> Optional[str]:
        for b in self.bindings:
            if "identity" in (b.meta.planning_use or []):
                return b.source_field
        return None


def load_binding_table(registry: FileRegistry, contract_id: str) -> BindingTable:
    """Build a BindingTable for a registered contract from its mapping document."""
    mapping = registry.get_mapping(contract_id)
    if mapping is None:
        return BindingTable(
            contract_id=contract_id,
            canonical_entity="",
            asset_role=None,
            bindings=[],
        )
    return BindingTable(
        contract_id=contract_id,
        canonical_entity=mapping.canonical_entity,
        asset_role=mapping.asset_role,
        bindings=list(mapping.bindings),
        metric_codes=dict(mapping.metric_codes),
    )

"""Resolve x-optimization bindings for a dataset into a usable BindingTable.

The ODCS contract is the authority for all semantic bindings. The BindingTable
exposes both the forward direction (source field -> canonical path), used by the
mapping engine, and the reverse direction (canonical path -> source field), used
by the snapshot solver-payload projector to reconstruct solver rows from
canonical objects.
"""

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.xopt import FieldBinding, XOptContractProfile

if TYPE_CHECKING:
    pass


@dataclass
class BindingTable:
    contract_id: str
    canonical_entity: str
    profile: Optional[XOptContractProfile]
    bindings: list[FieldBinding]

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

    @property
    def asset_role(self) -> Optional[str]:
        return self.profile.asset_role if self.profile else None


def load_binding_table(registry: FileRegistry, contract_id: str) -> BindingTable:
    """Build a BindingTable for a registered contract from its ODCS contract."""
    entry = registry.get_entry(contract_id)
    odcs = registry.get_odcs(contract_id)
    profile = odcs.profile if odcs else None
    bindings = list(odcs.bindings) if odcs else []
    return BindingTable(
        contract_id=contract_id,
        canonical_entity=entry.canonical_entity or "",
        profile=profile,
        bindings=bindings,
    )

"""Declarative source-to-canonical mapping engine.

Reads governed source records and the x-optimization bindings carried by their
contracts, and emits solver-neutral canonical objects with explicit unit
normalization and quality findings.
"""

from fl_op.mapping.bindings import BindingTable, load_binding_table
from fl_op.mapping.engine import MappingEngine, register_entity_emitter
from fl_op.mapping.result import MappingResult

__all__ = [
    "MappingEngine",
    "MappingResult",
    "BindingTable",
    "load_binding_table",
    "register_entity_emitter",
]

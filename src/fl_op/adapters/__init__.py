"""Solver adapters: compile snapshots into solver inputs and normalize results.

Adapters are the only components that invoke a solver, and they consume only
immutable planning snapshots (spec 4.3, 21).
"""

from fl_op.adapters.registry import get_adapter
from fl_op.adapters.spi import (
    AdapterHealth,
    AdapterManifest,
    SolverAdapter,
    ValidationReport,
)

__all__ = [
    "get_adapter",
    "SolverAdapter",
    "AdapterManifest",
    "ValidationReport",
    "AdapterHealth",
]

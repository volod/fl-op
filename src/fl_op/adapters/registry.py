"""Adapter lookup by id."""

from typing import TYPE_CHECKING

from fl_op.core.constants import (
    ADAPTER_ORTOOLS_PERIODIC_ID,
    ADAPTER_ORTOOLS_ROLLING_ID,
)

if TYPE_CHECKING:
    from fl_op.adapters.spi import SolverAdapter


def get_adapter(adapter_id: str) -> "SolverAdapter":
    """Return an adapter instance for a profile-declared adapter id."""
    if adapter_id == ADAPTER_ORTOOLS_PERIODIC_ID:
        from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter

        return OrToolsPeriodicAdapter()
    if adapter_id == ADAPTER_ORTOOLS_ROLLING_ID:
        from fl_op.adapters.ortools_rolling import OrToolsRollingAdapter

        return OrToolsRollingAdapter()
    raise KeyError(f"Unknown adapter id: {adapter_id}")

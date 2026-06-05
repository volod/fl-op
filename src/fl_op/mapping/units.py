"""Canonical unit normalization (spec 14.1 step 4, 15.3 convertUnit).

Source values are normalized to the canonical unit declared in the binding. The
demo's synthetic data is already produced in canonical units, so most conversions
are identity, but the conversion machinery is real so that a contract declaring a
non-canonical source unit (for example engine power in watts) is handled
correctly.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Multiplicative factors converting a known source unit to its canonical unit.
# Keyed by (source_unit, canonical_unit). Identity pairs are implicit.
_CONVERSION_FACTORS: dict[tuple[str, str], float] = {
    ("W", "kW"): 0.001,
    ("kW", "W"): 1000.0,
    ("g", "kg"): 0.001,
    ("kg", "g"): 1000.0,
    ("mL", "L"): 0.001,
    ("L", "mL"): 1000.0,
    ("m2", "ha"): 0.0001,
    ("ha", "m2"): 10000.0,
}


class UnitConversionError(ValueError):
    """Raised when a value cannot be converted to its canonical unit."""


def convert_to_canonical(
    value: float,
    canonical_unit: Optional[str],
    source_unit: Optional[str] = None,
) -> float:
    """Convert a numeric value into its canonical unit.

    When no source unit is declared, the value is assumed to already be in the
    canonical unit (identity). When source and canonical units differ, a known
    conversion factor must exist or UnitConversionError is raised.
    """
    if canonical_unit is None or source_unit is None or source_unit == canonical_unit:
        return float(value)
    factor = _CONVERSION_FACTORS.get((source_unit, canonical_unit))
    if factor is None:
        raise UnitConversionError(
            f"No conversion from '{source_unit}' to '{canonical_unit}'"
        )
    return float(value) * factor

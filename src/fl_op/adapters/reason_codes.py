"""Map legacy solver infeasibility reason strings to canonical ReasonCodes (spec 22.3)."""

from fl_op.canonical.enums import ReasonCode

_LEGACY_TO_CANONICAL: dict[str, ReasonCode] = {
    "no_compatible_vehicle_implement_pair": ReasonCode.NO_COMPATIBLE_BUNDLE,
    "prize_collecting_unserved": ReasonCode.OPTIMIZATION_TRADEOFF,
    "solver_timeout": ReasonCode.OPTIMIZATION_TRADEOFF,
    "worker_crash": ReasonCode.UNKNOWN,
    "unknown": ReasonCode.UNKNOWN,
}


def to_reason_code(legacy_reason: str) -> ReasonCode:
    """Translate a legacy reason string to its normalized ReasonCode."""
    return _LEGACY_TO_CANONICAL.get(legacy_reason, ReasonCode.UNKNOWN)

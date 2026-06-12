"""Source-record value coercion driven by binding metadata.

Coercion is purely a function of the declared `quantityKind`, so adding a new
bound field requires no code change here.
"""

import ast
import logging
from datetime import datetime
from typing import Any

from fl_op.contracts.xopt import XOptFieldMeta

logger = logging.getLogger(__name__)

_NUMERIC_KINDS = {
    "power", "mass", "volume", "money", "area", "speed", "length",
    "flow-rate", "angle", "ratio", "work", "duration",
}
_INTEGER_KINDS = {"time"}


def parse_list(raw: Any) -> list[Any]:
    """Parse a categorical-set value that may arrive as a stringified list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = ast.literal_eval(raw)
            return list(parsed) if isinstance(parsed, (list, tuple)) else [raw]
        except (ValueError, SyntaxError):
            return [raw]
    return [raw]


def parse_rate_map(raw: Any) -> dict[str, float]:
    """Parse a unit-to-rate map that may arrive as a stringified dict.

    Non-numeric and non-positive rates are dropped: a declared rate must be
    usable as a divisor in duration estimation.
    """
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            logger.warning("Skipping unparseable rate map %r", raw)
            return {}
    if not isinstance(raw, dict):
        return {}
    rates: dict[str, float] = {}
    for unit, rate in raw.items():
        try:
            value = float(rate)
        except (TypeError, ValueError):
            logger.warning("Skipping non-numeric rate %r for unit %r", rate, unit)
            continue
        if value > 0:
            rates[str(unit)] = value
    return rates


def parse_timestamp(raw: Any) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'."""
    if isinstance(raw, datetime):
        return raw
    text = str(raw).replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def coerce_value(meta: XOptFieldMeta, raw: Any) -> Any:
    """Coerce a raw source value into its canonical Python type per quantityKind."""
    kind = meta.quantity_kind
    if kind == "categorical-set":
        return parse_list(raw)
    if kind == "interval-set":
        return parse_list(raw)
    if kind == "rate-map":
        return parse_rate_map(raw)
    if kind == "timestamp":
        return parse_timestamp(raw)
    if kind in _INTEGER_KINDS:
        return int(float(raw))
    if kind in _NUMERIC_KINDS:
        return float(raw)
    return raw

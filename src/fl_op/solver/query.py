"""Query-contract helpers: time-window indexing and conflict risk assessment.

No OR-Tools solver call. These pure helpers are used by query_pipeline.py.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

_CONFLICT_RISK_HIGH_THRESHOLD = 3  # overlapping windows needed for 'high' risk
_QUERY_DEADLINE_FALLBACK_DAYS = 7


class TimeWindow(NamedTuple):
    start: str  # ISO-8601
    end: str  # ISO-8601
    task_id: str


def _build_vehicle_time_index(
    dispatch_packages: list[dict[str, Any]],
) -> dict[str, list[TimeWindow]]:
    """Build {vehicle_id: [TimeWindow, ...]} from schedule for O(1) conflict lookup."""
    index: dict[str, list[TimeWindow]] = {}
    for dp in dispatch_packages:
        vid = dp.get("prime_asset_id", "")
        tw = TimeWindow(
            start=dp.get("scheduled_start", ""),
            end=dp.get("scheduled_end", ""),
            task_id=dp.get("task_id", ""),
        )
        index.setdefault(vid, []).append(tw)
    return index


def _windows_overlap(start1: str, end1: str, start2: str, end2: str) -> bool:
    """Return True if two ISO-8601 time windows overlap (exclusive endpoint test)."""
    try:
        s1 = datetime.fromisoformat(start1)
        e1 = datetime.fromisoformat(end1)
        s2 = datetime.fromisoformat(start2)
        e2 = datetime.fromisoformat(end2)
        return s1 < e2 and s2 < e1
    except (ValueError, TypeError):
        return False


def _compute_conflict_risk(
    vehicle_id: str,
    new_start: str,
    new_end: str,
    time_index: dict[str, list[TimeWindow]],
) -> str:
    """Return 'low', 'medium', or 'high' conflict risk string."""
    windows = time_index.get(vehicle_id, [])
    if not windows:
        return "low"
    overlapping = sum(
        1 for tw in windows if _windows_overlap(new_start, new_end, tw.start, tw.end)
    )
    if overlapping == 0:
        return "low"
    if overlapping < _CONFLICT_RISK_HIGH_THRESHOLD:
        return "medium"
    return "high"


def _estimate_operation_window(order: dict[str, Any]) -> tuple[str, str]:
    """Estimate start/end for the new order based on 'now' and deadline."""
    now = datetime.now(tz=timezone.utc)
    deadline_str = order.get("deadline", "")
    try:
        deadline = datetime.fromisoformat(deadline_str)
    except (ValueError, TypeError):
        deadline = now + timedelta(days=_QUERY_DEADLINE_FALLBACK_DAYS)
    return now.isoformat(), deadline.isoformat()

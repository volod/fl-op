"""Location restriction semantics: restricted zones and time-restricted areas.

Two structural restrictions a location may declare (canonical bindings
``location.restrictedOperations`` and ``location.restrictionWindows``):

- A restricted zone prohibits specific operation types at the location; a
  task demanding a prohibited operation there can never be served.
- A time-restricted area prohibits *starting* execution during declared
  intervals (curfew, protection period). The routing model removes those
  intervals from the task's allowed start range; this module pre-filters the
  tasks whose entire feasible range is blocked.

Like workable time windows, these are data semantics applied whenever the
projected rows carry values; ``enforcement.py`` stays profile-driven.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from fl_op.canonical.enums import ReasonCode
from fl_op.core.constants import ROUTING_HORIZON_S
from fl_op.solver.enforcement import ops_set
from fl_op.solver.task_relations import parse_time_windows

logger = logging.getLogger(__name__)

# Closed integer interval [start, end] in epoch seconds or horizon offsets.
Interval = tuple[int, int]


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping or adjacent closed intervals into a sorted minimal set."""
    merged: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] >= i[0]):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(allowed: list[Interval], blocked: list[Interval]) -> list[Interval]:
    """Remove every blocked interval from the allowed set (closed intervals)."""
    result = merge_intervals(allowed)
    for b_start, b_end in merge_intervals(blocked):
        next_result: list[Interval] = []
        for a_start, a_end in result:
            if b_end < a_start or b_start > a_end:
                next_result.append((a_start, a_end))
                continue
            if a_start < b_start:
                next_result.append((a_start, b_start - 1))
            if b_end < a_end:
                next_result.append((b_end + 1, a_end))
        result = next_result
    return result


def _epoch_intervals(
    raw_windows: Any,
    clamp_start: int,
    clamp_end: int,
) -> list[Interval]:
    """Parse ISO "from/to" windows into clamped closed epoch-second intervals."""
    intervals: list[Interval] = []
    for start, end in parse_time_windows(raw_windows):
        start_s = max(clamp_start, int(start.timestamp()))
        end_s = clamp_end if end is None else min(clamp_end, int(end.timestamp()))
        if end_s >= start_s:
            intervals.append((start_s, end_s))
    return intervals


def allowed_start_intervals(
    order: Any,
    site: Optional[Any],
    now_epoch: int,
    deadline_epoch: int,
) -> list[Interval]:
    """Closed epoch-second intervals in which the task execution may start.

    The base range is [now, deadline], narrowed to the task's workable windows
    when declared, minus the site's restriction windows. An empty result means
    no admissible start exists.
    """
    base: list[Interval] = [(now_epoch, deadline_epoch)]
    workable = _epoch_intervals(order.time_windows, now_epoch, deadline_epoch)
    if parse_time_windows(order.time_windows):
        base = workable
    blocked = (
        _epoch_intervals(site.restriction_windows, now_epoch, deadline_epoch)
        if site is not None
        else []
    )
    return subtract_intervals(base, blocked)


def _deadline_epoch(order: Any, now_epoch: int) -> int:
    try:
        return int(datetime.fromisoformat(str(order.deadline)).timestamp())
    except (ValueError, TypeError):
        return now_epoch + ROUTING_HORIZON_S


def apply_location_restrictions(
    orders: list[Any],
    sites: list[Any],
    now: datetime,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Split off tasks blocked by their location's declared restrictions.

    A task is excluded when its operation type is prohibited at its location
    (restricted zone), or when the location's restriction windows block every
    admissible start in [now, deadline] (time-restricted area).
    """
    site_map = {s.location_id: s for s in sites}
    now_epoch = int(now.timestamp())
    kept: list[Any] = []
    infeasible: list[dict[str, Any]] = []
    for order in orders:
        site = site_map.get(order.location_ref)
        if site is None:
            kept.append(order)
            continue
        prohibited = ops_set(site.restricted_operations)
        if order.operation_type in prohibited:
            infeasible.append(
                {
                    "task_id": order.task_id,
                    "cluster_id": "",
                    "reason_code": ReasonCode.RESTRICTED_ZONE.value,
                    "detail": (
                        f"operation {order.operation_type} prohibited at "
                        f"{order.location_ref}"
                    ),
                }
            )
            continue
        if not allowed_start_intervals(
            order, site, now_epoch, _deadline_epoch(order, now_epoch)
        ):
            infeasible.append(
                {
                    "task_id": order.task_id,
                    "cluster_id": "",
                    "reason_code": ReasonCode.RESTRICTED_ZONE.value,
                    "detail": (
                        f"restriction windows at {order.location_ref} block "
                        "every admissible start before the deadline"
                    ),
                }
            )
            continue
        kept.append(order)
    if infeasible:
        logger.info("Location restrictions excluded %d tasks", len(infeasible))
    return kept, infeasible

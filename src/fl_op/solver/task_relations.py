"""Structural task-relation semantics: workable time windows and precedence.

These are data semantics of the canonical task entity (bindings
``task.timeWindows`` and ``task.dependsOnTaskRef``), not profile-declared
constraints, so the solver chain applies them whenever the projected rows
carry values; ``enforcement.py`` stays profile-driven.

Precedence semantics: a task whose ``depends_on_task_ref`` names a task that
is part of the planning input must be served after it, or not at all. A
reference to a task absent from the input is treated as already satisfied
(the predecessor completed in an earlier revision).
"""

import ast
import logging
from datetime import datetime
from typing import Any, Optional

from fl_op.canonical.enums import ReasonCode

logger = logging.getLogger(__name__)

# A parsed workable window: (start, end); end None means open-ended.
TimeWindow = tuple[datetime, Optional[datetime]]


def _as_list(raw: Any) -> list[Any]:
    """Accept a list or a stringified list (CSV physical sources)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            parsed = ast.literal_eval(raw)
            return list(parsed) if isinstance(parsed, (list, tuple)) else [raw]
        except (ValueError, SyntaxError):
            return [raw]
    return list(raw)


def parse_time_windows(raw: Any) -> list[TimeWindow]:
    """Parse a task row's workable windows into (start, end) datetime pairs.

    Items are ISO-8601 "from/to" interval strings; malformed windows are
    skipped so one bad window cannot make a task unschedulable.
    """
    windows: list[TimeWindow] = []
    for item in _as_list(raw):
        parts = str(item).split("/", 1)
        if len(parts) != 2 or not parts[0]:
            continue
        try:
            start = datetime.fromisoformat(parts[0])
        except ValueError:
            logger.warning("Skipping malformed time window %r", item)
            continue
        end: Optional[datetime] = None
        if parts[1]:
            try:
                end = datetime.fromisoformat(parts[1])
            except ValueError:
                logger.warning("Skipping malformed time window %r", item)
                continue
        windows.append((start, end))
    return windows


def _parse_deadline(raw: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def apply_time_window_filter(
    orders: list[Any],
    now: datetime,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Split off tasks none of whose workable windows can still be met.

    A task with no declared windows passes through. A window is usable when it
    has not fully elapsed and does not open after the task deadline.
    """
    kept: list[Any] = []
    infeasible: list[dict[str, Any]] = []
    for order in orders:
        windows = parse_time_windows(order.time_windows)
        if not windows:
            kept.append(order)
            continue
        deadline = _parse_deadline(order.deadline)
        usable = any(
            (end is None or end > now)
            and (deadline is None or start <= deadline)
            for start, end in windows
        )
        if usable:
            kept.append(order)
            continue
        infeasible.append(
            {
                "task_id": order.task_id,
                "cluster_id": "",
                "reason_code": ReasonCode.CONTRACT_WINDOW_INFEASIBLE.value,
                "detail": "no workable time window overlaps [now, deadline]",
            }
        )
    if infeasible:
        logger.info("Time-window filter excluded %d tasks", len(infeasible))
    return kept, infeasible


def apply_dependency_filter(
    orders: list[Any],
    excluded_task_ids: set[str],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Cascade-exclude tasks whose predecessor was excluded before solving.

    ``excluded_task_ids`` are tasks dropped by earlier filters (weather, time
    windows). Dependents of those tasks - transitively - cannot be served in
    this plan and are excluded with PREDECESSOR_UNSERVED.
    """
    kept = list(orders)
    infeasible: list[dict[str, Any]] = []
    excluded = set(excluded_task_ids)
    changed = True
    while changed:
        changed = False
        remaining: list[Any] = []
        for order in kept:
            predecessor = str(order.depends_on_task_ref or "")
            if predecessor and predecessor in excluded:
                infeasible.append(
                    {
                        "task_id": order.task_id,
                        "cluster_id": "",
                        "reason_code": ReasonCode.PREDECESSOR_UNSERVED.value,
                        "detail": f"predecessor {predecessor} excluded before solve",
                    }
                )
                excluded.add(order.task_id)
                changed = True
            else:
                remaining.append(order)
        kept = remaining
    if infeasible:
        logger.info("Dependency filter excluded %d dependent tasks", len(infeasible))
    return kept, infeasible


def enforce_dependency_outcomes(
    dispatch: list[dict[str, Any]],
    infeasible: list[dict[str, Any]],
    orders: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Post-solve safety net: a dependent may not outlive an unserved predecessor.

    Same-cluster chains are already ordered inside the routing model; this
    catches chains split across clusters or predecessors lost to per-cluster
    enforcement, removing the dependent dispatch with PREDECESSOR_UNSERVED.
    """
    present = {o.task_id for o in orders}
    predecessor_of = {
        o.task_id: str(o.depends_on_task_ref or "") for o in orders
    }
    out_dispatch = list(dispatch)
    extra: list[dict[str, Any]] = []
    while True:
        served = {d["task_id"] for d in out_dispatch}
        violating = [
            d
            for d in out_dispatch
            if predecessor_of.get(d["task_id"], "")
            and predecessor_of[d["task_id"]] in present
            and predecessor_of[d["task_id"]] not in served
        ]
        if not violating:
            break
        for record in violating:
            out_dispatch.remove(record)
            extra.append(
                {
                    "task_id": record["task_id"],
                    "cluster_id": record.get("cluster_id", ""),
                    "reason_code": ReasonCode.PREDECESSOR_UNSERVED.value,
                    "detail": (
                        f"predecessor {predecessor_of[record['task_id']]} "
                        "not served in this plan"
                    ),
                }
            )
    if extra:
        logger.info(
            "Dependency outcome enforcement withdrew %d dependent dispatches",
            len(extra),
        )
    return out_dispatch, [*infeasible, *extra]

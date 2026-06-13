"""Watermark-driven replan triggering.

A published plan carries the visibility horizon it was solved against
(``plan.source_watermarks``). Comparing it with the watermarks of a snapshot
built from the data visible *now* tells whether the world moved past the
plan: any source whose current watermark exceeds the plan's (or that the
plan never saw) makes the plan stale, and a corrective replan can be forced
automatically instead of waiting for an operator to notice.
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fl_op.canonical.plan import Plan
    from fl_op.canonical.snapshot import PlanningSnapshot

logger = logging.getLogger(__name__)


def newly_visible_sources(
    plan: "Plan", snapshot: "PlanningSnapshot"
) -> dict[str, dict[str, Any]]:
    """Sources whose visible data passed the plan's watermark.

    Returns {contract_id: {"plan": iso-or-None, "visible": iso}} for every
    source contract the current snapshot has seen beyond the plan's horizon;
    empty means the plan still covers everything visible.
    """
    newly: dict[str, dict[str, Any]] = {}
    for contract_id, visible in snapshot.source_watermarks.items():
        planned = plan.source_watermarks.get(contract_id)
        if planned is None or visible > planned:
            newly[contract_id] = {
                "plan": planned.isoformat() if planned else None,
                "visible": visible.isoformat(),
            }
    return newly


def should_replan(plan: "Plan", snapshot: "PlanningSnapshot") -> bool:
    """Whether data visible now lies beyond the plan's watermark horizon."""
    newly = newly_visible_sources(plan, snapshot)
    if newly:
        logger.warning(
            "Plan %s/%s is stale: newer data visible for %s",
            plan.plan_id,
            plan.revision_id,
            sorted(newly),
        )
    return bool(newly)

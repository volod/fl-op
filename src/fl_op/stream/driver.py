"""Stream driver: events -> rolling snapshot rebuilds -> immutable plan revisions.

Each replanning-trigger event mutates the in-memory source state, a fresh rolling
snapshot is built, and the rolling adapter produces a new immutable revision
linked to its parent. This is the near-real-time analogue of the periodic batch
flow, kept fully Python-native.
"""

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.adapters.ortools_rolling import OrToolsRollingAdapter
from fl_op.canonical.enums import PlanningMode
from fl_op.canonical.plan import Plan
from fl_op.contracts.registry import FileRegistry
from fl_op.models.enums import OrderStatus
from fl_op.snapshot.builder import SnapshotBuilder
from fl_op.stream.source import ExecutionEvent

logger = logging.getLogger(__name__)


@dataclass
class Revision:
    """One rolling revision plus the triggering event."""

    event: Optional[ExecutionEvent]
    plan: Plan
    snapshot_id: str


@dataclass
class StreamResult:
    revisions: list[Revision] = field(default_factory=list)


def _apply_event(sources: dict[str, list[dict[str, Any]]], event: ExecutionEvent) -> None:
    """Mutate the in-memory source rows in response to one execution event."""
    if event.event_type == "task.started":
        for o in sources["orders"]:
            if o["order_id"] == event.entity_ref:
                o["status"] = OrderStatus.STARTED.value
    elif event.event_type == "order.cancelled":
        sources["orders"] = [
            o for o in sources["orders"] if o["order_id"] != event.entity_ref
        ]
    elif event.event_type == "order.created":
        sources["orders"].append(dict(event.payload))
    elif event.event_type == "asset.unavailable":
        sources["vehicles"] = [
            v for v in sources["vehicles"] if v["vehicle_id"] != event.entity_ref
        ]
    elif event.event_type == "forecast.updated":
        # Structural no-op for the MVP: triggers a replan without changing inputs.
        logger.info("forecast.updated for %s triggers replan", event.entity_ref)


class StreamDriver:
    """Drives rolling replanning from an execution-event stream."""

    def __init__(
        self,
        registry: Optional[FileRegistry] = None,
        adapter: Optional[OrToolsRollingAdapter] = None,
    ) -> None:
        self.registry = registry or FileRegistry()
        self.builder = SnapshotBuilder(self.registry)
        self.adapter = adapter or OrToolsRollingAdapter()
        self.profile = self.registry.get_profile("agricultural-custom-services")

    def initial_revision(
        self,
        sources: dict[str, list[dict[str, Any]]],
        effective_at: Optional[datetime] = None,
    ) -> Revision:
        """Build the baseline rolling revision before any events are applied."""
        effective_at = effective_at or datetime.now(tz=timezone.utc)
        snapshot = self.builder.build_from_sources(
            sources, PlanningMode.ROLLING, effective_at, lineage_ref="stream://initial"
        )
        plan = self.adapter.plan(snapshot, self.profile, {"now": effective_at})
        return Revision(event=None, plan=plan, snapshot_id=snapshot.snapshot_id)

    def run(
        self,
        sources: dict[str, list[dict[str, Any]]],
        events: list[ExecutionEvent],
        effective_at: Optional[datetime] = None,
    ) -> StreamResult:
        """Produce a baseline revision plus one revision per triggering event."""
        effective_at = effective_at or datetime.now(tz=timezone.utc)
        working = copy.deepcopy(sources)

        result = StreamResult()
        baseline = self.initial_revision(working, effective_at)
        result.revisions.append(baseline)
        previous_plan = baseline.plan

        for event in events:
            _apply_event(working, event)
            now = _event_now(event, effective_at)
            snapshot = self.builder.build_from_sources(
                working, PlanningMode.ROLLING, effective_at,
                lineage_ref=f"stream://{event.event_id}",
            )
            plan = self.adapter.plan(
                snapshot,
                self.profile,
                {"now": now, "previous_plan": previous_plan},
            )
            result.revisions.append(
                Revision(event=event, plan=plan, snapshot_id=snapshot.snapshot_id)
            )
            previous_plan = plan

        logger.info("Stream produced %d revisions", len(result.revisions))
        return result


def _event_now(event: ExecutionEvent, fallback: datetime) -> datetime:
    if event.observed_at:
        try:
            return datetime.fromisoformat(event.observed_at.replace("Z", "+00:00"))
        except ValueError:
            pass
    return fallback

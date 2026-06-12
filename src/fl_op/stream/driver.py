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
from fl_op.core.constants import STREAM_CONVERGENCE_WINDOW_S
from fl_op.snapshot.builder import SnapshotBuilder
from fl_op.stream.apply import EventApplicator
from fl_op.stream.prognosis import (
    log_threshold_recommendations,
    prognosis_accuracy,
    record_prognosis_outcomes,
)
from fl_op.stream.source import ExecutionEvent

logger = logging.getLogger(__name__)


@dataclass
class Revision:
    """One rolling revision plus the triggering event.

    When events are coalesced (convergence window), ``event`` is the last
    event of the converged batch and ``n_coalesced_events`` its size.
    ``applied_event_ids`` lists every event id the revision's rebuild
    applied; after publication they go into the durable dedup store so a
    broker redelivery never produces a duplicate revision.
    """

    event: Optional[ExecutionEvent]
    plan: Plan
    snapshot_id: str
    n_coalesced_events: int = 1
    applied_event_ids: list[str] = field(default_factory=list)


@dataclass
class StreamResult:
    revisions: list[Revision] = field(default_factory=list)


class StreamDriver:
    """Drives rolling replanning from an execution-event stream.

    Event application is binding-driven (see stream/apply.py): collections and
    key columns come from the active domain's mapping documents, so the driver
    has no knowledge of domain-specific physical column names.
    """

    def __init__(
        self,
        registry: Optional[FileRegistry] = None,
        adapter: Optional[OrToolsRollingAdapter] = None,
        dedup_store: Optional[Any] = None,
    ) -> None:
        self.registry = registry or FileRegistry()
        self.builder = SnapshotBuilder(self.registry)
        self.adapter = adapter or OrToolsRollingAdapter()
        self.applicator = EventApplicator(self.registry, dedup_store)
        profile_id = self.registry.active_profile_id
        if profile_id is None:
            raise ValueError("Registry declares no active domain profile")
        self.profile = self.registry.get_profile(profile_id)

    def initial_revision(
        self,
        sources: dict[str, list[dict[str, Any]]],
        effective_at: Optional[datetime] = None,
    ) -> Revision:
        """Build the baseline rolling revision before any events are applied."""
        revision, _ = self._baseline(sources, effective_at or datetime.now(tz=timezone.utc))
        return revision

    def _baseline(
        self,
        sources: dict[str, list[dict[str, Any]]],
        effective_at: datetime,
    ) -> tuple[Revision, dict[str, str]]:
        snapshot = self.builder.build_from_sources(
            sources, PlanningMode.ROLLING, effective_at, lineage_ref="stream://initial"
        )
        plan = self.adapter.plan(snapshot, self.profile, {"now": effective_at})
        revision = Revision(event=None, plan=plan, snapshot_id=snapshot.snapshot_id)
        return revision, _service_reasons(snapshot)

    def run(
        self,
        sources: dict[str, list[dict[str, Any]]],
        events: list[ExecutionEvent],
        effective_at: Optional[datetime] = None,
        convergence_window_s: float = STREAM_CONVERGENCE_WINDOW_S,
    ) -> StreamResult:
        """Produce a baseline revision plus one revision per converged batch.

        Events whose observed times fall within ``convergence_window_s`` of
        each other are coalesced into one rebuild/re-solve, so a partition
        flushing its backlog converges before replanning. With the window at 0
        every event yields its own revision. Replayed event ids are applied
        idempotently and never produce a revision.
        """
        effective_at = effective_at or datetime.now(tz=timezone.utc)
        working = copy.deepcopy(sources)

        result = StreamResult()
        baseline, previous_service_reasons = self._baseline(working, effective_at)
        result.revisions.append(baseline)
        previous_plan = baseline.plan

        for batch in _coalesce(events, convergence_window_s, effective_at):
            applied = [self.applicator.apply(working, event) for event in batch]
            # Completions captured by this batch are measured against the
            # plan the tasks were executing under (the previous revision).
            if self.applicator.completions:
                from fl_op.stream.lead_time import record_completions

                record_completions(self.applicator.completions, previous_plan)
                self.applicator.completions = []
            if not any(applied):
                continue
            applied_event_ids = [
                event.event_id
                for event, was_applied in zip(batch, applied)
                if was_applied and event.event_id
            ]
            last_event = batch[-1]
            now = _event_now(last_event, effective_at)
            snapshot = self.builder.build_from_sources(
                working, PlanningMode.ROLLING, effective_at,
                lineage_ref=f"stream://{last_event.event_id}",
                source_watermarks=dict(self.applicator.watermarks),
            )
            plan = self.adapter.plan(
                snapshot,
                self.profile,
                {
                    "now": now,
                    "previous_plan": previous_plan,
                    "previous_service_reasons": previous_service_reasons,
                },
            )
            result.revisions.append(
                Revision(
                    event=last_event,
                    plan=plan,
                    snapshot_id=snapshot.snapshot_id,
                    n_coalesced_events=sum(applied),
                    applied_event_ids=applied_event_ids,
                )
            )
            record_prognosis_outcomes(plan)
            previous_plan = plan
            previous_service_reasons = _service_reasons(snapshot)

        accuracy = prognosis_accuracy()
        log_threshold_recommendations(accuracy)
        from fl_op.stream.lead_time import lead_time_stats

        stats = lead_time_stats()
        if stats:
            logger.info("Completion lead times: %s", stats)
        from fl_op.core import constants as _constants

        if _constants.MONITORING_AUTO_TUNE_ENABLED:
            from fl_op.snapshot.policy_tuning import auto_tune_monitoring_policy

            auto_tune_monitoring_policy(accuracy, self.builder.monitoring_policy)
        logger.info("Stream produced %d revisions", len(result.revisions))
        return result


def _coalesce(
    events: list[ExecutionEvent],
    window_s: float,
    fallback: datetime,
) -> list[list[ExecutionEvent]]:
    """Group consecutive events whose observed times lie within the window."""
    if window_s <= 0 or not events:
        return [[event] for event in events]
    batches: list[list[ExecutionEvent]] = [[events[0]]]
    for event in events[1:]:
        gap = (_event_now(event, fallback) - _event_now(batches[-1][-1], fallback)).total_seconds()
        if abs(gap) <= window_s:
            batches[-1].append(event)
        else:
            batches.append([event])
    return batches


def _service_reasons(snapshot: Any) -> dict[str, str]:
    """Monitoring-derived task reasons, for the next revision's reconciliation."""
    from fl_op.adapters.rolling.corrective import service_task_reasons

    return service_task_reasons(snapshot)


def _event_now(event: ExecutionEvent, fallback: datetime) -> datetime:
    if event.observed_at:
        try:
            return datetime.fromisoformat(event.observed_at.replace("Z", "+00:00"))
        except ValueError:
            pass
    return fallback

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
from typing import Any, Callable, Optional

from fl_op.adapters.ortools_rolling import OrToolsRollingAdapter
from fl_op.canonical.enums import PlanningMode
from fl_op.canonical.plan import Plan
from fl_op.contracts.registry import FileRegistry
from fl_op.core.constants import OBJECTIVE_MODE_COST, STREAM_CONVERGENCE_WINDOW_S
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

    def _profile_for_builder(self):
        profile_id = self.builder.profile_id or self.registry.active_profile_id
        if profile_id is None:
            raise ValueError("Registry declares no active domain profile")
        if self.profile.metadata.id != profile_id:
            self.profile = self.registry.get_profile(profile_id)
        return self.profile

    def initial_revision(
        self,
        sources: dict[str, list[dict[str, Any]]],
        effective_at: Optional[datetime] = None,
        objective: str = OBJECTIVE_MODE_COST,
    ) -> Revision:
        """Build the baseline rolling revision before any events are applied."""
        revision, _ = self._baseline(
            sources,
            effective_at or datetime.now(tz=timezone.utc),
            objective,
        )
        return revision

    def _baseline(
        self,
        sources: dict[str, list[dict[str, Any]]],
        effective_at: datetime,
        objective: str = OBJECTIVE_MODE_COST,
    ) -> tuple[Revision, dict[str, str]]:
        snapshot = self.builder.build_from_sources(
            sources, PlanningMode.ROLLING, effective_at, lineage_ref="stream://initial"
        )
        plan = self.adapter.plan(
            snapshot,
            self._profile_for_builder(),
            {"now": effective_at, "objective": objective},
        )
        revision = Revision(event=None, plan=plan, snapshot_id=snapshot.snapshot_id)
        return revision, _service_reasons(snapshot)

    def session(
        self,
        sources: dict[str, list[dict[str, Any]]],
        effective_at: Optional[datetime] = None,
        objective: str = OBJECTIVE_MODE_COST,
    ) -> "StreamSession":
        """Open a stateful session that survives many drain cycles.

        The session owns the mutable working copy, the rolling continuity
        (previous plan and service reasons) and the accumulated watermarks, so
        a continuous watcher can drain successive bounded batches from a broker
        without rebuilding the baseline each cycle.
        """
        return StreamSession(
            self,
            sources,
            effective_at or datetime.now(tz=timezone.utc),
            objective,
        )

    def run(
        self,
        sources: dict[str, list[dict[str, Any]]],
        events: list[ExecutionEvent],
        effective_at: Optional[datetime] = None,
        convergence_window_s: float = STREAM_CONVERGENCE_WINDOW_S,
        objective: str = OBJECTIVE_MODE_COST,
        on_revision: Optional[Callable[["Revision"], None]] = None,
    ) -> StreamResult:
        """Produce a baseline revision plus one revision per converged batch.

        Events whose observed times fall within ``convergence_window_s`` of
        each other are coalesced into one rebuild/re-solve, so a partition
        flushing its backlog converges before replanning. With the window at 0
        every event yields its own revision. Replayed event ids are applied
        idempotently and never produce a revision.

        ``on_revision`` is invoked once per published event-driven revision
        (not the baseline) so callers can persist artifacts and advance broker
        offsets behind each revision instead of only at the end of the stream.
        """
        session = self.session(sources, effective_at, objective)
        result = StreamResult()
        result.revisions.append(session.start())
        result.revisions.extend(
            session.drain(
                events,
                convergence_window_s=convergence_window_s,
                on_revision=on_revision,
            )
        )
        session.finalize()
        logger.info("Stream produced %d revisions", len(result.revisions))
        return result


class StreamSession:
    """Mutable rolling-planning state spanning one or more drain cycles.

    Built via :meth:`StreamDriver.session`. ``start`` produces the baseline
    revision; ``drain`` applies a bounded batch of events and yields the
    resulting revisions; ``finalize`` runs the accuracy/lead-time/auto-tune
    housekeeping. A periodic run calls all three once; a continuous watcher
    calls ``start`` once and then loops ``drain`` (+ ``finalize`` per cycle).
    """

    def __init__(
        self,
        driver: "StreamDriver",
        sources: dict[str, list[dict[str, Any]]],
        effective_at: datetime,
        objective: str,
    ) -> None:
        self._driver = driver
        self.effective_at = effective_at
        self.objective = objective
        self.working = copy.deepcopy(sources)
        self.previous_plan: Optional[Plan] = None
        self.previous_service_reasons: dict[str, str] = {}

    def start(self) -> Revision:
        """Build and remember the baseline revision (no events applied yet)."""
        baseline, reasons = self._driver._baseline(
            self.working, self.effective_at, self.objective
        )
        self.previous_plan = baseline.plan
        self.previous_service_reasons = reasons
        return baseline

    def drain(
        self,
        events: list[ExecutionEvent],
        convergence_window_s: float = STREAM_CONVERGENCE_WINDOW_S,
        on_revision: Optional[Callable[["Revision"], None]] = None,
    ) -> list[Revision]:
        """Apply one bounded batch of events; return its event-driven revisions.

        ``on_revision`` is called as each revision is produced so a continuous
        watcher can publish artifacts and record dedup ids before the cycle's
        offsets are committed, bounding crash redelivery to a single cycle.
        """
        if self.previous_plan is None:
            raise RuntimeError("StreamSession.start() must run before drain()")
        driver = self._driver
        revisions: list[Revision] = []
        for batch in _coalesce(events, convergence_window_s, self.effective_at):
            applied = [driver.applicator.apply(self.working, event) for event in batch]
            # Completions captured by this batch are measured against the
            # plan the tasks were executing under (the previous revision).
            if driver.applicator.completions:
                from fl_op.stream.lead_time import record_completions

                record_completions(driver.applicator.completions, self.previous_plan)
                driver.applicator.completions = []
            if not any(applied):
                continue
            applied_event_ids = [
                event.event_id
                for event, was_applied in zip(batch, applied)
                if was_applied and event.event_id
            ]
            last_event = batch[-1]
            now = _event_now(last_event, self.effective_at)
            snapshot = driver.builder.build_from_sources(
                self.working, PlanningMode.ROLLING, self.effective_at,
                lineage_ref=f"stream://{last_event.event_id}",
                source_watermarks=dict(driver.applicator.watermarks),
            )
            plan = driver.adapter.plan(
                snapshot,
                driver._profile_for_builder(),
                {
                    "now": now,
                    "previous_plan": self.previous_plan,
                    "previous_service_reasons": self.previous_service_reasons,
                    "objective": self.objective,
                },
            )
            revision = Revision(
                event=last_event,
                plan=plan,
                snapshot_id=snapshot.snapshot_id,
                n_coalesced_events=sum(applied),
                applied_event_ids=applied_event_ids,
            )
            revisions.append(revision)
            record_prognosis_outcomes(plan)
            self.previous_plan = plan
            self.previous_service_reasons = _service_reasons(snapshot)
            if on_revision is not None:
                on_revision(revision)
        return revisions

    def finalize(self) -> None:
        """Log accuracy/lead-time stats and auto-tune the monitoring policy."""
        accuracy = prognosis_accuracy()
        log_threshold_recommendations(accuracy)
        from fl_op.stream.lead_time import lead_time_stats

        stats = lead_time_stats()
        if stats:
            logger.info("Completion lead times: %s", stats)
        from fl_op.core import constants as _constants

        if _constants.MONITORING_AUTO_TUNE_ENABLED:
            from fl_op.snapshot.policy_tuning import auto_tune_monitoring_policy

            auto_tune_monitoring_policy(accuracy, self._driver.builder.monitoring_policy)


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

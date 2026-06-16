"""Binding-driven application of execution events to in-memory source rows.

The stream driver keeps raw physical source rows (keyed by contract id) and
mutates them per event before rebuilding a rolling snapshot. Which collection an
event touches and which physical column identifies a row are resolved from the
registry's mapping documents (canonical entity + identity binding), so the same
event vocabulary works for any domain pack without hardcoded column names.
"""

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from fl_op.canonical.enums import TaskStatus
from fl_op.contracts.registry import FileRegistry
from fl_op.core.constants import (
    COVERAGE_COMPLETE_FRACTION,
    METRIC_WORK_PROGRESS,
    WORK_PROGRESS_COMPLETE_PCT,
)
from fl_op.mapping.bindings import BindingTable, load_binding_table
from fl_op.stream.coverage import (
    coverage_state,
    has_coverage_payload,
    pass_ring_from_payload,
)
from fl_op.stream.source import (
    EVENT_ASSET_UNAVAILABLE,
    EVENT_ENTITY_CORRECTED,
    EVENT_FORECAST_UPDATED,
    EVENT_INVENTORY_ADJUSTED,
    EVENT_OBSERVATION_RECORDED,
    EVENT_ORDER_CANCELLED,
    EVENT_ORDER_CREATED,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_PROGRESS,
    EVENT_TASK_STARTED,
    ExecutionEvent,
)

logger = logging.getLogger(__name__)

# Binding paths resolved against each contract's mapping document. The generic
# work quantity is preferred; the service area is its legacy alias (both may
# resolve to the same physical column).
_TASK_STATUS_BINDING = "task.status"
_TASK_DEADLINE_BINDING = "task.deadline"
_TASK_WORK_QUANTITY_BINDINGS = ("task.workQuantity", "task.areaHa")
# Area reference for spatially-derived coverage fractions (covered geodesic
# area vs the task's original area in hectares).
_TASK_AREA_BINDING = "task.areaHa"

# Observation bindings consulted for telemetry-derived task progress.
_OBSERVATION_METRIC_BINDING = "observation.metric"
_OBSERVATION_VALUE_BINDING = "observation.value"
_OBSERVATION_ENTITY_REF_BINDING = "observation.entityRef"

# task.progress payload fields: the absolute remaining work in the task's
# work-quantity unit (exact, wins when present), or the completed share of the
# task's work in [0, 1].
PAYLOAD_REMAINING_QUANTITY = "remaining_quantity"
PAYLOAD_COMPLETED_FRACTION = "completed_fraction"

Sources = dict[str, list[dict[str, Any]]]


class EventApplicator:
    """Mutates physical source rows in response to canonical execution events."""

    def __init__(
        self,
        registry: Optional[FileRegistry] = None,
        dedup_store: Optional[Any] = None,
    ) -> None:
        self.registry = registry or FileRegistry()
        # Durable event-id store (stream/dedup.py): ids published by earlier
        # runs are suppressed, surviving process restarts. None (the default
        # and the JSONL development path) keeps in-memory idempotency only.
        self._dedup_store = dedup_store
        self._tables: dict[str, BindingTable] = {}
        self._by_entity: dict[str, list[str]] = {}
        # Idempotency: event ids already applied in this run. At-least-once
        # delivery may replay an event; a replay must mutate nothing and
        # produce no revision.
        self._seen_event_ids: set[str] = set()
        # Visibility horizon per event-mutated source contract: the newest
        # applied event's observed time. The same role observation watermarks
        # play for readings, extended to task/asset/location/forecast sources.
        self.watermarks: dict[str, datetime] = {}
        # Completion records captured before finished task rows disappear
        # (task id, completion time, deadline); the driver drains them into
        # the lead-time log after each batch.
        self.completions: list[dict[str, Any]] = []
        # Per-task accumulated coverage passes: task_id -> {rings, originals,
        # original_area_ha, n_passes}. Each covered-geometry pass unions into
        # the prior coverage so the remaining work is refined from the
        # overlap-corrected covered area, not a self-reported scalar.
        self._coverage: dict[str, dict[str, Any]] = {}
        # One record per coverage pass (covered/remaining area, fraction, pass
        # count); the driver drains them into the coverage trail after each
        # batch, the spatial counterpart to the completions/lead-time log.
        self.coverage_reports: list[dict[str, Any]] = []
        for cid in self.registry.list_contracts():
            entry = self.registry.get_entry(cid)
            if not entry.mapping_ref:
                continue
            table = load_binding_table(self.registry, cid)
            if not table.canonical_entity:
                continue
            self._tables[cid] = table
            self._by_entity.setdefault(table.canonical_entity, []).append(cid)

    # -- resolution helpers ----------------------------------------------------

    def _contracts_for(self, entity: str, sources: Sources) -> list[str]:
        """Contract ids of one canonical entity that are present in the sources."""
        return [cid for cid in self._by_entity.get(entity, []) if cid in sources]

    def _key_field(self, contract_id: str) -> Optional[str]:
        return self._tables[contract_id].entity_key_field

    def _status_field(self, contract_id: str) -> Optional[str]:
        binding = self._tables[contract_id].by_binding_path().get(_TASK_STATUS_BINDING)
        return binding.source_field if binding else None

    def _work_fields(self, contract_id: str) -> list[str]:
        """Distinct physical columns carrying the task's work, preferred first.

        Deduplicated by source field: a domain may bind the same column to
        both the generic work quantity and the legacy area alias, and it must
        be scaled only once.
        """
        by_path = self._tables[contract_id].by_binding_path()
        fields: list[str] = []
        for path in _TASK_WORK_QUANTITY_BINDINGS:
            binding = by_path.get(path)
            if binding is not None and binding.source_field not in fields:
                fields.append(binding.source_field)
        return fields

    def _area_field(self, contract_id: str) -> Optional[str]:
        """Physical column carrying the task's work area (the coverage reference)."""
        binding = self._tables[contract_id].by_binding_path().get(_TASK_AREA_BINDING)
        return binding.source_field if binding else None

    # -- event handlers ----------------------------------------------------------

    def _set_task_started(self, sources: Sources, event: ExecutionEvent) -> None:
        for cid in self._contracts_for("task", sources):
            key, status = self._key_field(cid), self._status_field(cid)
            if not key or not status:
                continue
            for row in sources[cid]:
                if str(row.get(key)) == event.entity_ref:
                    row[status] = TaskStatus.STARTED.value

    def _apply_task_progress(self, sources: Sources, event: ExecutionEvent) -> None:
        """Partial task completion: shrink the remaining work quantity.

        A coverage-geometry payload (a covered polygon or a path swept by an
        implement width) wins when present: the pass accumulates into the
        task's covered geometry and the remaining work is derived from the
        overlap-corrected covered area. Otherwise an absolute
        ``remaining_quantity`` payload (in the task's work-quantity unit)
        overwrites the work-quantity column exactly, suiting domains without a
        meaningful completed share, and ``completed_fraction`` scales every
        work-quantity column down to the remaining share. A fully completed
        task is removed (nothing is left to plan). Progress implies the task
        started.
        """
        if has_coverage_payload(event.payload) and self._apply_coverage_pass(
            sources, event.entity_ref, event.payload, event.observed_at
        ):
            return
        remaining = self._payload_float(event, PAYLOAD_REMAINING_QUANTITY)
        fraction = 0.0
        if remaining is None:
            try:
                fraction = float(event.payload.get(PAYLOAD_COMPLETED_FRACTION, 0.0))
            except (TypeError, ValueError):
                logger.warning(
                    "task.progress for %s has unusable %s: %r",
                    event.entity_ref,
                    PAYLOAD_COMPLETED_FRACTION,
                    event.payload.get(PAYLOAD_COMPLETED_FRACTION),
                )
                return
        if (remaining is not None and remaining <= 0.0) or fraction >= 1.0:
            self._complete_task(sources, event.entity_ref, event.observed_at, "progress")
            return
        self._scale_task_work(sources, event.entity_ref, fraction, remaining)

    def _scale_task_work(
        self,
        sources: Sources,
        task_ref: str,
        fraction: float,
        remaining: Optional[float],
    ) -> None:
        """Shrink a task's remaining work (shared by progress and telemetry)."""
        for cid in self._contracts_for("task", sources):
            key = self._key_field(cid)
            status = self._status_field(cid)
            work_fields = self._work_fields(cid)
            if not key or not work_fields:
                continue
            for row in sources[cid]:
                if str(row.get(key)) != task_ref:
                    continue
                if remaining is not None:
                    # Exact remaining work goes to the preferred work-quantity
                    # column only: a legacy area alias bound to a different
                    # column carries a different unit and must not receive it.
                    row[work_fields[0]] = round(remaining, 2)
                else:
                    for work_field in work_fields:
                        try:
                            scaled = float(row.get(work_field, 0.0)) * (1.0 - fraction)
                        except (TypeError, ValueError):
                            logger.warning(
                                "task progress: %s has non-numeric %s",
                                task_ref,
                                work_field,
                            )
                            continue
                        row[work_field] = round(scaled, 2)
                if status:
                    row[status] = TaskStatus.STARTED.value

    def _apply_coverage_pass(
        self,
        sources: Sources,
        task_id: str,
        payload: dict[str, Any],
        observed_at: str,
    ) -> bool:
        """Accumulate one covered-geometry pass and refine the remaining work.

        The pass geometry (an explicit covered polygon or a swept path) unions
        into the task's prior coverage; the overlap-corrected covered area over
        the task's original work area gives the completed fraction. Reaching
        ``COVERAGE_COMPLETE_FRACTION`` finishes the task; otherwise every work
        column is set to its original value times the uncovered share -- derived
        from the cumulative geometry, so overlapping passes never over-credit
        progress. Returns False when the payload carries no usable geometry, so
        the caller falls back to scalar progress.
        """
        ring = pass_ring_from_payload(payload)
        if ring is None:
            return False
        entry = self._coverage.get(task_id)
        if entry is None:
            originals, original_area_ha = self._capture_work_state(sources, task_id)
            if original_area_ha <= 0:
                logger.warning(
                    "coverage pass for %s has no positive work area; ignoring geometry",
                    task_id,
                )
                return False
            entry = {
                "rings": [],
                "originals": originals,
                "original_area_ha": original_area_ha,
                "n_passes": 0,
            }
            self._coverage[task_id] = entry
        entry["rings"].append(ring)
        entry["n_passes"] += 1
        state = coverage_state(entry["rings"], entry["original_area_ha"])
        self.coverage_reports.append(
            {
                "task_id": task_id,
                "observed_at": observed_at,
                "n_passes": entry["n_passes"],
                "original_area_ha": round(entry["original_area_ha"], 4),
                **state,
            }
        )
        if state["covered_fraction"] >= COVERAGE_COMPLETE_FRACTION:
            self._complete_task(sources, task_id, observed_at, "coverage")
            self._coverage.pop(task_id, None)
        else:
            self._set_remaining_from_originals(
                sources, task_id, entry["originals"], state["covered_fraction"]
            )
        return True

    def _capture_work_state(
        self, sources: Sources, task_id: str
    ) -> tuple[dict[str, float], float]:
        """First-pass originals: each work column's value plus the area reference.

        Captured before any scaling so cumulative coverage always reduces from
        the original work, not from an already-shrunk value.
        """
        for cid in self._contracts_for("task", sources):
            key = self._key_field(cid)
            if not key:
                continue
            work_fields = self._work_fields(cid)
            area_field = self._area_field(cid)
            for row in sources[cid]:
                if str(row.get(key)) != task_id:
                    continue
                originals: dict[str, float] = {}
                for field in work_fields:
                    try:
                        originals[field] = float(row.get(field, 0.0) or 0.0)
                    except (TypeError, ValueError):
                        continue
                area_ha = 0.0
                if area_field is not None:
                    try:
                        area_ha = float(row.get(area_field, 0.0) or 0.0)
                    except (TypeError, ValueError):
                        area_ha = 0.0
                if area_ha <= 0 and work_fields:
                    area_ha = originals.get(work_fields[0], 0.0)
                return originals, area_ha
        return {}, 0.0

    def _set_remaining_from_originals(
        self,
        sources: Sources,
        task_id: str,
        originals: dict[str, float],
        covered_fraction: float,
    ) -> None:
        """Set each work column to its original value times the uncovered share."""
        uncovered = max(0.0, 1.0 - covered_fraction)
        for cid in self._contracts_for("task", sources):
            key = self._key_field(cid)
            status = self._status_field(cid)
            if not key:
                continue
            for row in sources[cid]:
                if str(row.get(key)) != task_id:
                    continue
                for field, original in originals.items():
                    if field in row:
                        row[field] = round(original * uncovered, 2)
                if status:
                    row[status] = TaskStatus.STARTED.value

    def _apply_task_completed(self, sources: Sources, event: ExecutionEvent) -> None:
        """Execution finished: the task leaves planning, its outcome recorded."""
        self._complete_task(sources, event.entity_ref, event.observed_at, "event")

    def _complete_task(
        self, sources: Sources, task_ref: str, completed_at: str, via: str
    ) -> None:
        """Remove a finished task, capturing its commitments first.

        The deadline is read off the row before it disappears so the
        completion record can measure how much lead the execution had; the
        driver drains ``self.completions`` into the lead-time log.
        """
        record: dict[str, Any] = {
            "task_id": task_ref,
            "completed_at": completed_at,
            "via": via,
            "deadline": None,
        }
        for cid in self._contracts_for("task", sources):
            key = self._key_field(cid)
            deadline_binding = (
                self._tables[cid].by_binding_path().get(_TASK_DEADLINE_BINDING)
            )
            if not key:
                continue
            for row in sources[cid]:
                if str(row.get(key)) == task_ref:
                    if deadline_binding is not None:
                        record["deadline"] = row.get(deadline_binding.source_field)
                    break
        self.completions.append(record)
        self._remove_by_key(sources, task_ref, "task")

    @staticmethod
    def _payload_float(event: ExecutionEvent, key: str) -> Optional[float]:
        """Parse one optional numeric payload field; None when absent or unusable."""
        if key not in event.payload:
            return None
        try:
            return float(event.payload[key])
        except (TypeError, ValueError):
            logger.warning(
                "task.progress for %s has unusable %s: %r",
                event.entity_ref,
                key,
                event.payload[key],
            )
            return None

    def _remove_by_key(self, sources: Sources, entity_ref: str, entity: str) -> None:
        for cid in self._contracts_for(entity, sources):
            key = self._key_field(cid)
            if not key:
                continue
            sources[cid] = [
                row for row in sources[cid] if str(row.get(key)) != entity_ref
            ]

    def _remove_task(self, sources: Sources, event: ExecutionEvent) -> None:
        self._remove_by_key(sources, event.entity_ref, "task")

    def _remove_asset(self, sources: Sources, event: ExecutionEvent) -> None:
        self._remove_by_key(sources, event.entity_ref, "asset")

    def _append_payload(self, sources: Sources, event: ExecutionEvent, entity: str) -> None:
        """Add (or correct) one row in the entity's source collection.

        Rows are upserted by the contract's key column: a later report with the
        same identifier replaces the earlier one, so out-of-order corrections
        (a re-sent reading with a fixed value) converge instead of duplicating.
        """
        contracts = self._contracts_for(entity, sources)
        if not contracts:
            logger.warning(
                "Event %s targets entity '%s' but no mapped source collection is loaded",
                event.event_type,
                entity,
            )
            return
        payload = dict(event.payload)
        contract_id = contracts[0]
        key = self._key_field(contract_id)
        if key and key in payload:
            rows = sources[contract_id]
            for i, row in enumerate(rows):
                if str(row.get(key)) == str(payload[key]):
                    rows[i] = payload
                    return
        sources[contract_id].append(payload)

    def _append_task(self, sources: Sources, event: ExecutionEvent) -> None:
        self._append_payload(sources, event, "task")

    def _append_observation(self, sources: Sources, event: ExecutionEvent) -> None:
        self._append_payload(sources, event, "observation")
        self._derive_progress_from_telemetry(sources, event)

    def _derive_progress_from_telemetry(
        self, sources: Sources, event: ExecutionEvent
    ) -> None:
        """Telemetry-derived task progress: no explicit progress event needed.

        An observation whose (normalized) metric is the canonical
        work-progress code reports the completed share of a task's work in
        percent; it scales the remaining work exactly like a task.progress
        event, and reaching the completion percentage finishes the task like
        a task.completed event.
        """
        for cid in self._contracts_for("observation", sources):
            table = self._tables[cid]
            by_path = table.by_binding_path()
            metric_binding = by_path.get(_OBSERVATION_METRIC_BINDING)
            value_binding = by_path.get(_OBSERVATION_VALUE_BINDING)
            ref_binding = by_path.get(_OBSERVATION_ENTITY_REF_BINDING)
            if metric_binding is None or value_binding is None or ref_binding is None:
                continue
            raw_metric = str(event.payload.get(metric_binding.source_field, ""))
            metric = table.metric_codes.get(raw_metric, raw_metric)
            if metric != METRIC_WORK_PROGRESS:
                continue
            task_ref = str(event.payload.get(ref_binding.source_field, ""))
            if not task_ref:
                continue
            if has_coverage_payload(event.payload) and self._apply_coverage_pass(
                sources, task_ref, event.payload, event.observed_at
            ):
                return
            try:
                progress_pct = float(event.payload.get(value_binding.source_field))
            except (TypeError, ValueError):
                logger.warning(
                    "work-progress observation for %s has unusable value: %r",
                    task_ref,
                    event.payload.get(value_binding.source_field),
                )
                return
            if progress_pct >= WORK_PROGRESS_COMPLETE_PCT:
                self._complete_task(
                    sources, task_ref, event.observed_at, "telemetry"
                )
            else:
                self._scale_task_work(
                    sources,
                    task_ref,
                    max(0.0, progress_pct) / WORK_PROGRESS_COMPLETE_PCT,
                    None,
                )
            return

    def _upsert_entity(self, sources: Sources, event: ExecutionEvent) -> None:
        """Replace (or add) a corrected source row, resolved by its key column.

        Corrections let entities previously rejected by quality policy (or
        accepted with wrong values) re-enter planning on the next revision:
        the snapshot is rebuilt from the corrected rows and the running plan
        reconciles against it.

        The resolved contract's watermark is advanced by the correction's
        observed time: a correction makes a previously-invisible (or wrong)
        entity trustworthy as of that instant, so it must move the visibility
        horizon and let the freshness check trigger a replan, exactly like a
        fresh observation on that contract would.
        """
        payload = dict(event.payload)
        candidates = [
            cid
            for cid, table in self._tables.items()
            if cid in sources
            and table.entity_key_field
            and table.entity_key_field in payload
        ]
        if not candidates:
            logger.warning(
                "entity.corrected for %s matches no loaded source collection",
                event.entity_ref,
            )
            return
        observed = self._parse_observed(event)
        for cid in candidates:
            key = self._tables[cid].entity_key_field
            rows = sources[cid]
            for i, row in enumerate(rows):
                if str(row.get(key)) == str(payload[key]):
                    rows[i] = payload
                    self._bump_watermark(cid, observed)
                    return
        # No existing row anywhere: the corrected entity is new to this run.
        target = candidates[0]
        sources[target].append(payload)
        self._bump_watermark(target, observed)

    def _merge_inventory(self, sources: Sources, event: ExecutionEvent) -> None:
        """Partial update of a location row (depot fuel/energy/material balances).

        Only the fields present in the payload are merged into the matched
        row; everything else (coordinates, name) stays untouched.
        """
        for cid in self._contracts_for("location", sources):
            key = self._key_field(cid)
            if not key or key not in event.payload:
                continue
            for row in sources[cid]:
                if str(row.get(key)) == str(event.payload[key]):
                    row.update(event.payload)
                    return
        logger.warning(
            "inventory.adjusted for %s matched no location row", event.entity_ref
        )

    def _update_forecast(self, sources: Sources, event: ExecutionEvent) -> None:
        """Weather-window invalidation: upsert the new forecast window row.

        Without a payload the event remains a pure replan trigger.
        """
        if event.payload:
            self._append_payload(sources, event, "forecast")
        else:
            logger.info("%s for %s triggers replan", event.event_type, event.entity_ref)

    def _replan_only(self, sources: Sources, event: ExecutionEvent) -> None:
        logger.info("%s for %s triggers replan", event.event_type, event.entity_ref)

    # -- dispatch -----------------------------------------------------------------

    def apply(self, sources: Sources, event: ExecutionEvent) -> bool:
        """Apply one event to the in-memory source rows (mutating them).

        Returns whether the event was applied; a replayed event id is skipped
        idempotently and must not trigger a new revision.
        """
        if event.event_id:
            if event.event_id in self._seen_event_ids:
                logger.info(
                    "Skipping replayed event %s (%s)", event.event_id, event.event_type
                )
                return False
            if self._dedup_store is not None and event.event_id in self._dedup_store:
                logger.info(
                    "Skipping event %s (%s): already published by an earlier run",
                    event.event_id,
                    event.event_type,
                )
                return False
            self._seen_event_ids.add(event.event_id)
        handler = self._HANDLERS.get(event.event_type)
        if handler is None:
            logger.warning("Unhandled event type '%s'; replanning anyway", event.event_type)
            return True
        handler(self, sources, event)
        self._advance_watermarks(sources, event)
        return True

    def _advance_watermarks(self, sources: Sources, event: ExecutionEvent) -> None:
        """Record the newest applied event time per mutated source contract."""
        entity = self._EVENT_ENTITY.get(event.event_type)
        if not entity:
            return
        observed = self._parse_observed(event)
        if observed is None:
            return
        for cid in self._contracts_for(entity, sources):
            self._bump_watermark(cid, observed)

    def _bump_watermark(self, cid: str, observed: Optional[datetime]) -> None:
        """Advance one contract's visibility horizon to a newer observed time."""
        if observed is None:
            return
        current = self.watermarks.get(cid)
        if current is None or observed > current:
            self.watermarks[cid] = observed

    @staticmethod
    def _parse_observed(event: ExecutionEvent) -> Optional[datetime]:
        """Parse an event's observed time; None when absent or unparseable."""
        if not event.observed_at:
            return None
        try:
            return datetime.fromisoformat(
                str(event.observed_at).replace("Z", "+00:00")
            )
        except ValueError:
            return None

    # Canonical entity each event type mutates. entity.corrected is absent: it
    # resolves its target contract dynamically by key column, so it advances
    # that contract's watermark directly from _upsert_entity instead.
    _EVENT_ENTITY: dict[str, str] = {
        EVENT_TASK_STARTED: "task",
        EVENT_TASK_PROGRESS: "task",
        EVENT_TASK_COMPLETED: "task",
        EVENT_ORDER_CREATED: "task",
        EVENT_ORDER_CANCELLED: "task",
        EVENT_ASSET_UNAVAILABLE: "asset",
        EVENT_INVENTORY_ADJUSTED: "location",
        EVENT_FORECAST_UPDATED: "forecast",
        EVENT_OBSERVATION_RECORDED: "observation",
    }

    _HANDLERS: dict[str, Callable[["EventApplicator", Sources, ExecutionEvent], None]] = {
        EVENT_TASK_STARTED: _set_task_started,
        EVENT_TASK_PROGRESS: _apply_task_progress,
        EVENT_TASK_COMPLETED: _apply_task_completed,
        EVENT_ORDER_CREATED: _append_task,
        EVENT_ORDER_CANCELLED: _remove_task,
        EVENT_ASSET_UNAVAILABLE: _remove_asset,
        EVENT_FORECAST_UPDATED: _update_forecast,
        EVENT_OBSERVATION_RECORDED: _append_observation,
        EVENT_ENTITY_CORRECTED: _upsert_entity,
        EVENT_INVENTORY_ADJUSTED: _merge_inventory,
    }

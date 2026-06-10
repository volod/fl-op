"""Binding-driven application of execution events to in-memory source rows.

The stream driver keeps raw physical source rows (keyed by contract id) and
mutates them per event before rebuilding a rolling snapshot. Which collection an
event touches and which physical column identifies a row are resolved from the
registry's mapping documents (canonical entity + identity binding), so the same
event vocabulary works for any domain pack without hardcoded column names.
"""

import logging
from typing import Any, Callable, Optional

from fl_op.canonical.enums import TaskStatus
from fl_op.contracts.registry import FileRegistry
from fl_op.mapping.bindings import BindingTable, load_binding_table
from fl_op.stream.source import (
    EVENT_ASSET_UNAVAILABLE,
    EVENT_ENTITY_CORRECTED,
    EVENT_FORECAST_UPDATED,
    EVENT_INVENTORY_ADJUSTED,
    EVENT_OBSERVATION_RECORDED,
    EVENT_ORDER_CANCELLED,
    EVENT_ORDER_CREATED,
    EVENT_TASK_PROGRESS,
    EVENT_TASK_STARTED,
    ExecutionEvent,
)

logger = logging.getLogger(__name__)

# Binding paths resolved against each contract's mapping document.
_TASK_STATUS_BINDING = "task.status"
_TASK_WORK_QUANTITY_BINDING = "task.areaHa"

# task.progress payload field: completed share of the task's work, [0, 1].
PAYLOAD_COMPLETED_FRACTION = "completed_fraction"

Sources = dict[str, list[dict[str, Any]]]


class EventApplicator:
    """Mutates physical source rows in response to canonical execution events."""

    def __init__(self, registry: Optional[FileRegistry] = None) -> None:
        self.registry = registry or FileRegistry()
        self._tables: dict[str, BindingTable] = {}
        self._by_entity: dict[str, list[str]] = {}
        # Idempotency: event ids already applied in this run. At-least-once
        # delivery may replay an event; a replay must mutate nothing and
        # produce no revision.
        self._seen_event_ids: set[str] = set()
        active = self.registry.active_domain
        for cid in self.registry.list_contracts():
            entry = self.registry.get_entry(cid)
            if active and entry.domain != active:
                continue
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

        The payload's ``completed_fraction`` scales the task's work-quantity
        column down to the remaining share; a fully completed task is removed
        (nothing is left to plan). Progress implies the task started.
        """
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
        if fraction >= 1.0:
            self._remove_by_key(sources, event, "task")
            return
        for cid in self._contracts_for("task", sources):
            key = self._key_field(cid)
            status = self._status_field(cid)
            work_binding = self._tables[cid].by_binding_path().get(_TASK_WORK_QUANTITY_BINDING)
            if not key or work_binding is None:
                continue
            work_field = work_binding.source_field
            for row in sources[cid]:
                if str(row.get(key)) != event.entity_ref:
                    continue
                try:
                    remaining = float(row.get(work_field, 0.0)) * (1.0 - fraction)
                except (TypeError, ValueError):
                    logger.warning(
                        "task.progress: %s has non-numeric %s", event.entity_ref, work_field
                    )
                    continue
                row[work_field] = round(remaining, 2)
                if status:
                    row[status] = TaskStatus.STARTED.value

    def _remove_by_key(self, sources: Sources, event: ExecutionEvent, entity: str) -> None:
        for cid in self._contracts_for(entity, sources):
            key = self._key_field(cid)
            if not key:
                continue
            sources[cid] = [
                row for row in sources[cid] if str(row.get(key)) != event.entity_ref
            ]

    def _remove_task(self, sources: Sources, event: ExecutionEvent) -> None:
        self._remove_by_key(sources, event, "task")

    def _remove_asset(self, sources: Sources, event: ExecutionEvent) -> None:
        self._remove_by_key(sources, event, "asset")

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

    def _upsert_entity(self, sources: Sources, event: ExecutionEvent) -> None:
        """Replace (or add) a corrected source row, resolved by its key column.

        Corrections let entities previously rejected by quality policy (or
        accepted with wrong values) re-enter planning on the next revision:
        the snapshot is rebuilt from the corrected rows and the running plan
        reconciles against it.
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
        for cid in candidates:
            key = self._tables[cid].entity_key_field
            rows = sources[cid]
            for i, row in enumerate(rows):
                if str(row.get(key)) == str(payload[key]):
                    rows[i] = payload
                    return
        # No existing row anywhere: the corrected entity is new to this run.
        sources[candidates[0]].append(payload)

    def _merge_inventory(self, sources: Sources, event: ExecutionEvent) -> None:
        """Partial update of a location row (depot fuel/material balances).

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
            self._seen_event_ids.add(event.event_id)
        handler = self._HANDLERS.get(event.event_type)
        if handler is None:
            logger.warning("Unhandled event type '%s'; replanning anyway", event.event_type)
            return True
        handler(self, sources, event)
        return True

    _HANDLERS: dict[str, Callable[["EventApplicator", Sources, ExecutionEvent], None]] = {
        EVENT_TASK_STARTED: _set_task_started,
        EVENT_TASK_PROGRESS: _apply_task_progress,
        EVENT_ORDER_CREATED: _append_task,
        EVENT_ORDER_CANCELLED: _remove_task,
        EVENT_ASSET_UNAVAILABLE: _remove_asset,
        EVENT_FORECAST_UPDATED: _update_forecast,
        EVENT_OBSERVATION_RECORDED: _append_observation,
        EVENT_ENTITY_CORRECTED: _upsert_entity,
        EVENT_INVENTORY_ADJUSTED: _merge_inventory,
    }

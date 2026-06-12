"""Durable event-id deduplication for broker-backed rolling runs.

At-least-once delivery may redeliver an event after a process restart; the
in-memory idempotency set cannot see ids applied by an earlier run. This
store persists the ids of events whose revisions were *published* (one id
per line under DATA_DIR/stream) and reloads them on construction. Recording
after publication and committing broker offsets right after keeps the
pipeline effectively-once end to end:

- crash before publication: nothing recorded, offsets uncommitted, the
  broker redelivers and the events are re-applied;
- crash after publication but before the offset commit: the redelivery is
  suppressed by the store, so no duplicate revision is published.

The JSONL development source replays event files intentionally and does not
use the store.
"""

import logging
import os
import pathlib
from typing import Iterable, Optional

from fl_op.core.constants import (
    EVENT_DEDUP_FILENAME,
    EVENT_DEDUP_MAX_IDS,
    STREAM_STATE_DIRNAME,
)
from fl_op.core.paths import DATA_ROOT

logger = logging.getLogger(__name__)


class EventDedupStore:
    """Append-only event-id log, compacted in place past the retention bound."""

    def __init__(self, path: Optional[pathlib.Path] = None) -> None:
        self.path = (
            pathlib.Path(path)
            if path is not None
            else DATA_ROOT / STREAM_STATE_DIRNAME / EVENT_DEDUP_FILENAME
        )
        self._ids: list[str] = []
        self._seen: set[str] = set()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                event_id = line.strip()
                if event_id and event_id not in self._seen:
                    self._seen.add(event_id)
                    self._ids.append(event_id)

    def __contains__(self, event_id: str) -> bool:
        return event_id in self._seen

    def __len__(self) -> int:
        return len(self._ids)

    def record_published(self, event_ids: Iterable[str]) -> None:
        """Durably record ids whose revisions were published.

        Call only after the revisions are written: an id in the store means
        the event's effect is published, so its redelivery is suppressed.
        """
        new_ids = []
        for event_id in event_ids:
            if event_id and event_id not in self._seen:
                self._seen.add(event_id)
                new_ids.append(event_id)
        if not new_ids:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            for event_id in new_ids:
                fh.write(event_id + "\n")
        self._ids.extend(new_ids)
        if len(self._ids) > EVENT_DEDUP_MAX_IDS:
            self._compact()

    def _compact(self) -> None:
        """Keep the newest ids, replacing the file atomically."""
        keep = self._ids[-EVENT_DEDUP_MAX_IDS:]
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text("\n".join(keep) + "\n")
        os.replace(tmp_path, self.path)
        self._ids = keep
        self._seen = set(keep)
        logger.info(
            "Compacted event dedup store to newest %d ids (%s)",
            len(keep),
            self.path,
        )

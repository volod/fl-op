"""Per-request audit logging for the serving API.

Every protected request emits one structured audit record naming the principal,
the route, and the access decision (allow / the refusal reason and status).
Records always go to the ``fl_op.serving.audit`` logger; when a filename is
configured they are also appended as JSONL under
``DATA_DIR/<SERVE_AUDIT_DIRNAME>/`` for durable retention. Token strings are
never logged -- only the short non-reversible token id the authenticator sets.
"""

import json
import logging
import pathlib
import threading
from datetime import datetime, timezone
from typing import Optional

from fl_op.serving.security.principal import Principal

audit_logger = logging.getLogger("fl_op.serving.audit")

# Access decisions recorded in the audit trail.
DECISION_ALLOW: str = "allow"
DECISION_DENY: str = "deny"


class AuditLogger:
    """Writes one audit record per request to a logger and optional JSONL file."""

    def __init__(
        self,
        enabled: bool = True,
        file_path: Optional[pathlib.Path] = None,
    ) -> None:
        self.enabled = enabled
        self.file_path = file_path
        self._lock = threading.Lock()

    def record(
        self,
        *,
        principal: Optional[Principal],
        method: str,
        path: str,
        client: str,
        decision: str,
        status_code: int,
        reason: str = "",
    ) -> None:
        if not self.enabled:
            return
        record = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "method": method,
            "path": path,
            "client": client,
            "subject": principal.subject if principal else "",
            "token_id": principal.token_id if principal else "",
            "decision": decision,
            "status": status_code,
            "reason": reason,
        }
        audit_logger.info(
            "audit %s %s subject=%s decision=%s status=%d%s",
            method,
            path,
            record["subject"] or "-",
            decision,
            status_code,
            f" reason={reason}" if reason else "",
        )
        if self.file_path is not None:
            self._append(record)

    def _append(self, record: dict[str, object]) -> None:
        line = json.dumps(record, separators=(",", ":"))
        with self._lock:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.file_path.open("a") as fh:
                fh.write(line + "\n")

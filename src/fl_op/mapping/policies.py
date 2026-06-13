"""Missing-value policy application producing explicit quality findings.

No value is ever silently imputed: every fallback, imputation, or rejection emits
a QualityFinding so the snapshot and explanation service can surface it.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from fl_op.canonical.common import QualityFinding
from fl_op.canonical.enums import QualitySeverity
from fl_op.contracts.xopt import MissingValuePolicy

logger = logging.getLogger(__name__)

# Conservative fallback values by quantity kind, used by
# fallback-to-conservative-value and impute policies.
_CONSERVATIVE_FALLBACK: dict[str, Any] = {
    "power": 0.0,
    "mass": 0.0,
    "volume": 0.0,
    "money": 0.0,
    "speed": 1.0,
    "area": 0.0,
    "flow-rate": 0.0,
}


@dataclass
class PolicyOutcome:
    """Result of applying a missing-value policy to a single field."""

    value: Any
    drop_entity: bool
    finding: Optional[QualityFinding]


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def apply_missing_value_policy(
    *,
    raw_value: Any,
    policy: Optional[MissingValuePolicy],
    entity_ref: str,
    field_ref: str,
    quantity_kind: Optional[str],
    quality_policy_ref: Optional[str],
    finding_seq: int,
) -> PolicyOutcome:
    """Resolve a possibly-missing source value according to its declared policy."""
    if not _is_missing(raw_value):
        return PolicyOutcome(value=raw_value, drop_entity=False, finding=None)

    if policy == MissingValuePolicy.ACCEPT_OPTIONAL:
        return PolicyOutcome(value=None, drop_entity=False, finding=None)

    now = datetime.now(tz=timezone.utc)
    rule_id = quality_policy_ref or "dq://default/missing-value"
    fid = f"qf-{entity_ref}-{field_ref}-{finding_seq}"

    def finding(severity: QualitySeverity, action: str, normalized: Any) -> QualityFinding:
        return QualityFinding(
            quality_finding_id=fid,
            rule_id=rule_id,
            severity=severity,
            entity_ref=entity_ref,
            field_ref=field_ref,
            detected_at=now,
            action_applied=action,
            original_value=raw_value,
            normalized_value=normalized,
            planning_impact=action,
            source_ref=entity_ref,
        )

    if policy in (None, MissingValuePolicy.REJECT_FOR_PLANNING):
        return PolicyOutcome(
            value=None,
            drop_entity=True,
            finding=finding(QualitySeverity.ERROR, "reject-for-planning", None),
        )
    if policy == MissingValuePolicy.ACCEPT_WITH_WARNING:
        return PolicyOutcome(
            value=None,
            drop_entity=False,
            finding=finding(QualitySeverity.WARNING, "accept-with-warning", None),
        )
    if policy in (
        MissingValuePolicy.FALLBACK_TO_CONSERVATIVE_VALUE,
        MissingValuePolicy.IMPUTE,
    ):
        fallback = _CONSERVATIVE_FALLBACK.get(quantity_kind or "", 0.0)
        action = (
            "fallback-to-conservative-value"
            if policy == MissingValuePolicy.FALLBACK_TO_CONSERVATIVE_VALUE
            else "impute"
        )
        return PolicyOutcome(
            value=fallback,
            drop_entity=False,
            finding=finding(QualitySeverity.INFO, action, fallback),
        )
    # quarantine / manual-review / accept-with-penalty: exclude from planning but
    # keep traceable.
    return PolicyOutcome(
        value=None,
        drop_entity=True,
        finding=finding(QualitySeverity.WARNING, policy.value, None),
    )

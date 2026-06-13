"""Candidate scoring for resource pre-allocation."""

from typing import Any, Optional

import numpy as np

from fl_op.core.constants import HOLD_CAPACITY_HORIZON_S

_URGENCY_SCORE_WEIGHT = 0.10
_POWER_MARGIN_TIEBREAKER_WEIGHT = 0.01

ScoredLookup = dict[str, dict[tuple[int, int], float]]

# Asset id -> free share of the hold-capacity horizon, in [0, 1]. Assets
# without held windows are absent and treated as fully free.
FreeCapacity = dict[str, float]


def build_scored_lookup(
    scored_pairs: dict[str, list[tuple[float, int, int]]] | None,
) -> ScoredLookup | None:
    """Convert per-order scored tuples into a fast pair lookup."""
    if scored_pairs is None:
        return None
    return {
        oid: {(v_idx, i_idx): float(score) for score, v_idx, i_idx in candidates}
        for oid, candidates in scored_pairs.items()
    }


def build_free_capacity(
    held_windows: Optional[dict[str, list[tuple[int, int]]]],
    now_epoch: int,
    horizon_s: int = HOLD_CAPACITY_HORIZON_S,
) -> FreeCapacity:
    """Free share of the capacity horizon per held asset.

    A held asset's busy intervals (clamped to [now, now + horizon], merged)
    reduce its free fraction; 0.0 means fully held for the horizon. Assets
    without held windows are absent (treated as fully free).
    """
    if not held_windows:
        return {}
    from fl_op.solver.restrictions import merge_intervals

    horizon_end = now_epoch + horizon_s
    capacity: FreeCapacity = {}
    for asset_id, windows in held_windows.items():
        clamped = [
            (max(int(start), now_epoch), min(int(end), horizon_end))
            for start, end in windows
        ]
        busy_s = sum(
            end - start
            for start, end in merge_intervals([(s, e) for s, e in clamped if e > s])
        )
        capacity[asset_id] = max(0.0, 1.0 - busy_s / horizon_s)
    return capacity


def score_vi_pair(
    order: Any,
    power_margin: np.ndarray,
    v_idx: int,
    i_idx: int,
    scored_lookup: ScoredLookup | None,
    free_capacity: Optional[FreeCapacity] = None,
    vehicle_id: str = "",
    implement_id: str = "",
) -> float:
    """Score a candidate pair for global pre-allocation.

    Hold-aware discount: a positive score is scaled by the pair's smallest
    free-capacity fraction, so a cluster prefers assets whose calendars can
    still fit its work over equally suitable but mostly-held ones (negative
    scores stay as they are; making them less negative would invert the
    preference).
    """
    base_score = None
    if scored_lookup is not None:
        base_score = scored_lookup.get(order.task_id, {}).get((v_idx, i_idx))
    if base_score is None:
        base_score = float(power_margin[v_idx, i_idx])

    urgency = _float_or_zero(order.penalty_per_day)
    urgency *= _URGENCY_SCORE_WEIGHT
    headroom = float(power_margin[v_idx, i_idx]) * _POWER_MARGIN_TIEBREAKER_WEIGHT
    score = float(base_score) + urgency + headroom
    if free_capacity and score > 0:
        discount = min(
            free_capacity.get(vehicle_id, 1.0),
            free_capacity.get(implement_id, 1.0),
        )
        score *= discount
    return score


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

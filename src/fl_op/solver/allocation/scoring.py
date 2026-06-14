"""Candidate scoring for resource pre-allocation."""

from typing import Any, Optional

import numpy as np

from fl_op.core.constants import HOLD_CAPACITY_HORIZON_S

_URGENCY_SCORE_WEIGHT = 0.10
_POWER_MARGIN_TIEBREAKER_WEIGHT = 0.01

ScoredLookup = dict[str, dict[tuple[int, int], float]]

# Asset id -> largest contiguous free-gap share of the hold-capacity horizon,
# in [0, 1]. Assets without held windows are absent and treated as fully free.
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
    """Largest contiguous free-gap share of the capacity horizon per held asset.

    A held asset's busy intervals (clamped to [now, now + horizon], merged) carve
    the horizon into free gaps; the metric is the longest single gap divided by
    the horizon. This is gap-aware: a fragmented calendar whose total free time is
    high but whose largest gap is small cannot host a contiguous execution window,
    so it scores lower than an equally-free asset with one long gap. 0.0 means no
    free gap remains; assets without held windows are absent (fully free).
    """
    if not held_windows:
        return {}
    from fl_op.solver.restrictions import merge_intervals

    horizon_end = now_epoch + horizon_s
    capacity: FreeCapacity = {}
    for asset_id, windows in held_windows.items():
        busy = merge_intervals(
            [
                (max(int(start), now_epoch), min(int(end), horizon_end))
                for start, end in windows
                if min(int(end), horizon_end) > max(int(start), now_epoch)
            ]
        )
        largest_gap = _largest_free_gap(busy, now_epoch, horizon_end)
        capacity[asset_id] = max(0.0, min(1.0, largest_gap / horizon_s))
    return capacity


def _largest_free_gap(
    busy: list[tuple[int, int]], start: int, end: int
) -> int:
    """Longest uninterrupted free interval within [start, end] given merged busy.

    `busy` must be sorted and non-overlapping (as produced by `merge_intervals`).
    Uses plain subtraction (no closed-interval +/-1 offsets) so a single block
    flush against one edge yields the exact remaining span.
    """
    cursor = start
    largest = 0
    for b_start, b_end in busy:
        gap = b_start - cursor
        if gap > largest:
            largest = gap
        if b_end > cursor:
            cursor = b_end
    tail = end - cursor
    if tail > largest:
        largest = tail
    return max(0, largest)


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
    largest-free-gap fraction, so a cluster prefers assets whose calendars still
    have a long open stretch to fit its work over equally suitable ones that are
    mostly held or so fragmented that no single gap can host the execution
    (negative scores stay as they are; making them less negative would invert the
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

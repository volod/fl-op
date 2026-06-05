"""Candidate scoring for resource pre-allocation."""

from typing import Any

import numpy as np

_URGENCY_SCORE_WEIGHT = 0.10
_POWER_MARGIN_TIEBREAKER_WEIGHT = 0.01

ScoredLookup = dict[str, dict[tuple[int, int], float]]


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


def score_vi_pair(
    order: dict[str, Any],
    power_margin: np.ndarray,
    v_idx: int,
    i_idx: int,
    scored_lookup: ScoredLookup | None,
) -> float:
    """Score a candidate pair for global pre-allocation."""
    base_score = None
    if scored_lookup is not None:
        base_score = scored_lookup.get(order["order_id"], {}).get((v_idx, i_idx))
    if base_score is None:
        base_score = float(power_margin[v_idx, i_idx])

    urgency = _float_or_zero(order.get("penalty_per_day_eur", 0.0))
    urgency *= _URGENCY_SCORE_WEIGHT
    headroom = float(power_margin[v_idx, i_idx]) * _POWER_MARGIN_TIEBREAKER_WEIGHT
    return float(base_score) + urgency + headroom


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

"""Capability-based compatibility between prime-mover and related-equipment assets.

Operates on the generic solver rows projected from canonical assets, reading
only canonical power capabilities, so it works for any domain (agricultural,
construction, ...).

Layout: rows = prime movers (indexed 0..N_p-1), cols = related equipment
(indexed 0..N_r-1).
  compat[p, r]       -> bool: prime mover p can power related equipment r
  power_margin[p, r] -> float32: (prime.ratedPower - related.requiredPower) as a
                        percentage of prime.ratedPower; positive means headroom,
                        negative means overload.

Workers load via np.load(mmap_mode='r') for zero-copy read access.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np

from fl_op.core.constants import POWER_MARGIN_PCT

logger = logging.getLogger(__name__)

COMPAT_FILENAME = "compat.npy"
POWER_MARGIN_FILENAME = "power_margin.npy"


def build_compat_matrix(
    prime_movers: list[Any],
    related_equipment: list[Any],
) -> tuple[np.ndarray, np.ndarray]:
    """Return (compat bool ndarray, power_margin float32 ndarray), shape (N_p, N_r).

    A prime-related pair is compatible when the power margin is within
    POWER_MARGIN_PCT, i.e. the prime mover is not overloaded beyond the allowed
    short-duration peak. Finer operation-type compatibility is applied per-task in
    preprocessing.
    """
    n_p = len(prime_movers)
    n_r = len(related_equipment)

    rated = np.array([p.rated_power for p in prime_movers], dtype=np.float32)  # (N_p,)
    required = np.array(
        [r.required_power for r in related_equipment], dtype=np.float32
    )  # (N_r,)

    # power_margin[p, r] = (rated[p] - required[r]) / rated[p] * 100, via broadcast.
    power_margin: np.ndarray = (
        (rated[:, np.newaxis] - required[np.newaxis, :]) / rated[:, np.newaxis] * 100.0
    ).astype(np.float32)

    compat: np.ndarray = power_margin >= -POWER_MARGIN_PCT

    logger.debug(
        "Built compat matrix %dx%d: %d compatible pairs (%.1f%%)",
        n_p,
        n_r,
        compat.sum(),
        100.0 * compat.sum() / (n_p * n_r) if n_p and n_r else 0.0,
    )
    return compat, power_margin


def save_compat_matrix(
    compat: np.ndarray,
    power_margin: np.ndarray,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    np.save(output_dir / COMPAT_FILENAME, compat)
    np.save(output_dir / POWER_MARGIN_FILENAME, power_margin)
    logger.debug("Saved compat matrix to %s", output_dir)


def load_compat_matrix(
    matrix_dir: Path,
) -> tuple[np.ndarray, np.ndarray]:
    """Load compat and power_margin arrays with memory-mapped read-only access."""
    compat = np.load(matrix_dir / COMPAT_FILENAME, mmap_mode="r")
    power_margin = np.load(matrix_dir / POWER_MARGIN_FILENAME, mmap_mode="r")
    return compat, power_margin

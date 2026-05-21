"""Compatibility matrix between vehicles and implements.

Layout: rows = vehicles (indexed 0..N_v-1), cols = implements (indexed 0..N_i-1).
  compat[v, i]       -> bool: vehicle v can tow implement i
  power_margin[v, i] -> float32: (vehicle.rated_power_kw - implement.required_power_kw)
                        as a percentage of vehicle.rated_power_kw;
                        positive means headroom, negative means overload.

Workers load via np.load(mmap_mode='r') for zero-copy read access.
"""

import logging
from pathlib import Path

import numpy as np

from fl_op.core.constants import POWER_MARGIN_PCT
from fl_op.models.implement import Implement
from fl_op.models.vehicle import Vehicle

logger = logging.getLogger(__name__)

COMPAT_FILENAME = "compat.npy"
POWER_MARGIN_FILENAME = "power_margin.npy"


def build_compat_matrix(
    vehicles: list[Vehicle],
    implements: list[Implement],
) -> tuple[np.ndarray, np.ndarray]:
    """Return (compat bool ndarray, power_margin float32 ndarray), shape (N_v, N_i).

    A V-I pair is compatible when:
      1. The implement's required operation is in the vehicle type's capability set
         (currently delegated to ImplementType/OperationType matching; refined in
         preprocessing.py per-order).
      2. The power margin is within POWER_MARGIN_PCT (vehicle is not overloaded).
    """
    n_v = len(vehicles)
    n_i = len(implements)

    # Vectorize rated power and required power for broadcast
    rated = np.array([v.rated_power_kw for v in vehicles], dtype=np.float32)  # (N_v,)
    required = np.array([im.required_power_kw for im in implements], dtype=np.float32)  # (N_i,)

    # power_margin[v, i] = (rated[v] - required[i]) / rated[v] * 100
    # Shape: (N_v, N_i) via broadcast
    power_margin: np.ndarray = (
        (rated[:, np.newaxis] - required[np.newaxis, :]) / rated[:, np.newaxis] * 100.0
    ).astype(np.float32)

    # Compatible when implement does not overload vehicle beyond allowed margin
    # i.e. power_margin >= -POWER_MARGIN_PCT  (negative margin = overload)
    compat: np.ndarray = power_margin >= -POWER_MARGIN_PCT

    logger.debug(
        "Built compat matrix %dx%d: %d compatible pairs (%.1f%%)",
        n_v,
        n_i,
        compat.sum(),
        100.0 * compat.sum() / (n_v * n_i),
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

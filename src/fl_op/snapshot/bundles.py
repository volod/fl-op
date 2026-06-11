"""Bundle feasibility summary and lazy bundle enumeration for the snapshot.

The snapshot no longer materializes a capped bundle list. It carries a
compact BundleFeasibilitySummary with exact counts computed vectorised over
the full prime-mover x related-equipment cross product, and consumers that
need concrete bundles enumerate them lazily through ``iter_bundles`` (a
generator over the same compatibility rule, filterable by operation type or
participating asset). The solver adapters perform their own compatibility
filtering, so both surfaces are explanation artifacts, never assignment
inputs.
"""

import logging
from typing import TYPE_CHECKING, Iterator, Optional

import numpy as np

from fl_op.canonical.asset import Capability
from fl_op.canonical.bundle import (
    BundleFeasibilitySummary,
    OperationalBundle,
    compute_bundle_id,
)
from fl_op.core.constants import POWER_MARGIN_PCT, URN_CAPABILITY_PREFIX

if TYPE_CHECKING:
    from fl_op.canonical.asset import Asset

logger = logging.getLogger(__name__)

_RATED_POWER_TERM = URN_CAPABILITY_PREFIX + "rated-power"
_REQUIRED_POWER_TERM = URN_CAPABILITY_PREFIX + "required-power"
_COMPATIBLE_OPS_TERM = URN_CAPABILITY_PREFIX + "compatible-operations"


def _split_roles(assets: list["Asset"]) -> tuple[list["Asset"], list["Asset"]]:
    prime_movers = [a for a in assets if "mobile-prime-mover" in a.roles]
    implements = [a for a in assets if "implement" in a.roles]
    return prime_movers, implements


def _power_value(asset: "Asset", term: str) -> float:
    """Capability power value; NaN when absent (never pairs)."""
    value = asset.capability_value(term)
    try:
        return float(value) if value is not None else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _pair_matrix(
    prime_movers: list["Asset"], implements: list["Asset"]
) -> np.ndarray:
    """Bool matrix [prime, implement]: pair is power-feasible.

    Mirrors the solver compatibility rule: an implement may draw up to
    POWER_MARGIN_PCT above the prime mover's rated power. Assets without the
    relevant power capability never pair (NaN comparisons are False).
    """
    if not prime_movers or not implements:
        return np.zeros((len(prime_movers), len(implements)), dtype=bool)
    rated = np.array(
        [_power_value(pm, _RATED_POWER_TERM) for pm in prime_movers], dtype=np.float64
    )
    required = np.array(
        [_power_value(im, _REQUIRED_POWER_TERM) for im in implements], dtype=np.float64
    )
    max_required = rated[:, np.newaxis] * (1.0 + POWER_MARGIN_PCT / 100.0)
    with np.errstate(invalid="ignore"):
        return required[np.newaxis, :] <= max_required


def summarize_bundles(assets: list["Asset"]) -> BundleFeasibilitySummary:
    """Exact feasibility summary over the full bundle cross product."""
    prime_movers, implements = _split_roles(assets)
    pairs = _pair_matrix(prime_movers, implements)

    pairs_per_implement = pairs.sum(axis=0)
    pairs_by_operation: dict[str, int] = {}
    for column, implement in enumerate(implements):
        count = int(pairs_per_implement[column])
        if count == 0:
            continue
        for op in implement.capability_value(_COMPATIBLE_OPS_TERM) or []:
            op_name = str(op)
            pairs_by_operation[op_name] = pairs_by_operation.get(op_name, 0) + count

    summary = BundleFeasibilitySummary(
        n_prime_movers=len(prime_movers),
        n_related_equipment=len(implements),
        n_feasible_pairs=int(pairs.sum()),
        pairs_by_operation=dict(sorted(pairs_by_operation.items())),
        n_unmatched_prime_movers=int((~pairs.any(axis=1)).sum()) if implements else len(prime_movers),
        n_unmatched_related_equipment=int((~pairs.any(axis=0)).sum()) if prime_movers else len(implements),
    )
    logger.info(
        "Bundle feasibility: %d prime movers x %d related -> %d feasible pairs",
        summary.n_prime_movers,
        summary.n_related_equipment,
        summary.n_feasible_pairs,
    )
    return summary


def iter_bundles(
    assets: list["Asset"],
    configuration_version: str,
    operation_type: Optional[str] = None,
    asset_id: Optional[str] = None,
) -> Iterator[OperationalBundle]:
    """Lazily enumerate feasible bundles, optionally filtered.

    ``operation_type`` keeps only bundles whose related equipment supports the
    operation; ``asset_id`` keeps only bundles a given asset participates in.
    Enumeration order is deterministic (prime movers x implements in asset
    order), and nothing is materialized beyond the yielded bundle.
    """
    prime_movers, implements = _split_roles(assets)
    pairs = _pair_matrix(prime_movers, implements)
    for row, pm in enumerate(prime_movers):
        for column, im in enumerate(implements):
            if not pairs[row, column]:
                continue
            if asset_id is not None and asset_id not in (pm.asset_id, im.asset_id):
                continue
            if operation_type is not None:
                supported = [
                    str(op)
                    for op in im.capability_value(_COMPATIBLE_OPS_TERM) or []
                ]
                if operation_type not in supported:
                    continue
            rated = _power_value(pm, _RATED_POWER_TERM)
            yield OperationalBundle(
                bundle_id=compute_bundle_id(
                    [pm.asset_id, im.asset_id], [], configuration_version
                ),
                bundle_type=f"{pm.asset_type}+{im.asset_type}",
                asset_ids=[pm.asset_id, im.asset_id],
                capabilities=[
                    Capability(
                        capability_id="rated-power",
                        semantic_term=_RATED_POWER_TERM,
                        value=rated,
                        canonical_unit="kW",
                    )
                ],
            )

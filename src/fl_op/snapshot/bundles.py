"""Operational-bundle generation for the snapshot (spec 18.1 static filtering).

Reuses the existing NumPy compatibility matrix to enumerate feasible
(prime-mover, implement) pairs and materializes them as canonical
OperationalBundles for inspection and explanation. The solver adapters perform
their own compatibility filtering, so this is bounded by BUNDLE_GENERATION_CAP.
"""

import logging
from typing import TYPE_CHECKING

from fl_op.canonical.asset import Capability
from fl_op.canonical.bundle import OperationalBundle, compute_bundle_id
from fl_op.core.constants import (
    BUNDLE_GENERATION_CAP,
    POWER_MARGIN_PCT,
    URN_CAPABILITY_PREFIX,
)

if TYPE_CHECKING:
    from fl_op.canonical.asset import Asset

logger = logging.getLogger(__name__)

_RATED_POWER_TERM = URN_CAPABILITY_PREFIX + "rated-power"
_REQUIRED_POWER_TERM = URN_CAPABILITY_PREFIX + "required-power"
_COMPATIBLE_OPS_TERM = URN_CAPABILITY_PREFIX + "compatible-operations"


def generate_bundles(
    assets: list["Asset"], configuration_version: str
) -> list[OperationalBundle]:
    """Enumerate compatible prime-mover + implement bundles (power feasibility)."""
    prime_movers = [a for a in assets if "mobile-prime-mover" in a.roles]
    implements = [a for a in assets if "implement" in a.roles]

    bundles: list[OperationalBundle] = []
    for pm in prime_movers:
        rated = pm.capability_value(_RATED_POWER_TERM)
        if rated is None:
            continue
        # Mirror the solver compatibility rule: an implement may draw up to
        # POWER_MARGIN_PCT above the prime mover's rated power.
        max_required = float(rated) * (1.0 + POWER_MARGIN_PCT / 100.0)
        for im in implements:
            required = im.capability_value(_REQUIRED_POWER_TERM)
            if required is None or required > max_required:
                continue
            bundle_id = compute_bundle_id([pm.asset_id, im.asset_id], [], configuration_version)
            bundles.append(
                OperationalBundle(
                    bundle_id=bundle_id,
                    bundle_type=f"{pm.asset_type}+{im.asset_type}",
                    asset_ids=[pm.asset_id, im.asset_id],
                    capabilities=[
                        Capability(
                            capability_id="rated-power",
                            semantic_term=_RATED_POWER_TERM,
                            value=float(rated),
                            canonical_unit="kW",
                        )
                    ],
                )
            )
            if len(bundles) >= BUNDLE_GENERATION_CAP:
                logger.warning(
                    "Bundle generation capped at %d; snapshot bundle list truncated",
                    BUNDLE_GENERATION_CAP,
                )
                return bundles
    logger.info("Generated %d operational bundles", len(bundles))
    return bundles

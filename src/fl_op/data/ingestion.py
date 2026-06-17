"""Synthetic ingestion-arrival timestamps for generated readings and events.

A report is taken in the field at its observed time but reaches the platform
later, through a buffered, partitioned pipeline. Stamping a true ``ingested_at``
(observed time plus a bounded delivery delay) makes a series order by ingestion
across restarts, and lets purely event-fed series detect arrival-order
timestamp regressions, instead of approximating arrival by source row order.

Every generator (file readings and event producers alike) stamps arrival times
through this one helper so the synthetic delivery-delay model stays uniform.
"""

from datetime import datetime, timedelta

import numpy as np

from fl_op.core.constants import INGESTION_DELAY_MAX_S


def ingestion_delay_s(rng: np.random.Generator) -> float:
    """A bounded synthetic delivery delay in seconds, drawn in [0, max]."""
    return float(rng.uniform(0.0, INGESTION_DELAY_MAX_S))


def stamp_ingested(observed: datetime, rng: np.random.Generator) -> str:
    """ISO-8601 arrival time: ``observed`` plus a bounded delivery delay."""
    return (observed + timedelta(seconds=ingestion_delay_s(rng))).isoformat()

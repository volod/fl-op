"""Cross-run trending of per-source observation error rates.

Each dataset snapshot build appends one record to an append-only JSONL file
under ``$DATA_DIR/quality/``, so slow source degradation is visible between
builds, not only within one snapshot. A source is reported as degrading when
its error rate strictly increased over the last ERROR_RATE_TREND_MIN_RUNS
recorded runs and is non-zero.
"""

import json
import logging
import pathlib
from typing import TYPE_CHECKING, Any, Optional

from fl_op.core.constants import (
    ERROR_RATE_TREND_MIN_RUNS,
    QUALITY_TREND_DIRNAME,
    QUALITY_TREND_FILENAME,
)
from fl_op.core.paths import DATA_ROOT

if TYPE_CHECKING:
    from fl_op.canonical.snapshot import PlanningSnapshot

logger = logging.getLogger(__name__)


def _trend_path(path: Optional[pathlib.Path]) -> pathlib.Path:
    return path or (DATA_ROOT / QUALITY_TREND_DIRNAME / QUALITY_TREND_FILENAME)


def record_error_rates(
    snapshot: "PlanningSnapshot", path: Optional[pathlib.Path] = None
) -> None:
    """Append the snapshot's per-source observation error rates to the trend."""
    rates = snapshot.quality_summary.observation_error_rates
    if not rates:
        return
    target = _trend_path(path)
    record = {
        "generated_at": snapshot.generated_at.isoformat(),
        "snapshot_id": snapshot.snapshot_id,
        "rates": rates,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("Could not append quality trend record to %s: %s", target, exc)


def _load_records(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Skipping malformed quality trend line in %s", path)
    return records


def degrading_sources(
    path: Optional[pathlib.Path] = None,
    min_runs: int = ERROR_RATE_TREND_MIN_RUNS,
) -> dict[str, list[float]]:
    """Sources whose error rate strictly increased over the last min_runs runs.

    Returns contract id -> the increasing rate sequence, empty when no source
    is degrading or not enough history exists.
    """
    records = _load_records(_trend_path(path))
    if len(records) < min_runs:
        return {}
    window = records[-min_runs:]
    contracts = set().union(*(set(r.get("rates", {})) for r in window))
    degrading: dict[str, list[float]] = {}
    for contract in sorted(contracts):
        rates = [r.get("rates", {}).get(contract) for r in window]
        if any(rate is None for rate in rates):
            continue
        increasing = all(prev < cur for prev, cur in zip(rates, rates[1:]))
        if increasing and rates[-1] > 0.0:
            degrading[contract] = rates
    return degrading

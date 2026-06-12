"""Cross-run observation error-rate trending."""

import json
import pathlib
from datetime import datetime, timezone

import pytest

from fl_op.canonical.common import QualitySummary, TimeInterval, VersionDimensions
from fl_op.canonical.enums import PlanningMode
from fl_op.canonical.snapshot import PlanningSnapshot
from fl_op.snapshot.quality_trend import degrading_sources, record_error_rates

_TS = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _snapshot(rates: dict[str, float], run: int) -> PlanningSnapshot:
    return PlanningSnapshot(
        snapshot_id=f"snap-{run}",
        effective_at=_TS,
        generated_at=_TS,
        planning_mode=PlanningMode.PERIODIC,
        planning_horizon=TimeInterval(**{"from": _TS}),
        version_dimensions=VersionDimensions(),
        quality_summary=QualitySummary(observation_error_rates=rates),
    )


def test_strictly_increasing_rates_flag_source_as_degrading(tmp_path: pathlib.Path) -> None:
    trend = tmp_path / "rates.jsonl"
    for run, rate in enumerate([0.05, 0.10, 0.20]):
        record_error_rates(_snapshot({"sensor-readings": rate}, run), trend)
    degrading = degrading_sources(trend)
    assert degrading == {"sensor-readings": [0.05, 0.10, 0.20]}


def test_plateau_or_recovery_is_not_degrading(tmp_path: pathlib.Path) -> None:
    trend = tmp_path / "rates.jsonl"
    for run, rate in enumerate([0.05, 0.20, 0.10]):
        record_error_rates(_snapshot({"sensor-readings": rate}, run), trend)
    assert degrading_sources(trend) == {}


def test_insufficient_history_is_not_degrading(tmp_path: pathlib.Path) -> None:
    trend = tmp_path / "rates.jsonl"
    for run, rate in enumerate([0.05, 0.10]):
        record_error_rates(_snapshot({"sensor-readings": rate}, run), trend)
    assert degrading_sources(trend) == {}


def test_zero_rates_are_not_recorded_as_degrading(tmp_path: pathlib.Path) -> None:
    trend = tmp_path / "rates.jsonl"
    for run in range(3):
        record_error_rates(_snapshot({"sensor-readings": 0.0}, run), trend)
    assert degrading_sources(trend) == {}


def test_trend_file_is_compacted_to_newest_records(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """The append-only file cannot grow unboundedly: once over the cap it is
    compacted in place to the newest records, and the trend verdict on the
    retained window is unchanged."""
    from fl_op.snapshot import quality_trend

    monkeypatch.setattr(quality_trend, "QUALITY_TREND_MAX_RECORDS", 5)
    trend = tmp_path / "rates.jsonl"
    rates = [0.01 * run for run in range(12)]
    for run, rate in enumerate(rates):
        record_error_rates(_snapshot({"sensor-readings": rate}, run), trend)

    lines = [ln for ln in trend.read_text().splitlines() if ln.strip()]
    assert len(lines) == 5
    kept_ids = [json.loads(ln)["snapshot_id"] for ln in lines]
    assert kept_ids == [f"snap-{run}" for run in range(7, 12)]
    assert degrading_sources(trend) == {
        "sensor-readings": [pytest.approx(r) for r in rates[-3:]]
    }
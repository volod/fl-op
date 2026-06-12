"""Guarded automatic monitoring-policy tuning: bounded steps, audit trail."""

import json

import pytest

from fl_op.contracts.profile import MonitoringPolicySpec
from fl_op.snapshot.policy_tuning import (
    apply_tuned_overrides,
    auto_tune_monitoring_policy,
    load_tuned_overrides,
)


def _accuracy(fp: float = 0.0, fn: float = 0.0) -> dict[str, float]:
    return {
        "n_observed": 25.0,
        "false_positive_rate": fp,
        "false_negative_rate": fn,
    }


class TestAutoTune:
    def test_false_positives_tighten_policy_with_bounded_step(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        audit = tmp_path / "audit.jsonl"
        overrides = auto_tune_monitoring_policy(
            _accuracy(fp=0.5), MonitoringPolicySpec(), overlay, audit
        )
        # One 10% step down from the defaults (3.0 days, 0.35 threshold).
        assert overrides["batteryForecastHorizonDays"] == pytest.approx(2.7)
        assert overrides["compositeHealthThreshold"] == pytest.approx(0.315)
        assert json.loads(overlay.read_text()) == pytest.approx(overrides)
        records = [json.loads(line) for line in audit.read_text().splitlines()]
        assert records[0]["reason"] == "false-positives"
        assert len(records[0]["adjustments"]) == 2

    def test_false_negatives_loosen_policy(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        overrides = auto_tune_monitoring_policy(
            _accuracy(fn=0.5), MonitoringPolicySpec(), overlay, tmp_path / "a.jsonl"
        )
        assert overrides["batteryForecastHorizonDays"] == pytest.approx(3.3)
        assert overrides["compositeHealthThreshold"] == pytest.approx(0.385)

    def test_steps_accumulate_and_clamp_at_the_bounds(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        audit = tmp_path / "audit.jsonl"
        for _ in range(40):
            auto_tune_monitoring_policy(
                _accuracy(fp=0.5), MonitoringPolicySpec(), overlay, audit
            )
        overrides = load_tuned_overrides(overlay)
        # The absolute clamps hold no matter how many steps accumulate.
        assert overrides["batteryForecastHorizonDays"] == pytest.approx(1.0)
        assert overrides["compositeHealthThreshold"] == pytest.approx(0.1)

    def test_conflicting_signals_skip_adjustment_but_audit(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        audit = tmp_path / "audit.jsonl"
        overrides = auto_tune_monitoring_policy(
            _accuracy(fp=0.5, fn=0.5), MonitoringPolicySpec(), overlay, audit
        )
        assert overrides == {}
        assert not overlay.exists()
        record = json.loads(audit.read_text().splitlines()[0])
        assert record["reason"] == "conflicting-signals"
        assert record["adjustments"] == []

    def test_healthy_rates_change_nothing(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        audit = tmp_path / "audit.jsonl"
        auto_tune_monitoring_policy(
            _accuracy(fp=0.1, fn=0.1), MonitoringPolicySpec(), overlay, audit
        )
        assert not overlay.exists()
        assert not audit.exists()


class TestTunedOverlay:
    def test_overrides_layer_on_profile_policy(self):
        policy = apply_tuned_overrides(
            MonitoringPolicySpec(), {"batteryForecastHorizonDays": 2.0}
        )
        assert policy.batteryForecastHorizonDays == 2.0
        assert policy.compositeHealthThreshold == pytest.approx(0.35)

    def test_unknown_overlay_fields_are_ignored_on_load(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        overlay.write_text(
            json.dumps(
                {"batteryForecastHorizonDays": 2.0, "serviceOperationType": "HACK"}
            )
        )
        assert load_tuned_overrides(overlay) == {"batteryForecastHorizonDays": 2.0}

    def test_builder_applies_overlay_on_the_reviewed_policy(
        self, tmp_path, monkeypatch
    ):
        from fl_op.snapshot import policy_tuning

        monkeypatch.setattr(policy_tuning, "DATA_ROOT", tmp_path)
        (tmp_path / "quality").mkdir(parents=True)
        (tmp_path / "quality" / "monitoring-policy-tuned.json").write_text(
            json.dumps({"batteryForecastHorizonDays": 2.0})
        )
        from fl_op.snapshot.builder import SnapshotBuilder

        builder = SnapshotBuilder()
        assert builder.monitoring_policy.batteryForecastHorizonDays == 2.0

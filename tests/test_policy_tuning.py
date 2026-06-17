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
        # One 10% step down from the defaults (3.0 days, 0.35 threshold, 20% battery).
        assert overrides["batteryForecastHorizonDays"] == pytest.approx(2.7)
        assert overrides["compositeHealthThreshold"] == pytest.approx(0.315)
        assert overrides["batteryLowThresholdPct"] == pytest.approx(18.0)
        assert json.loads(overlay.read_text()) == pytest.approx(overrides)
        records = [json.loads(line) for line in audit.read_text().splitlines()]
        assert records[0]["reason"] == "false-positives"
        assert len(records[0]["adjustments"]) == 3

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


class TestLeadTimeFeedback:
    def _lead(self, late_share: float, n: int = 5) -> dict:
        return {"n_service_completions": n, "service_late_share": late_share}

    def test_late_service_completions_loosen_policy(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        audit = tmp_path / "audit.jsonl"
        overrides = auto_tune_monitoring_policy(
            _accuracy(fp=0.1, fn=0.1), MonitoringPolicySpec(), overlay, audit,
            lead_time=self._lead(0.5),
        )
        # Healthy fp/fn, but a high service late share loosens (fires earlier).
        assert overrides["batteryForecastHorizonDays"] == pytest.approx(3.3)
        assert overrides["batteryLowThresholdPct"] == pytest.approx(22.0)
        record = json.loads(audit.read_text().splitlines()[0])
        assert record["reason"] == "late-service-completions"
        assert record["service_late_share"] == pytest.approx(0.5)

    def test_lateness_below_alert_changes_nothing(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        auto_tune_monitoring_policy(
            _accuracy(), MonitoringPolicySpec(), overlay, tmp_path / "a.jsonl",
            lead_time=self._lead(0.1),
        )
        assert not overlay.exists()

    def test_too_few_completions_are_not_trusted(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        auto_tune_monitoring_policy(
            _accuracy(), MonitoringPolicySpec(), overlay, tmp_path / "a.jsonl",
            lead_time=self._lead(1.0, n=1),
        )
        assert not overlay.exists()

    def test_false_positives_and_lateness_conflict_is_skipped(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        audit = tmp_path / "audit.jsonl"
        overrides = auto_tune_monitoring_policy(
            _accuracy(fp=0.5), MonitoringPolicySpec(), overlay, audit,
            lead_time=self._lead(0.5),
        )
        assert overrides == {}
        assert not overlay.exists()
        record = json.loads(audit.read_text().splitlines()[0])
        assert record["reason"] == "conflicting-signals"


class TestPerAssetTypeTuning:
    def test_noisy_type_tuned_without_touching_global(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        audit = tmp_path / "audit.jsonl"
        accuracy = {
            "n_observed": 20.0,
            "false_positive_rate": 0.1,  # global healthy
            "false_negative_rate": 0.1,
            "by_asset_type": {
                "PROBE": {
                    "n_observed": 10.0,
                    "false_positive_rate": 0.5,  # this type fires too eagerly
                    "false_negative_rate": 0.0,
                }
            },
        }
        overrides = auto_tune_monitoring_policy(
            accuracy, MonitoringPolicySpec(), overlay, audit
        )
        probe = overrides["assetTypeOverrides"]["PROBE"]
        assert probe["batteryForecastHorizonDays"] == pytest.approx(2.7)
        assert probe["batteryLowThresholdPct"] == pytest.approx(18.0)
        # The global scalar policy was left alone (healthy global rates).
        assert "batteryForecastHorizonDays" not in overrides
        records = [json.loads(line) for line in audit.read_text().splitlines()]
        assert any(
            r["scope"] == "PROBE" and r["reason"] == "false-positives" for r in records
        )

    def test_per_type_override_is_applied_by_for_asset_type(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        accuracy = {
            "false_positive_rate": 0.0,
            "false_negative_rate": 0.0,
            "by_asset_type": {
                "PROBE": {
                    "n_observed": 10.0,
                    "false_positive_rate": 0.5,
                    "false_negative_rate": 0.0,
                }
            },
        }
        auto_tune_monitoring_policy(
            accuracy, MonitoringPolicySpec(), overlay, tmp_path / "a.jsonl"
        )
        tuned = apply_tuned_overrides(MonitoringPolicySpec(), load_tuned_overrides(overlay))
        assert tuned.for_asset_type("PROBE").batteryForecastHorizonDays == pytest.approx(2.7)
        assert tuned.batteryForecastHorizonDays == pytest.approx(3.0)


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

    def test_load_keeps_per_type_tunables_only(self, tmp_path):
        overlay = tmp_path / "tuned.json"
        overlay.write_text(
            json.dumps(
                {
                    "assetTypeOverrides": {
                        "PROBE": {
                            "batteryForecastHorizonDays": 2.0,
                            "serviceOperationType": "HACK",
                        }
                    }
                }
            )
        )
        assert load_tuned_overrides(overlay) == {
            "assetTypeOverrides": {"PROBE": {"batteryForecastHorizonDays": 2.0}}
        }

    def test_apply_merges_per_type_overrides(self):
        policy = apply_tuned_overrides(
            MonitoringPolicySpec(),
            {"assetTypeOverrides": {"PROBE": {"batteryForecastHorizonDays": 2.0}}},
        )
        assert policy.for_asset_type("PROBE").batteryForecastHorizonDays == 2.0
        # The base policy (other asset types) is untouched.
        assert policy.batteryForecastHorizonDays == pytest.approx(3.0)

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

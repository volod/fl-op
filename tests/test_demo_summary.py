"""Demo summary reporting helpers."""

from fl_op.planning.demo_summary import _excluded_observation_reading_count


def test_excluded_observation_reading_count_uses_assessment_findings() -> None:
    snapshot = {
        "quality_findings": [
            {
                "rule_id": "dq://observation/outlier",
                "action_applied": "outlier-excluded-reading_1",
            },
            {
                "rule_id": "dq://observation/future-timestamp",
                "action_applied": "future-timestamp-excluded-reading_2",
            },
            {
                "rule_id": "dq://observation/source-flagged",
                "action_applied": "source-flagged-reading_3",
            },
            {
                "rule_id": "dq://observation/metric-drift",
                "action_applied": "metric-drift",
            },
            {
                "rule_id": "dq://dataset/source-file-missing",
                "action_applied": "dataset-missing",
            },
        ]
    }

    assert _excluded_observation_reading_count(snapshot) == 3

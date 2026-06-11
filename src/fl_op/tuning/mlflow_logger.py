"""Opt-in MLflow run logging for solver runs and tuning trials.

Enabled with MLFLOW_LOGGING_ENABLED=1. The tracking URI defaults to a local
SQLite store under $DATA_DIR/mlruns so experiments are comparable across
datasets without any external service; MLFLOW_TRACKING_URI points logging at
a real tracking server in deployments. Logging is strictly best-effort: a
missing client or tracking failure degrades to a warning, never a failed run.
"""

import logging
import numbers
import os
from typing import Any, Optional

from fl_op.core import constants
from fl_op.core.paths import DATA_ROOT

logger = logging.getLogger(__name__)


def mlflow_logging_enabled() -> bool:
    return constants.MLFLOW_LOGGING_ENABLED


def _tracking_uri() -> str:
    override = os.environ.get("MLFLOW_TRACKING_URI")
    if override:
        return override
    local_store = (DATA_ROOT / constants.MLFLOW_LOCAL_DIRNAME).resolve()
    local_store.mkdir(parents=True, exist_ok=True)
    # MLflow 3.x: plain file stores are deprecated; a local SQLite backend is
    # the supported zero-service default.
    return f"sqlite:///{local_store / 'mlflow.db'}"


def log_solver_run(
    run_name: str,
    params: dict[str, Any],
    metrics: dict[str, Any],
    tags: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """Log one solver/tuning run to MLflow; returns the run id or None.

    Non-numeric metric values are dropped (MLflow metrics are floats); params
    and tags are stringified. Disabled or failing logging returns None.
    """
    if not mlflow_logging_enabled():
        return None
    try:
        import mlflow
    except ImportError:
        logger.warning("MLFLOW_LOGGING_ENABLED=1 but mlflow is not installed")
        return None

    numeric_metrics = {
        key: float(value)
        for key, value in metrics.items()
        if isinstance(value, numbers.Real) and not isinstance(value, bool)
    }
    try:
        mlflow.set_tracking_uri(_tracking_uri())
        mlflow.set_experiment(constants.MLFLOW_EXPERIMENT_NAME)
        with mlflow.start_run(run_name=run_name) as run:
            if params:
                mlflow.log_params({k: str(v) for k, v in params.items()})
            if numeric_metrics:
                mlflow.log_metrics(numeric_metrics)
            if tags:
                mlflow.set_tags({k: str(v) for k, v in tags.items()})
            return run.info.run_id
    except Exception as exc:  # noqa: BLE001 - tracking must never fail a run
        logger.warning("MLflow logging failed: %s", exc)
        return None

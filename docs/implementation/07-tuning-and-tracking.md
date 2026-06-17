[Implementation guide](../current-implementation.md) > Parameter tuning and experiment tracking

# Parameter tuning and experiment tracking

- `fl-op tune` (`tuning/optuna_tuner.py`) runs a seeded Optuna TPE study over
  the tunable solver parameters (`solver/parameters.py:SolverParameters`:
  cluster target size, greedy score weights, per-cluster time limit, LNS
  budget, and rolling change penalty) against recorded KPI baselines built at
  the trial-scale time budget. Additional datasets (`--extra-data`) are scored
  with workload weights derived from task counts, and, by default, the study
  records a multi-objective frontier: maximize business objective (margin minus
  unassigned penalty exposure), minimize plan-instability penalty, and
  minimize wall time. By default the periodic chain's instability is zero (no
  previous revision); `--measure-instability` instead scores real plan churn by
  re-solving each case after removing its busiest prime mover (a one-event
  rolling perturbation) and counting avoidable assignment changes x
  `rolling_change_penalty` (which then joins the search space). Parallel workers
  (`--jobs` or TUNE_N_JOBS) use Optuna RDB storage; `--jobs 0` auto-selects the
  worker count from CPU count and available memory versus a per-dataset job
  footprint (bigger datasets reduce parallelism). Without an explicit URI,
  `n_jobs > 1` creates `study.db` in the tuning run directory. Artifacts:
  `baseline.json`, `trials.json`, `best_params.json` under
  `$DATA_DIR/tune/<ts>/`, including per-dataset case scores, workload-weight
  contributions, and the Pareto frontier.
- `fl-op tune-promote --best-params <run>/best_params.json`
  (`tuning/solver_profile.py`) writes the reviewed tuned solver profile
  overlay. Without scope flags it writes the legacy shared artifact
  `$DATA_DIR/tune/solver-parameters-tuned.json`. With `--domain`, `--profile`,
  and `--adapter-version`, it writes a scoped artifact under
  `$DATA_DIR/tune/<domain>/<profile>/<adapter-version>/solver-parameters-tuned.json`
  and records optional `--expires-at` metadata. Periodic and rolling adapters
  layer matching scoped artifacts onto the active profile's allocation policy
  when no explicit `SolverParameters` were passed. Drone logistics reads only
  its checked-in tuning file and matching scoped overlays, so the shared legacy
  overlay does not silently alter drone behavior.
- Opt-in MLflow logging (`tuning/mlflow_logger.py`, MLFLOW_LOGGING_ENABLED):
  tuning trials, the baseline, periodic plans, and the final revision of
  each rolling run are logged with KPIs, version dimensions, and the
  solve-telemetry summary; local SQLite store under `$DATA_DIR/mlruns` by
  default, MLFLOW_TRACKING_URI for a real server. Best-effort only: a
  tracking failure degrades to a warning, never a failed run.
</content>

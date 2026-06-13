import os

# ---------------------------------------------------------------------------
# Synthetic data generation defaults
# ---------------------------------------------------------------------------

# Quickstart-scale defaults; override via VEHICLES / IMPLEMENTS / ORDERS /
# DEPOTS environment variables (set in .env or exported in the shell).
DEFAULT_GENERATE_VEHICLES: int = int(os.environ.get("VEHICLES", "100"))
DEFAULT_GENERATE_IMPLEMENTS: int = int(os.environ.get("IMPLEMENTS", "400"))
DEFAULT_GENERATE_ORDERS: int = int(os.environ.get("ORDERS", "250"))
DEFAULT_GENERATE_DEPOTS: int = int(os.environ.get("DEPOTS", "50"))

# ---------------------------------------------------------------------------
# Pre-allocation
# ---------------------------------------------------------------------------

# V-I pairs reserved per resource to support parallel cluster solving without
# hoarding the whole fleet in the first high-penalty clusters.
PREALLOC_ORDERS_PER_RESOURCE: int = int(os.environ.get("PREALLOC_ORDERS_PER_RESOURCE", "5"))
PREALLOC_MIN_RESOURCES_PER_MULTI_ORDER_CLUSTER: int = int(
    os.environ.get("PREALLOC_MIN_RESOURCES_PER_MULTI_ORDER_CLUSTER", "2")
)

# ---------------------------------------------------------------------------
# Global pre-allocation assignment model
# ---------------------------------------------------------------------------
# A small CP-SAT assignment model replaces the greedy per-cluster reservation
# loop: scarce vehicles, implements, and operators are assigned across all
# clusters at once. Greedy remains the fallback when the model is disabled,
# oversized, or fails to return a solution within its time budget.

# Master switch for the CP-SAT pre-allocation model (1 = on, 0 = greedy only).
GLOBAL_ASSIGNMENT_ENABLED: bool = bool(int(os.environ.get("GLOBAL_ASSIGNMENT_ENABLED", "1")))

# Wall-clock budget for the CP-SAT assignment solve.
GLOBAL_ASSIGNMENT_TIME_LIMIT_S: float = float(
    os.environ.get("GLOBAL_ASSIGNMENT_TIME_LIMIT_S", "10.0")
)

# Stop the assignment solve when within this relative gap of the bound. One
# allocation is worth about 1/n_allocations of the objective (count-first
# rewards), so a 0.1% gap never sacrifices an allocation, only tie-break
# score polish.
GLOBAL_ASSIGNMENT_RELATIVE_GAP: float = float(
    os.environ.get("GLOBAL_ASSIGNMENT_RELATIVE_GAP", "0.001")
)

# Best-scoring candidate (vehicle, implement) pairs kept per cluster; bounds
# the model so it stays a small assignment problem, not a full matching.
GLOBAL_ASSIGNMENT_CANDIDATES_PER_CLUSTER: int = int(
    os.environ.get("GLOBAL_ASSIGNMENT_CANDIDATES_PER_CLUSTER", "100")
)

# Diversity caps inside one cluster's truncated candidate list. Without them
# the nearest vehicle scores best across every implement and floods the
# top-K, leaving the model no alternative vehicles when neighbouring
# clusters contest the same machine.
GLOBAL_ASSIGNMENT_PAIRS_PER_VEHICLE: int = int(
    os.environ.get("GLOBAL_ASSIGNMENT_PAIRS_PER_VEHICLE", "2")
)
GLOBAL_ASSIGNMENT_PAIRS_PER_IMPLEMENT: int = int(
    os.environ.get("GLOBAL_ASSIGNMENT_PAIRS_PER_IMPLEMENT", "2")
)

# Total candidate count above which the model is skipped in favour of greedy.
GLOBAL_ASSIGNMENT_MAX_MODEL_CANDIDATES: int = int(
    os.environ.get("GLOBAL_ASSIGNMENT_MAX_MODEL_CANDIDATES", "20000")
)

# Integer objective units per score unit (CP-SAT needs integer coefficients).
GLOBAL_ASSIGNMENT_SCORE_SCALE: int = int(
    os.environ.get("GLOBAL_ASSIGNMENT_SCORE_SCALE", "1000")
)

# Fixed seed so equal-objective assignments resolve identically across runs.
GLOBAL_ASSIGNMENT_RANDOM_SEED: int = int(
    os.environ.get("GLOBAL_ASSIGNMENT_RANDOM_SEED", "7")
)

# Count-vs-margin tradeoff of the global assignment objective: 1.0 keeps the
# count-first construction (allocate as many bundles as the limits admit,
# scores only break ties); 0.0 maximizes summed candidate scores regardless
# of how many bundles are allocated. Profiles override it via
# allocationPolicy.countPriority.
GLOBAL_ASSIGNMENT_COUNT_PRIORITY: float = float(
    os.environ.get("GLOBAL_ASSIGNMENT_COUNT_PRIORITY", "1.0")
)

# Objective reward per cluster operation type the assigned operator is
# certified for, and the smaller tiebreak bonus for an operator whose home
# depot matches the cluster depot.
OPERATOR_COVERAGE_REWARD: int = 100
OPERATOR_DEPOT_MATCH_REWARD: int = 1

# Maximum operator-reward discount for a fully-held operator; kept below
# OPERATOR_COVERAGE_REWARD so certified coverage always dominates and holds
# only break ties among equally covering operators.
OPERATOR_HOLD_DISCOUNT_REWARD: int = 50

# ---------------------------------------------------------------------------
# Large Neighbourhood Search improvement pass
# ---------------------------------------------------------------------------
# Optional second routing solve for high-value clusters: continues from the
# first OR-Tools solution with guided local search and LNS operators.

# Opt-in switch for the per-cluster LNS improvement pass (1 = on).
CLUSTER_LNS_ENABLED: bool = bool(int(os.environ.get("CLUSTER_LNS_ENABLED", "0")))

# Additional wall-clock budget for the improvement solve, per cluster.
CLUSTER_LNS_TIME_LIMIT_S: int = int(os.environ.get("CLUSTER_LNS_TIME_LIMIT_S", "10"))

# A cluster qualifies as high-value when the sum of its tasks' lateness
# penalties (EUR/day) reaches this threshold.
CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY: float = float(
    os.environ.get("CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY", "1000.0")
)

# ---------------------------------------------------------------------------
# Weather restriction thresholds (hard safety constraints — not env-tunable)
# ---------------------------------------------------------------------------

# Maximum sustained wind speed above which all field operations are prohibited.
WEATHER_WIND_MAX_MS: float = 10.0  # m/s

# Maximum hourly rainfall above which field operations are prohibited.
WEATHER_RAIN_MAX_MM: float = 5.0  # mm/h

# Maximum volumetric soil moisture above which heavy machinery is prohibited.
WEATHER_SOIL_MOISTURE_MAX_PCT: float = 85.0  # % volumetric water content

# ---------------------------------------------------------------------------
# Vehicle-implement compatibility
# ---------------------------------------------------------------------------

# Implement may draw up to this % above vehicle rated power (short-duration peaks).
POWER_MARGIN_PCT: float = float(os.environ.get("POWER_MARGIN_PCT", "10.0"))

# Compatibility-matrix cache: matrices are keyed by a content hash of the
# power capabilities they derive from (plus the margin), so a repeated solve
# over the same fleet skips the rebuild. Safe by construction: any input
# change changes the key.
COMPAT_MATRIX_CACHE_ENABLED: bool = bool(
    int(os.environ.get("COMPAT_MATRIX_CACHE_ENABLED", "1"))
)

# Cache directory under DATA_DIR, and how many cached matrices to retain
# (oldest entries beyond the bound are pruned).
COMPAT_MATRIX_CACHE_DIRNAME: str = "cache/compat-matrix"
COMPAT_MATRIX_CACHE_MAX_ENTRIES: int = int(
    os.environ.get("COMPAT_MATRIX_CACHE_MAX_ENTRIES", "8")
)

# Preprocessing cache: operation-filtered V-I candidates and cluster specs are
# keyed by stable content hashes over canonical solver rows. This extends the
# compatibility-matrix cache to the next expensive deterministic preprocessing
# stages.
PREPROCESSING_CACHE_ENABLED: bool = bool(
    int(os.environ.get("PREPROCESSING_CACHE_ENABLED", "1"))
)
PREPROCESSING_CACHE_DIRNAME: str = "cache/preprocessing"
PREPROCESSING_CACHE_MAX_ENTRIES: int = int(
    os.environ.get("PREPROCESSING_CACHE_MAX_ENTRIES", "16")
)

# Serving feasibility cache: exact /feasibility requests are cached by the
# dataset/schedule/order content hash. The endpoint still sees file changes
# because the key includes source bytes, not just paths.
FEASIBILITY_CACHE_ENABLED: bool = bool(
    int(os.environ.get("FEASIBILITY_CACHE_ENABLED", "1"))
)
FEASIBILITY_CACHE_DIRNAME: str = "cache/feasibility"
FEASIBILITY_CACHE_MAX_ENTRIES: int = int(
    os.environ.get("FEASIBILITY_CACHE_MAX_ENTRIES", "128")
)

# Maximum V-I candidate pairs per order before routing model construction.
MAX_PAIRS_PER_ORDER: int = int(os.environ.get("MAX_PAIRS_PER_ORDER", "30"))

# ---------------------------------------------------------------------------
# Geographic / clustering
# ---------------------------------------------------------------------------

# WGS-84 mean Earth radius used for haversine distance calculations.
EARTH_RADIUS_KM: float = 6371.0

# Travel-network locations above which the all-pairs shortest-path closure is
# skipped (the composed lookup grows with the square of the node count);
# direct links still apply, pairs without one fall back to haversine.
TRAVEL_NETWORK_MAX_COMPOSE_NODES: int = int(
    os.environ.get("TRAVEL_NETWORK_MAX_COMPOSE_NODES", "1000")
)

# Target number of orders per geographic cluster fed to one solver worker.
CLUSTER_TARGET_SIZE: int = int(os.environ.get("CLUSTER_TARGET_SIZE", "50"))

# ---------------------------------------------------------------------------
# Solver time limits
# ---------------------------------------------------------------------------

# Wall-clock seconds per cluster worker before marking the cluster infeasible.
CLUSTER_SOLVE_TIME_LIMIT_S: int = int(os.environ.get("CLUSTER_SOLVE_TIME_LIMIT_S", "60"))

# Routing-model scheduling horizon: deadlines, workable windows, and location
# restriction windows are clamped to this many seconds from now.
ROUTING_HORIZON_S: int = 30 * 24 * 3600

# Vehicle route-load capacity assigned when a prime mover declares none
# (capacity dimension upper bound that can never bind).
VEHICLE_LOAD_UNLIMITED_KG: float = 1.0e9

# Depot reloads (multi-trip routes). When enabled and any cluster task
# demands a load, each routing vehicle gets one optional reload visit at the
# depot that resets the load dimensions, so demand beyond one vehicle fill
# can be served in several trips instead of dropping tasks.
DEPOT_RELOAD_ENABLED: bool = bool(int(os.environ.get("DEPOT_RELOAD_ENABLED", "1")))

# Handling time of one depot reload visit (loading at the ramp).
DEPOT_RELOAD_SERVICE_S: int = int(os.environ.get("DEPOT_RELOAD_SERVICE_S", "900"))

# Number of parallel solver workers. 0 = auto: min(n_clusters, cpu_count,
# memory-derived cap). An explicit positive value always wins.
SOLVER_WORKERS: int = int(os.environ.get("SOLVER_WORKERS", "0"))

# ---------------------------------------------------------------------------
# Memory-aware pool sizing (auto mode only)
# ---------------------------------------------------------------------------
# Each spawned worker pays a base footprint (interpreter + OR-Tools import)
# plus a model footprint estimated from the largest cluster: the routing model
# holds the time matrix and one transit callback per routing vehicle, scaling
# with n_nodes^2 x (n_vehicles + 1) cells.

# Baseline resident memory of one spawned solver worker before any model.
SOLVER_WORKER_BASE_MEMORY_MB: float = float(
    os.environ.get("SOLVER_WORKER_BASE_MEMORY_MB", "300.0")
)

# Estimated bytes per routing-model matrix cell (matrix entry plus callback
# and search-state overhead, measured order of magnitude).
SOLVER_MODEL_BYTES_PER_CELL: float = float(
    os.environ.get("SOLVER_MODEL_BYTES_PER_CELL", "64.0")
)

# Share of available memory kept free for the parent process and OS.
SOLVER_MEMORY_HEADROOM_PCT: float = float(
    os.environ.get("SOLVER_MEMORY_HEADROOM_PCT", "20.0")
)

# Feedback from completed worker solves. Per-cluster telemetry records worker
# RSS and LNS objective deltas; later runs use the retained summary to calibrate
# auto worker-count memory estimates and LNS improvement budgets.
SOLVER_FEEDBACK_ENABLED: bool = bool(int(os.environ.get("SOLVER_FEEDBACK_ENABLED", "1")))
SOLVER_FEEDBACK_DIRNAME: str = "cache/solver-feedback"
SOLVER_MEMORY_FEEDBACK_FILENAME: str = "worker-memory.json"
SOLVER_LNS_FEEDBACK_FILENAME: str = "lns-budget.json"

# Guardrails for adapting the LNS improvement budget from observed objective
# deltas. The constant LNS time limit remains the nominal budget; feedback
# scales it between these multipliers.
CLUSTER_LNS_FEEDBACK_REFERENCE_DELTA: float = float(
    os.environ.get("CLUSTER_LNS_FEEDBACK_REFERENCE_DELTA", "1000.0")
)
CLUSTER_LNS_MIN_BUDGET_MULTIPLIER: float = float(
    os.environ.get("CLUSTER_LNS_MIN_BUDGET_MULTIPLIER", "0.5")
)
CLUSTER_LNS_MAX_BUDGET_MULTIPLIER: float = float(
    os.environ.get("CLUSTER_LNS_MAX_BUDGET_MULTIPLIER", "3.0")
)

# ---------------------------------------------------------------------------
# Cost rates
# ---------------------------------------------------------------------------
# Cost rates are data entities (canonical cost-rate contract): when the active
# snapshot carries a valid rate for a resource code, that rate wins. The
# constants below are the engine fallback for unpriced resources.

# Canonical resource codes a cost-rate row may price (cost-rate.rateType).
RATE_TYPE_FUEL: str = "fuel"
RATE_TYPE_MATERIAL: str = "fertilizer"

# Canonical unit of depot material inventory and of the material-reservation
# quantities derived from it (urn:xopt:inventory:fertilizer is kept in kg).
MATERIAL_INVENTORY_CANONICAL_UNIT: str = "kg"

# Diesel cost per litre used for repositioning cost estimation in greedy scoring.
FUEL_COST_EUR_PER_L: float = float(os.environ.get("FUEL_COST_EUR_PER_L", "1.45"))

# Liquid fertilizer cost per kilogram for inventory arc cost estimation.
FERTILIZER_COST_EUR_PER_KG: float = float(os.environ.get("FERTILIZER_COST_EUR_PER_KG", "0.55"))

# Share of a related-equipment material tank assumed consumed by one task.
RELATED_MATERIAL_FILL_RATIO: float = 0.8

# ---------------------------------------------------------------------------
# Greedy scoring weights
# ---------------------------------------------------------------------------

# Weight on gross margin estimate in the greedy score.
SCORE_WEIGHT_MARGIN: float = float(os.environ.get("SCORE_WEIGHT_MARGIN", "1.0"))

# Weight on repositioning cost penalty (subtracted from margin).
SCORE_WEIGHT_REPOSITION: float = float(os.environ.get("SCORE_WEIGHT_REPOSITION", "1.0"))

# ---------------------------------------------------------------------------
# Canonical solver-row defaults
# ---------------------------------------------------------------------------
# Default capability values applied when a canonical solver row does not carry a
# field (absent from the contract projection, or from a partial query order).
# These are the single source of truth for the defaults the solver-row
# dataclasses apply; no solver module hard-codes these literals at the access site.

# Prime-mover ground travel speed when none is projected.
TRAVEL_SPEED_DEFAULT_KMH: float = 15.0

# Prime-mover diesel burn rate when none is projected.
FUEL_CONSUMPTION_DEFAULT_L_PER_H: float = 18.0

# Fallback gross revenue per hectare when an order carries no explicit revenue.
FALLBACK_REVENUE_EUR_PER_HA: float = 200.0

# Related-equipment effective working width when none is projected.
RELATED_WORKING_WIDTH_DEFAULT: float = 12.0

# Related-equipment operating speed when none is projected.
RELATED_OPERATING_SPEED_DEFAULT: float = 8.0

# ---------------------------------------------------------------------------
# Parameter tuning (Optuna) and experiment tracking (MLflow)
# ---------------------------------------------------------------------------

# Optuna trials per tuning run, and the TPE sampler seed for reproducibility.
TUNE_N_TRIALS: int = int(os.environ.get("TUNE_N_TRIALS", "20"))
TUNE_SEED: int = int(os.environ.get("TUNE_SEED", "7"))
# Parallel Optuna workers. Values above 1 use an RDB storage backend so trials
# can be coordinated safely across worker threads/processes or repeated CLI
# invocations.
TUNE_N_JOBS: int = int(os.environ.get("TUNE_N_JOBS", "1"))
TUNE_STORAGE_URI: str = os.environ.get("TUNE_STORAGE_URI", "")

# Per-cluster solve budget used for the tuning baseline and as the search
# upper bound: trials run at experiment scale, not production scale, so the
# baseline uses the same budget for comparability.
TUNE_TIME_LIMIT_MIN_S: int = int(os.environ.get("TUNE_TIME_LIMIT_MIN_S", "5"))
TUNE_TIME_LIMIT_MAX_S: int = int(os.environ.get("TUNE_TIME_LIMIT_MAX_S", "30"))

# Search bounds for the cluster target size and the greedy score weights
# (weights sampled log-uniform around the 1.0 defaults).
TUNE_CLUSTER_TARGET_SIZE_MIN: int = int(os.environ.get("TUNE_CLUSTER_TARGET_SIZE_MIN", "10"))
TUNE_CLUSTER_TARGET_SIZE_MAX: int = int(os.environ.get("TUNE_CLUSTER_TARGET_SIZE_MAX", "80"))
TUNE_SCORE_WEIGHT_MIN: float = float(os.environ.get("TUNE_SCORE_WEIGHT_MIN", "0.1"))
TUNE_SCORE_WEIGHT_MAX: float = float(os.environ.get("TUNE_SCORE_WEIGHT_MAX", "5.0"))

# Reviewed tuned solver-parameter artifact. `fl-op tune-promote` writes this
# under DATA_DIR/tune; plan adapters load it as an overlay on the checked-in
# profile defaults without modifying the profile document itself.
TUNED_SOLVER_PARAMETERS_FILENAME: str = "solver-parameters-tuned.json"

# Opt-in MLflow run logging. The tracking URI defaults to a local file store
# under DATA_DIR; MLFLOW_TRACKING_URI overrides it.
MLFLOW_LOGGING_ENABLED: bool = bool(int(os.environ.get("MLFLOW_LOGGING_ENABLED", "0")))
MLFLOW_EXPERIMENT_NAME: str = os.environ.get("MLFLOW_EXPERIMENT_NAME", "fl-op")
MLFLOW_LOCAL_DIRNAME: str = "mlruns"

# ---------------------------------------------------------------------------
# Serving (thin service API)
# ---------------------------------------------------------------------------

# Bind address and port for `fl-op serve`. The default binds loopback only;
# set SERVE_HOST=0.0.0.0 explicitly to expose the API beyond the host.
SERVE_HOST: str = os.environ.get("SERVE_HOST", "127.0.0.1")
SERVE_PORT: int = int(os.environ.get("SERVE_PORT", "8000"))

# Optional bearer token for the serving API. Non-local binds require it; local
# loopback serving may omit it for developer use. Health remains public.
SERVE_AUTH_TOKEN: str = os.environ.get("SERVE_AUTH_TOKEN", "")

# Artifact root the serving API reads. Defaults to DATA_DIR, but can point at a
# shared mounted filesystem so multiple API instances serve the same plans and
# datasets without changing route shapes.
SERVE_ARTIFACT_ROOT: str = os.environ.get("SERVE_ARTIFACT_ROOT", "")

# ---------------------------------------------------------------------------
# Event-bus ingestion (broker-backed execution events)
# ---------------------------------------------------------------------------

# Execution-event source for rolling planning: "jsonl" reads the --events
# file (the development default); "kafka" consumes the configured broker
# topic instead, for deployments with a real event bus.
EVENT_SOURCE_KIND: str = os.environ.get("EVENT_SOURCE_KIND", "jsonl")
EVENT_BROKER_BOOTSTRAP_SERVERS: str = os.environ.get(
    "EVENT_BROKER_BOOTSTRAP_SERVERS", "localhost:9092"
)
EVENT_BROKER_TOPIC: str = os.environ.get("EVENT_BROKER_TOPIC", "fl-op.execution-events")
EVENT_BROKER_GROUP_ID: str = os.environ.get("EVENT_BROKER_GROUP_ID", "fl-op-rolling")
# Poll timeout per broker fetch, and how many consecutive empty polls end a
# bounded consumption (a rolling run drains the visible backlog, then stops).
EVENT_BROKER_POLL_TIMEOUT_S: float = float(
    os.environ.get("EVENT_BROKER_POLL_TIMEOUT_S", "1.0")
)
EVENT_BROKER_MAX_EMPTY_POLLS: int = int(
    os.environ.get("EVENT_BROKER_MAX_EMPTY_POLLS", "3")
)

# Durable event-id deduplication for broker-backed rolling runs: ids of
# events whose revisions were published are persisted so a redelivery after
# a process restart is suppressed instead of producing a duplicate revision.
# The JSONL development source replays files intentionally and never uses
# the store.
EVENT_DEDUP_STORE_ENABLED: bool = bool(
    int(os.environ.get("EVENT_DEDUP_STORE_ENABLED", "1"))
)

# Directory (under DATA_DIR) and filename of the durable event-id log.
STREAM_STATE_DIRNAME: str = "stream"
EVENT_DEDUP_FILENAME: str = "event-dedup.ids"

# Retention of the id log: past this many ids it is compacted in place to
# the newest ones (atomic replace), bounding the file across deployments.
EVENT_DEDUP_MAX_IDS: int = int(os.environ.get("EVENT_DEDUP_MAX_IDS", "10000"))

# ---------------------------------------------------------------------------
# JSON artifact schema
# ---------------------------------------------------------------------------

# Bumped when the structure of any output JSON file changes in a breaking way.
ARTIFACT_SCHEMA_VERSION: str = "1.0"

# ---------------------------------------------------------------------------
# Physical data format
# ---------------------------------------------------------------------------

# Default format for dataset files written by generate-data and read by the pipeline.
DEFAULT_DATA_FORMAT: str = "avro"

# All supported physical formats for dataset I/O (not schema generation).
SUPPORTED_DATA_FORMATS: frozenset[str] = frozenset({"csv", "avro", "parquet"})

# ---------------------------------------------------------------------------
# x-optimization extension namespace
# ---------------------------------------------------------------------------

# ODCS custom-property name for optimization semantics (camelCase per ODCS convention).
XOPT_ODCS_PROPERTY: str = "xOptimization"

# ODCS custom-property name for schema-level generation hints (namespace, record name, etc.).
SCHEMA_GEN_PROPERTY: str = "schemaGeneration"

# ODCS custom-property name for field-level generation hints (aliases, defaults, proto field numbers).
FIELD_GEN_PROPERTY: str = "fieldGeneration"

# Version of the optimization extension itself, independent of Avro/ODCS/
# mapping/profile/adapter versions.
XOPT_EXTENSION_VERSION: str = "0.1.0"

# apiVersion stamped on OptimizationProfile documents.
XOPT_API_VERSION: str = "x-optimization/v0.1.0"

# Semantic-model URN for the domain-agnostic canonical optimization model. Domain
# mapping packs project their physical schemas onto this model.
URN_MODEL_CANONICAL: str = "urn:xopt:model:canonical:0.1.0"

# ODCS custom-property names used by the canonical optimization-model contracts.
CANONICAL_ENTITY_PROPERTY: str = "canonicalEntity"
CANONICAL_BINDING_PROPERTY: str = "canonicalBinding"

# Filename of the canonical-model index inside the canonical contract root.
CANONICAL_MODEL_FILENAME: str = "model.yaml"

# URN prefix for capability semantic terms.
URN_CAPABILITY_PREFIX: str = "urn:xopt:capability:"

# ---------------------------------------------------------------------------
# Planning horizons and rolling-dispatch windows
# ---------------------------------------------------------------------------

# Periodic (batch) planning horizon length.
PERIODIC_HORIZON_DAYS: int = int(os.environ.get("PERIODIC_HORIZON_DAYS", "7"))

# Rolling (stream) dispatch horizon length.
ROLLING_HORIZON_HOURS: int = int(os.environ.get("ROLLING_HORIZON_HOURS", "48"))

# Tasks whose planned start falls within this window are frozen (not replanned).
FREEZE_WINDOW_MINUTES: int = int(os.environ.get("FREEZE_WINDOW_MINUTES", "60"))

# Horizon over which a held asset's free capacity is measured for hold-aware
# pre-allocation scoring (the rolling dispatch horizon); busy time beyond it
# does not discount candidates.
HOLD_CAPACITY_HORIZON_S: int = ROLLING_HORIZON_HOURS * 3600

# Maximum CP-SAT/routing feedback iterations before stopping.
MAX_ASSIGNMENT_ROUTING_ITERATIONS: int = int(
    os.environ.get("MAX_ASSIGNMENT_ROUTING_ITERATIONS", "3")
)

# Score penalty applied per assignment changed after the freeze window.
DEFAULT_CHANGE_PENALTY: int = int(os.environ.get("DEFAULT_CHANGE_PENALTY", "1000"))

# ---------------------------------------------------------------------------
# Integer scaling units for solver quantities
# ---------------------------------------------------------------------------

# Versioned so a change to any scale factor forces a profile/adapter bump.
INTEGER_SCALING_POLICY_VERSION: str = "1.0.0"

SCALE_TIME_UNITS_PER_MINUTE: int = 1          # internal time unit = minutes
SCALE_DISTANCE_UNITS_PER_METER: int = 1       # internal distance unit = meters
SCALE_POWER_UNITS_PER_KW: int = 10            # deciwatts of kW (0.1 kW resolution)
SCALE_FUEL_UNITS_PER_LITER: int = 1000        # milliliters
SCALE_MASS_UNITS_PER_KG: int = 1000           # grams
SCALE_MONEY_UNITS_PER_EUR: int = 100          # euro cents (smallest currency unit)
SCALE_PROBABILITY_BASIS_POINTS: int = 10000   # 1.0 == 10000 basis points

# ---------------------------------------------------------------------------
# Solver-adapter identity
# ---------------------------------------------------------------------------

ADAPTER_ORTOOLS_PERIODIC_ID: str = "ortools-periodic"
ADAPTER_ORTOOLS_ROLLING_ID: str = "ortools-rolling"
ADAPTER_VERSION: str = "0.1.0"

# Snapshot/adapter compatibility version recorded on every snapshot and plan.
ADAPTER_COMPATIBILITY_VERSION: str = "0.1.0"

# Version dimensions stamped onto snapshots and plans for governance/lineage.
MAPPING_VERSION: str = "1.0.0"
OPTIMIZATION_PROFILE_VERSION: str = "0.1.0"

# ---------------------------------------------------------------------------
# Canonical snapshot inputs
# ---------------------------------------------------------------------------

# Canonical entities that constitute planning-snapshot state. The snapshot
# builder maps every active-domain contract whose mapping targets one of these;
# event-envelope entities (execution-event) drive the stream layer instead.
SNAPSHOT_INPUT_ENTITIES: tuple[str, ...] = (
    "asset",
    "location",
    "task",
    "forecast",
    "observation",
    "commitment",
    "travel-link",
    "cost-rate",
)

# ---------------------------------------------------------------------------
# Stationary-equipment monitoring policy
# ---------------------------------------------------------------------------
# Thresholds the monitoring policy applies to derive service tasks for
# stationary assets (sensor stations, fixed road/field equipment) from their
# latest observations and maintenance state.

# Canonical metric codes observations must carry for the engine to interpret
# them; domain sources normalize their metric vocabulary to these values.
METRIC_BATTERY_LEVEL: str = "battery-level"
METRIC_HEALTH_STATUS: str = "health-status"

# Telemetry-derived task progress: an observation carrying this canonical
# metric for a task id reports the completed share of the task's work in
# percent; reaching the completion percentage finishes the task exactly like
# a task.completed event.
METRIC_WORK_PROGRESS: str = "work-progress"
WORK_PROGRESS_COMPLETE_PCT: float = 100.0

# Battery level (percent) at or below which a service visit is required.
BATTERY_LOW_THRESHOLD_PCT: float = float(os.environ.get("BATTERY_LOW_THRESHOLD_PCT", "20.0"))

# Battery level (percent) at or below which the asset has effectively failed:
# the derived service task is escalated (the prognosis was too optimistic).
BATTERY_CRITICAL_THRESHOLD_PCT: float = float(
    os.environ.get("BATTERY_CRITICAL_THRESHOLD_PCT", "5.0")
)

# Predictive horizon: if the battery drain trend projects the level to cross
# the low threshold within this many days, a service task is derived early.
BATTERY_FORECAST_HORIZON_DAYS: float = float(
    os.environ.get("BATTERY_FORECAST_HORIZON_DAYS", "3.0")
)

# Canonical operation type stamped on monitoring-derived service tasks. Domains
# that want such tasks solvable must declare assets compatible with it.
EQUIPMENT_SERVICE_OPERATION: str = "EQUIPMENT_SERVICE"

# Scheduling attributes of derived service tasks.
SERVICE_TASK_PRIORITY_CLASS: int = int(os.environ.get("SERVICE_TASK_PRIORITY_CLASS", "2"))
SERVICE_TASK_DEADLINE_DAYS: int = int(os.environ.get("SERVICE_TASK_DEADLINE_DAYS", "3"))
SERVICE_TASK_PENALTY_EUR_PER_DAY: float = float(
    os.environ.get("SERVICE_TASK_PENALTY_EUR_PER_DAY", "150.0")
)

# Scheduling attributes of escalated service tasks (asset failed earlier than
# the prognosis: critical battery or failed health).
SERVICE_TASK_ESCALATED_PRIORITY_CLASS: int = int(
    os.environ.get("SERVICE_TASK_ESCALATED_PRIORITY_CLASS", "1")
)
SERVICE_TASK_ESCALATED_DEADLINE_DAYS: int = int(
    os.environ.get("SERVICE_TASK_ESCALATED_DEADLINE_DAYS", "1")
)

# Nominal work-area equivalent assigned to a service visit so duration/cost
# estimation (which is area-driven for field work) yields a small fixed effort.
SERVICE_TASK_NOMINAL_AREA_HA: float = float(
    os.environ.get("SERVICE_TASK_NOMINAL_AREA_HA", "1.0")
)

# Explicit effort of a service visit; consumed through the canonical
# service-duration term, overriding any quantity-driven duration estimate.
SERVICE_TASK_DURATION_MINUTES: float = float(
    os.environ.get("SERVICE_TASK_DURATION_MINUTES", "45.0")
)

# ---------------------------------------------------------------------------
# Statistical observation assessment
# ---------------------------------------------------------------------------
# Parameters for separating sensor faults from real signals before the
# monitoring policy derives service tasks from observation series.

# Modified z-score (MAD-based) above which a reading is treated as an outlier.
OUTLIER_MAD_Z_THRESHOLD: float = float(os.environ.get("OUTLIER_MAD_Z_THRESHOLD", "3.5"))

# Scale factor relating the median absolute deviation to the standard
# deviation of a normal distribution (the modified z-score convention).
MAD_NORMAL_CONSISTENCY: float = 0.6745

# Minimum readings in a numeric series before outlier statistics apply.
OUTLIER_MIN_SERIES_READINGS: int = int(os.environ.get("OUTLIER_MIN_SERIES_READINGS", "5"))

# Battery level rising by more than this between consecutive readings without
# a service visit marks the series as a suspected instrument fault.
BATTERY_RISE_FAULT_PCT: float = float(os.environ.get("BATTERY_RISE_FAULT_PCT", "5.0"))

# A non-zero value repeated this many consecutive times marks a frozen sensor.
# Constant zero is excluded: a dead battery legitimately reads zero.
FROZEN_SERIES_MIN_READINGS: int = int(os.environ.get("FROZEN_SERIES_MIN_READINGS", "6"))

# Confidence assigned to every reading of a fault-suspected series; below any
# reasonable monitoring gate, so suspect series never derive service tasks.
SUSPECT_SERIES_CONFIDENCE: float = 0.0

# Minimum observation confidence the monitoring policy requires before acting.
MIN_OBSERVATION_CONFIDENCE: float = float(
    os.environ.get("MIN_OBSERVATION_CONFIDENCE", "0.5")
)

# Drift detection: mean shift between the two series halves exceeding this
# many MADs flags the metric as drifting (calibration needed).
DRIFT_MAD_MULTIPLIER: float = float(os.environ.get("DRIFT_MAD_MULTIPLIER", "3.0"))

# Minimum readings in a series before drift statistics apply.
DRIFT_MIN_SERIES_READINGS: int = int(os.environ.get("DRIFT_MIN_SERIES_READINGS", "8"))

# Metrics expected to trend by design (state of charge drains); exempt from
# drift detection so normal behavior is not flagged as calibration need.
DRIFT_EXEMPT_METRICS: tuple[str, ...] = (METRIC_BATTERY_LEVEL,)

# Share of bad readings (outliers + suspect) per source contract above which
# the source is reported as degraded.
OBSERVATION_ERROR_RATE_ALERT: float = float(
    os.environ.get("OBSERVATION_ERROR_RATE_ALERT", "0.2")
)

# Confidence factor per source quality flag; readings whose factor is zero are
# excluded from planning. Unknown flags are trusted (factor 1.0).
QUALITY_FLAG_CONFIDENCE: dict[str, float] = {
    "ok": 1.0,
    "suspect": 0.5,
    "bad": 0.0,
    "error": 0.0,
}

# Observation retention: readings older than this window (relative to the
# planning effective time) are dropped from snapshots.
OBSERVATION_RETENTION_DAYS: float = float(
    os.environ.get("OBSERVATION_RETENTION_DAYS", "14.0")
)

# Maximum readings kept per (entity, metric) series; longer series are
# aggregated into time windows (one representative reading per window, oldest
# and newest readings always preserved).
OBSERVATION_MAX_SERIES_READINGS: int = int(
    os.environ.get("OBSERVATION_MAX_SERIES_READINGS", "32")
)

# Clock-skew tolerance: a reading whose observed-at lies further than this
# ahead of planning time is excluded (its station clock cannot be trusted).
CLOCK_SKEW_TOLERANCE_S: float = float(os.environ.get("CLOCK_SKEW_TOLERANCE_S", "300.0"))

# Arrival-order timestamp regression beyond this many seconds flags the series
# (out-of-order delivery is normal; a large regression hints at clock trouble).
TIMESTAMP_REGRESSION_TOLERANCE_S: float = float(
    os.environ.get("TIMESTAMP_REGRESSION_TOLERANCE_S", "3600.0")
)

# Coalesce stream events whose observed times fall within this window into one
# rolling revision (0 disables: one revision per event). Lets a partition
# catching up converge before replanning instead of re-solving per event.
STREAM_CONVERGENCE_WINDOW_S: float = float(
    os.environ.get("STREAM_CONVERGENCE_WINDOW_S", "0.0")
)

# ---------------------------------------------------------------------------
# Composite health scoring
# ---------------------------------------------------------------------------
# A weighted health score in [0, 1] (1 = healthy) per stationary asset,
# combining partial signals that individually would not fire a rule. A service
# task is derived when the score falls below the threshold.

# Composite score below which a service task is derived.
COMPOSITE_HEALTH_THRESHOLD: float = float(
    os.environ.get("COMPOSITE_HEALTH_THRESHOLD", "0.35")
)

# Battery headroom above the low threshold that counts as fully healthy.
COMPOSITE_BATTERY_HEADROOM_PCT: float = 30.0

# Days until the planned service due date that count as fully healthy.
COMPOSITE_SERVICE_HEADROOM_DAYS: float = 30.0

# Signal weights in the composite score.
COMPOSITE_WEIGHT_BATTERY: float = 0.35
COMPOSITE_WEIGHT_HEALTH: float = 0.35
COMPOSITE_WEIGHT_SERVICE: float = 0.2
COMPOSITE_WEIGHT_DRIFT: float = 0.1

# Minimum available signals before the composite score is meaningful;
# single-signal cases are covered by the individual rules.
COMPOSITE_MIN_SIGNALS: int = 2

# Health-state subscores (1 = healthy).
HEALTH_STATE_SCORES: dict[str, float] = {
    "healthy": 1.0,
    "unknown": 0.7,
    "degraded": 0.3,
    "failed": 0.0,
}

# ---------------------------------------------------------------------------
# Cross-run quality trending
# ---------------------------------------------------------------------------

# Directory (under DATA_DIR) and filename of the append-only error-rate trend.
QUALITY_TREND_DIRNAME: str = "quality"
QUALITY_TREND_FILENAME: str = "observation-error-rates.jsonl"

# Consecutive runs with strictly increasing error rate that flag a source as
# degrading.
ERROR_RATE_TREND_MIN_RUNS: int = int(os.environ.get("ERROR_RATE_TREND_MIN_RUNS", "3"))

# Retention of the trend file itself: once it exceeds this many records it is
# compacted in place to the newest records, so the append-only file cannot
# grow unboundedly across long deployments.
QUALITY_TREND_MAX_RECORDS: int = int(os.environ.get("QUALITY_TREND_MAX_RECORDS", "500"))

# ---------------------------------------------------------------------------
# Service-prognosis accuracy feedback
# ---------------------------------------------------------------------------

# Filename (under DATA_DIR/quality) of the per-revision service-outcome log.
PROGNOSIS_LOG_FILENAME: str = "service-prognosis.jsonl"

# Filename (under DATA_DIR/quality) of the task-completion lead-time log:
# one record per completed task with its deadline lead and schedule error.
LEAD_TIME_LOG_FILENAME: str = "completion-lead-times.jsonl"

# Share of withdrawn (false positive) service prognoses above which a looser
# monitoring policy (shorter forecast horizon, lower composite threshold) is
# recommended.
PROGNOSIS_FALSE_POSITIVE_ALERT: float = float(
    os.environ.get("PROGNOSIS_FALSE_POSITIVE_ALERT", "0.3")
)

# Share of escalated (false negative) service prognoses above which a more
# cautious monitoring policy (longer forecast horizon, higher thresholds) is
# recommended.
PROGNOSIS_FALSE_NEGATIVE_ALERT: float = float(
    os.environ.get("PROGNOSIS_FALSE_NEGATIVE_ALERT", "0.2")
)

# ---------------------------------------------------------------------------
# Guarded automatic monitoring-policy tuning
# ---------------------------------------------------------------------------
# Opt-in: accumulated prognosis accuracy may adjust the active monitoring
# policy in bounded steps, recorded in an audit trail. Adjustments live in a
# tuned-policy overlay under DATA_DIR/quality; the reviewed profile document
# is never modified, and deleting the overlay reverts to it.

MONITORING_AUTO_TUNE_ENABLED: bool = bool(
    int(os.environ.get("MONITORING_AUTO_TUNE_ENABLED", "0"))
)

# Maximum relative change of one tunable field per adjustment.
MONITORING_AUTO_TUNE_MAX_STEP_PCT: float = float(
    os.environ.get("MONITORING_AUTO_TUNE_MAX_STEP_PCT", "10.0")
)

# Absolute clamps the auto-tuned fields may never leave.
MONITORING_TUNE_HORIZON_MIN_DAYS: float = 1.0
MONITORING_TUNE_HORIZON_MAX_DAYS: float = 14.0
MONITORING_TUNE_COMPOSITE_MIN: float = 0.1
MONITORING_TUNE_COMPOSITE_MAX: float = 0.6

# Overlay and audit-trail filenames under DATA_DIR/quality.
MONITORING_TUNED_POLICY_FILENAME: str = "monitoring-policy-tuned.json"
MONITORING_TUNE_AUDIT_FILENAME: str = "monitoring-policy-audit.jsonl"

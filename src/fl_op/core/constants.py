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

# Maximum V-I candidate pairs per order before routing model construction.
MAX_PAIRS_PER_ORDER: int = int(os.environ.get("MAX_PAIRS_PER_ORDER", "30"))

# ---------------------------------------------------------------------------
# Geographic / clustering
# ---------------------------------------------------------------------------

# WGS-84 mean Earth radius used for haversine distance calculations.
EARTH_RADIUS_KM: float = 6371.0

# Target number of orders per geographic cluster fed to one solver worker.
CLUSTER_TARGET_SIZE: int = int(os.environ.get("CLUSTER_TARGET_SIZE", "50"))

# ---------------------------------------------------------------------------
# Solver time limits
# ---------------------------------------------------------------------------

# Wall-clock seconds per cluster worker before marking the cluster infeasible.
CLUSTER_SOLVE_TIME_LIMIT_S: int = int(os.environ.get("CLUSTER_SOLVE_TIME_LIMIT_S", "60"))

# Number of parallel solver threads (0 = auto: min(n_clusters, cpu_count)).
SOLVER_WORKERS: int = int(os.environ.get("SOLVER_WORKERS", "0"))

# ---------------------------------------------------------------------------
# Cost rates
# ---------------------------------------------------------------------------

# Diesel cost per litre used for repositioning cost estimation in greedy scoring.
FUEL_COST_EUR_PER_L: float = float(os.environ.get("FUEL_COST_EUR_PER_L", "1.45"))

# Liquid fertilizer cost per kilogram for inventory arc cost estimation.
FERTILIZER_COST_EUR_PER_KG: float = float(os.environ.get("FERTILIZER_COST_EUR_PER_KG", "0.55"))

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

# Canonical extension namespace key embedded in ODCS metadata.
XOPT_NAMESPACE: str = "x-optimization"

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

# Semantic-model URN for the agricultural custom-services domain.
URN_MODEL: str = "urn:xopt:model:agricultural-custom-services:0.1.0"

# Semantic-model URN for the domain-agnostic canonical optimization model. Domain
# mapping packs project their physical schemas onto this model.
URN_MODEL_CANONICAL: str = "urn:xopt:model:canonical:0.1.0"

# ODCS custom-property names used by the canonical optimization-model contracts.
CANONICAL_ENTITY_PROPERTY: str = "canonicalEntity"
CANONICAL_BINDING_PROPERTY: str = "canonicalBinding"

# Filename of the canonical-model index inside the canonical contract root.
CANONICAL_MODEL_FILENAME: str = "model.yaml"

# URN prefixes used by semantic terms and bindings.
URN_CAPABILITY_PREFIX: str = "urn:xopt:capability:"
URN_RELATIONSHIP_PREFIX: str = "urn:xopt:relationship:"
URN_ENTITY_PREFIX: str = "urn:xopt:entity:"

# ---------------------------------------------------------------------------
# Planning horizons and rolling-dispatch windows
# ---------------------------------------------------------------------------

# Periodic (batch) planning horizon length.
PERIODIC_HORIZON_DAYS: int = int(os.environ.get("PERIODIC_HORIZON_DAYS", "7"))

# Rolling (stream) dispatch horizon length.
ROLLING_HORIZON_HOURS: int = int(os.environ.get("ROLLING_HORIZON_HOURS", "48"))

# Tasks whose planned start falls within this window are frozen (not replanned).
FREEZE_WINDOW_MINUTES: int = int(os.environ.get("FREEZE_WINDOW_MINUTES", "60"))

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

# Maximum number of compatible (prime-mover, implement) operational bundles
# materialized into a snapshot for inspection/explanation. The solver chain does
# its own compatibility filtering, so this only bounds the snapshot artifact size.
BUNDLE_GENERATION_CAP: int = int(os.environ.get("BUNDLE_GENERATION_CAP", "2000"))

# Version dimensions stamped onto snapshots and plans for governance/lineage.
MAPPING_VERSION: str = "1.0.0"
OPTIMIZATION_PROFILE_VERSION: str = "0.1.0"

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
# JSON artifact schema
# ---------------------------------------------------------------------------

# Bumped when the structure of any output JSON file changes in a breaking way.
ARTIFACT_SCHEMA_VERSION: str = "1.0"

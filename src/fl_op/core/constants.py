# ---------------------------------------------------------------------------
# Synthetic data generation defaults
# ---------------------------------------------------------------------------

# Default production-sized dataset used by the generate-data CLI and Makefile.
DEFAULT_GENERATE_VEHICLES: int = 1500
DEFAULT_GENERATE_IMPLEMENTS: int = 6000
DEFAULT_GENERATE_ORDERS: int = 2500
DEFAULT_GENERATE_DEPOTS: int = 500

# Resource pre-allocation reserves enough V-I pairs for parallelism without
# hoarding the whole fleet in the first high-penalty clusters.
PREALLOC_ORDERS_PER_RESOURCE: int = 5
PREALLOC_MIN_RESOURCES_PER_MULTI_ORDER_CLUSTER: int = 2

# ---------------------------------------------------------------------------
# Weather restriction thresholds (hard constraints)
# ---------------------------------------------------------------------------

# Maximum sustained wind speed above which all field operations are prohibited.
WEATHER_WIND_MAX_MS: float = 10.0  # m/s

# Maximum hourly rainfall above which field operations are prohibited.
WEATHER_RAIN_MAX_MM: float = 5.0  # mm/h

# Maximum volumetric soil moisture above which heavy machinery is prohibited
# (prevents compaction and equipment getting stuck).
WEATHER_SOIL_MOISTURE_MAX_PCT: float = 85.0  # % volumetric water content

# ---------------------------------------------------------------------------
# Vehicle-implement compatibility
# ---------------------------------------------------------------------------

# A V-I pair is considered compatible only when the implement's required tractor
# power does not exceed the vehicle's rated power by more than this margin.
# Positive = implement may draw up to this percentage above vehicle rated power
# (short-duration peaks); negative would mean a safety headroom.
POWER_MARGIN_PCT: float = 10.0  # percent

# Maximum number of V-I candidate pairs kept per order before routing model
# construction. Caps OR-Tools model size at tractable bounds.
MAX_PAIRS_PER_ORDER: int = 30

# ---------------------------------------------------------------------------
# Geographic / clustering
# ---------------------------------------------------------------------------

# WGS-84 mean Earth radius used for haversine distance calculations.
EARTH_RADIUS_KM: float = 6371.0

# Target number of orders per geographic cluster fed to one cluster solver worker.
CLUSTER_TARGET_SIZE: int = 50

# ---------------------------------------------------------------------------
# Solver time limits
# ---------------------------------------------------------------------------

# Maximum wall-clock seconds granted to a single cluster solver worker.
# On timeout the cluster is marked infeasible("solver_timeout").
CLUSTER_SOLVE_TIME_LIMIT_S: int = 60

# ---------------------------------------------------------------------------
# Cost rates
# ---------------------------------------------------------------------------

# Diesel cost per litre used for repositioning cost estimation in greedy scoring.
FUEL_COST_EUR_PER_L: float = 1.45

# Liquid fertilizer cost per kilogram for inventory arc cost estimation.
FERTILIZER_COST_EUR_PER_KG: float = 0.55

# ---------------------------------------------------------------------------
# Greedy scoring weights
# ---------------------------------------------------------------------------

# Weight applied to gross margin estimate component of greedy score.
SCORE_WEIGHT_MARGIN: float = 1.0

# Weight applied to repositioning cost penalty component of greedy score
# (subtracted from margin; higher = distance matters more).
SCORE_WEIGHT_REPOSITION: float = 1.0

# ---------------------------------------------------------------------------
# JSON artifact schema
# ---------------------------------------------------------------------------

# Bumped when the structure of any output JSON file changes in a breaking way.
ARTIFACT_SCHEMA_VERSION: str = "1.0"

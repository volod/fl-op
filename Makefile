-include .env
export

.PHONY: venv setenv quickstart analyse data demo contracts canonical-validate validate-construction avro-gen proto-gen es-gen parquet-gen contracts-gen check-gen clean

PYTHON := .venv/bin/python
FL_OP := .venv/bin/fl-op

# Fallback defaults when .env is absent or a variable is not defined there.
# Command-line overrides (e.g. make quickstart VEHICLES=200) always take precedence.
VEHICLES ?= 100
IMPLEMENTS ?= 400
ORDERS ?= 250
DEPOTS ?= 50
SEED ?=
DATA_DIR ?= .data
FORMAT ?= avro

SEED_ARG := $(if $(SEED),--seed $(SEED),)
QUICKSTART_SEED_ARG := $(if $(SEED),--seed $(SEED),--seed 42)

# Pick uv link mode by comparing the repo filesystem device with the uv cache
# device. Hardlinks cannot span filesystems, so fall back to copy when they
# differ (silences the "Failed to hardlink files" warning); use hardlink when
# they share a device for fast, space-saving installs.
venv:
	@cache_dir="$$(uv cache dir 2>/dev/null)"; \
	repo_dev="$$(stat -c '%d' . 2>/dev/null)"; \
	cache_dev="$$(stat -c '%d' "$$cache_dir" 2>/dev/null)"; \
	if [ -n "$$repo_dev" ] && [ "$$repo_dev" = "$$cache_dev" ]; then \
		link_mode=hardlink; \
	else \
		link_mode=copy; \
	fi; \
	echo "[venv] repo dev=$$repo_dev cache dev=$$cache_dev -> link-mode=$$link_mode"; \
	uv sync --link-mode="$$link_mode"
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example"; else echo ".env already exists, skipping"; fi

setenv:
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example"; else echo ".env already exists, skipping"; fi

quickstart: venv
	@echo "Generating dataset (--vehicles $(VEHICLES) --implements $(IMPLEMENTS) --orders $(ORDERS) --depots $(DEPOTS) $(QUICKSTART_SEED_ARG) --format $(FORMAT))..."
	$(FL_OP) generate-data --vehicles $(VEHICLES) --implements $(IMPLEMENTS) --orders $(ORDERS) --depots $(DEPOTS) $(QUICKSTART_SEED_ARG) --format $(FORMAT)
	@echo "Running solver on latest dataset..."
	$(FL_OP) solve --data latest
	@echo "Dataset parameters: vehicles=$(VEHICLES), implements=$(IMPLEMENTS), orders=$(ORDERS), depots=$(DEPOTS), $(QUICKSTART_SEED_ARG)"
	@echo "Analysing latest solver run..."
	$(FL_OP) analyse --schedule latest
	@echo "Quickstart complete. See $(DATA_DIR)/solve/ for results."

analyse: venv
	$(FL_OP) analyse --schedule latest

# Validate only the canonical optimization-model contracts (entities + vocabulary).
canonical-validate: venv
	$(FL_OP) contracts canonical-validate

# Validate that the construction domain pack maps completely onto the canonical
# model (proof that a second physical domain reuses the one optimization model).
validate-construction: venv
	$(FL_OP) contracts validate-domain --domain construction

# Validate the declarative data-contract suite (canonical model + ODCS + generated schemas + dual fingerprints).
contracts: venv
	$(FL_OP) contracts validate

avro-gen: venv  ## Generate Avro schemas from ODCS contracts
	$(FL_OP) contracts generate --format avro

proto-gen: venv  ## Generate and compile Protobuf schemas from ODCS contracts
	$(FL_OP) contracts generate --format proto

es-gen: venv  ## Generate Elasticsearch mappings from ODCS contracts
	$(FL_OP) contracts generate --format es

parquet-gen: venv  ## Generate Parquet schema descriptors from ODCS contracts
	$(FL_OP) contracts generate --format parquet

contracts-gen: avro-gen proto-gen es-gen parquet-gen  ## Generate all physical schema formats

check-gen: venv  ## Check ODCS contracts have complete generation hints for all formats
	$(FL_OP) contracts check-generation --format avro
	$(FL_OP) contracts check-generation --format proto
	$(FL_OP) contracts check-generation --format es
	$(FL_OP) contracts check-generation --format parquet

# Full declarative demo: contracts -> snapshot -> periodic (batch) -> rolling (stream).
# Depends on avro-gen because contracts/generated/ is gitignored: the demo's first
# step validates Avro fingerprints, so the schemas must be materialised first.
demo: venv avro-gen
	@echo "Generating demo dataset ($(QUICKSTART_SEED_ARG) --format $(FORMAT))..."
	$(FL_OP) generate-data --vehicles $(VEHICLES) --implements $(IMPLEMENTS) --orders $(ORDERS) --depots $(DEPOTS) $(QUICKSTART_SEED_ARG) --format $(FORMAT)
	$(FL_OP) demo --data latest
	@echo "Demo complete. See $(DATA_DIR)/plan-periodic/ and $(DATA_DIR)/plan-rolling/ for results."

# Full benchmark (manual only, not a CI target):
#   make data
# This runs at the default benchmark scale. Override with:
#   make data VEHICLES=3000 IMPLEMENTS=20000 ORDERS=2500 DEPOTS=50
# Expect 60+ seconds generation time and several minutes for solver.
data: venv
	@echo "[manual benchmark] Generating full dataset ($(VEHICLES)v / $(IMPLEMENTS)i / $(ORDERS)o / $(DEPOTS)d --format $(FORMAT))..."
	$(FL_OP) generate-data --vehicles $(VEHICLES) --implements $(IMPLEMENTS) --orders $(ORDERS) --depots $(DEPOTS) $(SEED_ARG) --format $(FORMAT)
	@echo "[manual benchmark] Solving full dataset..."
	$(FL_OP) solve --data latest

clean:
	rm -rf $(DATA_DIR)/ .venv/
	rm -rf build/ dist/ *.egg-info/ htmlcov/ .pytest_cache/ .ruff_cache/ .mypy_cache/ .coverage .coverage.* coverage.xml
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

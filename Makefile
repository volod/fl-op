-include .env
export

.PHONY: venv setenv quickstart analyse data clean

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

SEED_ARG := $(if $(SEED),--seed $(SEED),)
QUICKSTART_SEED_ARG := $(if $(SEED),--seed $(SEED),--seed 42)

venv:
	uv sync
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example"; else echo ".env already exists, skipping"; fi

setenv:
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example"; else echo ".env already exists, skipping"; fi

quickstart: venv
	@echo "Generating dataset (--vehicles $(VEHICLES) --implements $(IMPLEMENTS) --orders $(ORDERS) --depots $(DEPOTS) $(QUICKSTART_SEED_ARG))..."
	$(FL_OP) generate-data --vehicles $(VEHICLES) --implements $(IMPLEMENTS) --orders $(ORDERS) --depots $(DEPOTS) $(QUICKSTART_SEED_ARG)
	@echo "Running solver on latest dataset..."
	$(FL_OP) solve --data latest
	@echo "Dataset parameters: vehicles=$(VEHICLES), implements=$(IMPLEMENTS), orders=$(ORDERS), depots=$(DEPOTS), $(QUICKSTART_SEED_ARG)"
	@echo "Analysing latest solver run..."
	$(FL_OP) analyse --schedule latest
	@echo "Quickstart complete. See $(DATA_DIR)/solve/ for results."

analyse: venv
	$(FL_OP) analyse --schedule latest

# Full benchmark (manual only, not a CI target):
#   make data
# This runs at the default benchmark scale. Override with:
#   make data VEHICLES=3000 IMPLEMENTS=20000 ORDERS=2500 DEPOTS=50
# Expect 60+ seconds generation time and several minutes for solver.
data: venv
	@echo "[manual benchmark] Generating full dataset ($(VEHICLES)v / $(IMPLEMENTS)i / $(ORDERS)o / $(DEPOTS)d)..."
	$(FL_OP) generate-data --vehicles $(VEHICLES) --implements $(IMPLEMENTS) --orders $(ORDERS) --depots $(DEPOTS) $(SEED_ARG)
	@echo "[manual benchmark] Solving full dataset..."
	$(FL_OP) solve --data latest

clean:
	rm -rf $(DATA_DIR)/ .venv/
	rm -rf build/ dist/ *.egg-info/ htmlcov/ .pytest_cache/ .ruff_cache/ .mypy_cache/ .coverage .coverage.* coverage.xml
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

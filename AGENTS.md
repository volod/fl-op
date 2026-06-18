# fl-op Project Rules

## Development Guardrails
- **Git:** Do not create git commits or revert user changes unless explicitly asked.
- **Python:** Use `uv` and `pyproject.toml` for all dependency management (Python >= 3.10).
- **Typing:** Do not add `from __future__ import annotations`; use normal annotations and `TYPE_CHECKING` imports when needed.
- **Paths:** Never hardcode absolute directories (e.g., `/home/...`). Resolve every path from the project base directory and honor `.env`/`DATA_DIR` settings.

## Code Organization
- **CLI vs Core:** Use `src/fl_op/main.py` as the CLI entry point. Keep top-level `scripts/` as shell entrypoints only. Put production Python implementations inside `src/fl_op/...`.
- **Modularity & Refactoring:** Keep modules small and focused by organizing them into intuitively named subpackages or submodules. Extract long procedural code sequences with a small number of input parameters into well-named, self-contained functions. Maximally reuse existing code and avoid repeating yourself (DRY). You must proactively evaluate your work against these principles after completing any sizable feature implementation.
- **Artifacts:** Runtime data and run artifacts belong under `.data/<method_name>/<run_timestamp>/`. Never write to a module-local `.data/` inside `src/`.
- **Shell Scripts:** Reuse `scripts/shared/common.sh` for shared shell root/env/bootstrap behavior instead of duplicating logic.

## Documentation
- **Future-work hygiene:** `docs/future-improvements.md` tracks open future work
  only; delivered behavior lives in `docs/current-implementation.md`. After
  implementing an item from the Ordered Implementation Sequence and relates item 
  description section, before finishing the task: (1) move the important implementation 
  details into `docs/current-implementation.md`; (2) update the item with the residual
  "possible further improvements" the implementation surfaced (the still-open gaps and 
  natural next steps or research-grade improvements), keeping only that open work; and
  (3) delete the now-implemented description from `docs/future-improvements.md`. 
  If an item is fully delivered with no residual work, remove it entirely. 
  Keep each item's sequence number stable as a workstream identifier.

## Formatting & Conventions
- **ASCII Only:** Use ASCII in logs, docs, comments, and generated shell output. No emojis or Unicode box-drawing characters (use `[ok]`, `->`, `=`, `-`, `[info]`, `*`).
- **Constants:** Avoid magic numbers. Create constant modules with well-described variables to improve readability.
- **Logging:** Use Python's `logging` module instead of `print()`.
- **Optimization Stack:** Prefer Python-native packages (`ortools`, `numpy`, `scipy`) to keep the stack Pythonic. Use `Optuna` and `MLflow` for tuning and tracking when necessary.

## Production-grade implementation requirements
When a task is explicitly production-grade (not a proof of concept), every change must satisfy:
- **Contract-first canonical fields:** A new canonical entity attribute is added across the full stack in one change: the semantic term in `contracts/canonical/model.yaml`, the ODCS field with a `canonicalBinding` in `contracts/canonical/odcs/*.odcs.yaml`, the input binding in `solver/inputs.py`, the canonical model (explicit field, or generic `capabilities` for asset capabilities), the mapping builder in `mapping/builders.py`, and the solver row in `solver/types.py`.
- **Schema evolution gate:** An additive optional field is a backward-compatible change -- bump the owning contract's `version` (minor) and run `make evolution-freeze` to refreeze `contracts/evolution/*.json`, then confirm `make evolution-check` and `uv run fl-op contracts validate` pass. Never hand-edit the frozen baselines.
- **Backward-compatible defaults:** New fields default to zero/empty so existing datasets, snapshots, and golden tests are byte-for-byte unchanged when the data omits them. Opt-in behavior changes ride behind a default-off flag.
- **Authoritative outputs are complete:** The economically/operationally authoritative outputs (OR-Tools arc costs, dispatch-package fields and margins, KPI aggregates) must reflect every new priced/modeled term. Heuristic-only estimates (greedy warm start, repositioning estimates) may lag and are tracked as residual, never the source of published numbers.
- **Determinism and the worker pool:** Solver inputs cross a process pool -- keep them picklable (plain dataclasses/dicts), and keep results deterministic for a fixed snapshot and seed.
- **Tests and docs:** Add unit tests for new helpers and at least one end-to-end assertion through the real solve path; the full suite (`uv run pytest`) must stay green. Follow the Documentation hygiene above (move delivered behavior to `current-implementation.md`, leave only residual work in `future-improvements.md`).

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

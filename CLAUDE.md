# CLAUDE.md

This file provides compact repository guidance for coding agents.

## Rules


- Use `uv` and `pyproject.toml` for all Python dependency and environment management.
- `make venv` creates `.venv` with Python 3.10+ and installs project dependencies.
- `make setenv` creates `.env` from `.env.example` without overwriting an existing `.env`.
- `make data` prepares all required datasets under `.data/datasets`.
- Keep `.data/`, `.env`, and `.venv` out of git.
- do not use magic number as constant, model or function default parameters. create constant module with well described constants and use constant from this module for     
  reradability in avery place where we need provide a number
- Keep `scripts/` shell-only. Do not place Python files or embedded Python programs there.
- Put Python implementation under `src/fl_ml` subpackages. Keep package root limited to package metadata and `main.py`.
- Use `src/fl_op/main.py` as the command-line entry point.
- Every method run should write artifacts to `.data/<method_name>/<run_timestamp>/`.
- prefer python native packages for optimization to keep staxk pythonic;
- Use Optuna for hyperparameter optimization and MLflow for run logging if needed.
- Prefer realistic, reasonably sized datasets for learning value. Use small canonical datasets only when estimator constraints or runtime make them appropriate.
- Never create a git commit unless the user explicitly asks for one.
- Never add `from __future__ import annotations`, and replace those cases with explicit imports TYPE_CHECKING.
- Top-level `scripts/` must be shell entrypoints only. Put Python implementations under `src/sfl_op/...` and call them from shell wrappers when needed.
- Reuse `scripts/shared/common.sh` for shared shell behavior instead of duplicating root/env/bootstrap logic.
- Use ASCII-only characters in all log messages, docstrings, comments, and documentation. No emoji, no Unicode box-drawing or symbol characters (no ✓ ▷ ═ ─ ℹ ⚠ ● or similar). Use plain ASCII equivalents: `[ok]`, `->`, `=`, `-`, `[info]`, `[warn]`, `*`.
- use logging instead of print()

## gstack

- Run `scripts/setup-gstack.sh` once to install gstack into `~/.claude/skills/gstack`.
- Use the `/browse` skill from gstack for all web browsing tasks.
- Never use `mcp__claude-in-chrome__*` tools for any purpose.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. 
When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore

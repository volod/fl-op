# ADR-019: Python-native OR-Tools rolling adapter instead of Timefold

Date: 2026-06-05
Status: Accepted
Deciders: Volodymyr Lazurenko, Claude Code

## Context

The spec (section 19) names Timefold for rolling dispatch. Timefold is a JVM
framework; its Python binding still requires a JVM and JPype. The project rule is
to prefer Python-native packages to keep the stack pythonic (CLAUDE.md, ADR-001),
and the existing reschedule pipeline already performs event-driven re-optimization
with OR-Tools (freeze started orders, re-solve the rest, diff the plan).

## Decision

Implement rolling dispatch as a Python-native OR-Tools adapter
(`adapters/ortools_rolling.py`) behind the common adapter SPI, evolving the
existing reschedule logic. The `OptimizationProfile` binds the rolling mode to
`ortools-rolling` rather than `timefold-rolling`. No JVM is added.

## Rationale

Timefold's conceptual mapping (vehicles=bundles, visits=tasks, freezeTime,
pinned visits) is honored conceptually: the adapter freezes started and imminent
tasks, preserves them verbatim across revisions, and applies a plan-instability
penalty to post-freeze changes. Reusing the proven solver chain avoids a second
solver toolchain and a JVM dependency for a POC.

## Consequences

- The freeze window (started OR planned start within `FREEZE_WINDOW_MINUTES`) is
  enforced in the adapter layer, not in the solver internals.
- Each replanning trigger produces a new immutable `PlanRevision` linked to its
  parent; frozen assignments are byte-identical across revisions (verified by
  `test_plan_rolling_e2e.py`).
- A future Timefold adapter can be added behind the same SPI without changing the
  contracts, canonical model, or snapshot layers.

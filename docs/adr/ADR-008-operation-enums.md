# ADR-008: Python Enum for OperationType and ImplementType

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review

## Context

Agricultural operations (spraying, tillage, seeding, harvesting, fertilizing)
and implement types (sprayer, plow, seeder, etc.) appear as string fields in
CSV imports and as filter keys throughout the pipeline. Two representations
were considered:

- **Plain strings**: fast, flexible, no import dependency. Risk: case mismatch
  ("spraying" vs "SPRAYING"), typos, and missing values fail silently at filter
  time — a sprayer passes a TILLAGE order compatibility check because the
  string comparison returns False quietly, not because an error was raised.
- **Python Enum (str subclass)**: validated at model parse time by Pydantic.
  Unknown strings raise `ValidationError` immediately at data load, not silently
  at filter time several layers later.

## Decision

Define `OperationType` and `ImplementType` (and `VehicleType`, `OrderStatus`)
as **Python Enums** inheriting from both `str` and `Enum`. Pydantic model fields
use the enum type; compatibility filtering compares enum values directly.

## Rationale

A mismatch between "SPRAYING" and "spraying" in a string comparison passes the
filter silently. The result is that a sprayer implement is assigned to a tillage
order in the dispatch package — a real-world error that causes equipment damage
(a sprayer cannot till soil). This is a category of bug that appears in
production but not in unit tests if the tests use matching strings.

Enum membership check raises `ValueError` at the first invalid string, not after
the dispatch package is written to disk. The cost — one `Enum()` call per CSV
row at load time — is negligible. The benefit — guaranteed constraint correctness
at data ingestion — is high.

`str` subclassing (`class OperationType(str, Enum)`) preserves JSON
serialisability and Pydantic's native enum support without special configuration.

## Consequences

- All CSV columns that carry operation or implement type values are validated
  against the Enum on first load. A CSV row with an unrecognised operation type
  raises `ValidationError` and halts data ingestion with a clear error message.
- New operation types require an Enum update before they can be used. This is an
  intentional gate: adding a new operation type without updating the compatibility
  filter would be a silent correctness bug.
- The `field_validator(mode='before')` on `Implement.compatible_operations` parses
  CSV-format lists (`"['SPRAYING']"`) to Python lists before Enum validation runs.
  This is the only place where raw string parsing of enum values is permitted.
- Tests that use raw dict fixtures must pass Enum-compatible string values
  (uppercase, matching exactly); the fixture helpers in test files enforce this.

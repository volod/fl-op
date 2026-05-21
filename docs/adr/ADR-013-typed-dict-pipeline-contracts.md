# ADR-013: TypedDict layer for pipeline contracts

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /autoplan

## Context

The pipeline passes plain Python dicts between layers (see ADR-006). Without
type annotations, dict keys are arbitrary strings; a typo in a key name
("order_ids" vs "orderids") causes a silent `KeyError` at runtime, not a
compile-time error.

Three options for type-safe dicts were considered:

- **Pydantic models**: full runtime validation, but prohibited across the process
  boundary (ADR-006) and expensive to construct at high frequency.
- **dataclasses**: lightweight, statically typed, but not directly compatible with
  `dict` operations (can't pass a dataclass where a dict is expected).
- **TypedDict** (`typing.TypedDict`): a dict subtype with static field
  declarations. No runtime cost (TypedDict is erased at runtime). mypy/pyright
  enforces field names and types at analysis time. Can be passed anywhere a dict
  is expected.

## Decision

Define **TypedDict** classes for all pipeline data contracts in
`src/fl_op/models/types.py`:

- `ClusterSpec`: input to the cluster solver worker
- `FeasibleAssignment`: greedy scorer output per V-I pair
- `DispatchPackage`: solver output per assigned order
- `InfeasibleOrder`: solver output per rejected order

These types are used as return-type annotations throughout the pipeline.

## Rationale

TypedDict provides static type checking without any runtime overhead. A type
checker (mypy, pyright) will flag `cluster["ordr_ids"]` as a `TypedDict` key
error; no test required to catch the typo. At the same time, TypedDicts are
plain dicts at runtime, so they cross the process boundary, are JSON-serialisable,
and are accepted by any function expecting `dict`.

The alternative — Pydantic models everywhere — would require converting every
pipeline dict to a model on entry and back to a dict on exit at every layer
boundary. For a pipeline processing thousands of clusters, this overhead is
unnecessary when the data structure is fixed and well-understood.

## Consequences

- All functions that produce or consume pipeline data must use the corresponding
  TypedDict in their type signature. Untyped `dict` return types in pipeline
  functions are a code smell.
- `total=False` variants are used where optional fields exist (e.g.
  `ClusterSpec.operator_id` is assigned by `resource_allocator` and absent
  until that step runs).
- TypedDict does not provide runtime validation. A worker that returns a dict
  with the wrong keys passes the `assert len(result) == 2` check (ADR-007) but
  may cause a `KeyError` downstream. If runtime validation becomes necessary,
  Pydantic can be added at layer-boundary entry points only.

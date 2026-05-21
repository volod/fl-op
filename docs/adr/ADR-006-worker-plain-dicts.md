# ADR-006: Plain Python dicts across the process boundary

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review

## Context

`multiprocessing.Pool` transfers data between the parent process and worker
processes by pickling arguments and return values through a pipe. The cluster
solver workers receive input data and produce dispatch packages.

Two data representation options were considered:

- **Pass Pydantic models**: rich, validated, type-safe objects. Convenient in the
  main process; worker code can call `.model_validate()` on inputs.
- **Pass plain Python dicts**: JSON-serialisable primitives only. No Pydantic,
  no OR-Tools objects cross the process boundary.

A related question: where is the OR-Tools `RoutingModel` created?

- **Create in parent, pass to worker**: the RoutingModel is not picklable and
  will raise `TypeError` at pickle time. This is not viable.
- **Create inside worker**: the worker constructs and destroys the RoutingModel
  entirely within its own process lifetime.

## Decision

All data crossing the `multiprocessing.Pool` boundary — both arguments to worker
functions and their return values — must be **plain Python dicts and primitive
types** (str, int, float, list, None). No Pydantic model instances. No OR-Tools
objects. The routing model is created and destroyed inside the worker function.

## Rationale

Pydantic v2 models are not consistently picklable across Python versions and
multiprocessing start methods. Even when pickling succeeds today, it relies on
internal Pydantic implementation details that may change between minor versions.
The cost of unpickling a 3000-vehicle Pydantic model graph in each of 50 worker
processes is also significant (repeated deserialisation, not shared memory).

OR-Tools `RoutingModel` objects hold C++ state that is explicitly non-picklable.
Any attempt to pass them through the pipe raises `TypeError` immediately.

Plain dicts are always picklable, trivially JSON-serialisable for debugging, and
impose no implicit version coupling between parent and worker code. The overhead
of converting Pydantic models to dicts in the parent (one pass before Pool opens)
is paid once, not once per worker per field access.

## Consequences

- Worker functions must validate their input dicts defensively (check for
  expected keys, convert string numerics from CSV if necessary) rather than
  relying on Pydantic enforcement.
- Return values from workers are plain `list[dict]`; the aggregator must
  validate structure (`assert len(result) == 2` per ADR-007) before merging.
- The TypedDict layer (ADR-013) provides static type hints for these dicts
  without runtime cost.
- Debugging is easier: any dict in the pipeline can be `json.dumps()`-ed and
  inspected without a Pydantic model context.

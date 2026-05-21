# ADR-011: TYPE_CHECKING + model_rebuild() for circular model references

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review

## Context

Two pairs of Pydantic models have circular references:
- `Vehicle` has an optional `Operator` field; `Operator` has a `vehicle_id` back-reference.
- `Order` has an optional `Contract` field; `Contract` contains a list of `Order` objects.

Pydantic v2 with Python 3.10+ offers three patterns for resolving forward references:

1. **`from __future__ import annotations`**: defers evaluation of all annotations
   to strings, resolving them lazily at runtime. Banned by `CLAUDE.md` due to
   known interactions with Pydantic v2 and runtime annotation inspection tools.
2. **String literals**: `contract: "Contract | None"`. Works but is fragile —
   string annotations are not checked by type checkers unless explicitly configured.
3. **`TYPE_CHECKING` guard + `model_rebuild()`**: import the forward-referenced
   model under `if TYPE_CHECKING:` (invisible at runtime), declare the field with
   the actual type, call `Model.model_rebuild()` after all models are imported.
   This is the Pydantic v2 recommended pattern for circular dependencies.

## Decision

Use `TYPE_CHECKING` imports for circular dependencies and call `model_rebuild()`
in `src/fl_op/models/__init__.py` after all model modules are imported.

```python
# order.py
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from fl_op.models.contract import Contract

class Order(BaseModel):
    contract: "Contract | None" = None
```

```python
# models/__init__.py
from fl_op.models.contract import Contract
from fl_op.models.order import Order
Order.model_rebuild()
Contract.model_rebuild()
```

## Rationale

`from __future__ import annotations` is banned (CLAUDE.md) because it changes
the semantics of all annotations in the file, not just the circular ones. It
breaks runtime annotation inspection used by Pydantic validators and causes
subtle failures that are hard to debug.

`model_rebuild()` with `TYPE_CHECKING` is explicit and surgical: only the
forward-referenced types are deferred, and `model_rebuild()` resolves them
exactly once after all definitions are loaded. Pydantic v2's documentation
recommends this pattern specifically for circular models.

## Consequences

- `src/fl_op/models/__init__.py` must import all models that participate in
  circular references and call `model_rebuild()` on them in dependency order.
  This file is the single point where circular dependencies are resolved.
- Any new model that introduces a circular reference must be registered here.
  Forgetting to call `model_rebuild()` produces a `PydanticUserError` at
  first model instantiation (clear error, not silent).
- Type checkers see the `TYPE_CHECKING` imports and correctly infer field types
  without false positives.
- The `models/__init__.py` import order matters: models must be imported before
  `model_rebuild()` is called on models that reference them.

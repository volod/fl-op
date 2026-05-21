"""Resolve forward references for circular model pairs."""

from fl_op.models.contract import Contract
from fl_op.models.order import Order

# Resolve Vehicle <-> Operator and Order <-> Contract forward refs.
# Must run after both sides of each circular pair are imported.
Order.model_rebuild()
Contract.model_rebuild()

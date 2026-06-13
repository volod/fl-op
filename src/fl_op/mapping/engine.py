"""Mapping engine: source records + x-optimization bindings -> canonical objects.

This module keeps the public MappingEngine API. Row accumulation and canonical
object construction live in helper modules under ``fl_op.mapping``. Entity
dispatch is adaptive: any canonical entity registered in
``builders.ENTITY_EMITTERS`` is mappable, so new entities (and domain packs that
use them) require no engine changes.
"""

import logging
from typing import Optional

from fl_op.contracts.registry import FileRegistry
from fl_op.mapping.accumulator import accumulate_row
from fl_op.mapping.bindings import load_binding_table
from fl_op.mapping.builders import ENTITY_EMITTERS, register_entity_emitter
from fl_op.mapping.result import MappingResult

logger = logging.getLogger(__name__)


class MappingEngine:
    """Maps registered source datasets into canonical objects via their bindings."""

    def __init__(self, registry: Optional[FileRegistry] = None) -> None:
        self.registry = registry or FileRegistry()

    def map_dataset(
        self,
        contract_id: str,
        rows: list[dict],
        result: Optional[MappingResult] = None,
    ) -> MappingResult:
        """Map one source dataset's rows into the appropriate canonical objects."""
        result = result or MappingResult()
        table = load_binding_table(self.registry, contract_id)
        entity = table.canonical_entity

        emitter = ENTITY_EMITTERS.get(entity)
        if emitter is None:
            logger.warning("Unhandled canonical entity '%s' for %s", entity, contract_id)
            return result

        for row in rows:
            acc = accumulate_row(table, row, result)
            if acc is None:
                continue
            emitter(table, acc, result)

        logger.info(
            "Mapped %s: %d rows -> %s (excluded %d)",
            contract_id,
            len(rows),
            entity,
            len(result.excluded.get(contract_id, [])),
        )
        return result


__all__ = ["MappingEngine", "MappingResult", "register_entity_emitter"]

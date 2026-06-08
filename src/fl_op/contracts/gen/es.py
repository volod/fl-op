"""Generate Elasticsearch index mappings from ODCS contracts.

Generated mappings contain no semantic metadata. They describe only the
physical field types and indexing options needed for ES to store the data.

Type decisions:
- keyword  -> keyword with ignore_above: ES_KEYWORD_IGNORE_ABOVE (truncation guard)
- datetime -> date with strict_date_optional_time||epoch_millis format
- lat/lon  -> double (individual fields) + synthesized geo_point named 'location'
- dynamic  -> strict (rejects unmapped fields; prevents silent data loss)
"""

import json
from typing import Any

from fl_op.contracts.gen.base import (
    ES_DATE_FORMAT,
    ES_KEYWORD_IGNORE_ABOVE,
    PHYSICAL_TYPE_TO_ES,
    VALID_ES_TYPES,
    GenerationError,
    GeneratorBase,
    iter_schema_properties,
    semantic_hints,
)


def _geo_point_field_name(lat_field_name: str) -> str:
    """Derive a geo_point field name from the latitude field name.

    'lat'         -> 'location'
    'current_lat' -> 'current_location'
    'depot_lat'   -> 'depot_location'
    """
    if lat_field_name == "lat":
        return "location"
    if lat_field_name.endswith("_lat"):
        return lat_field_name[:-4] + "_location"
    return lat_field_name + "_location"


class EsGenerator(GeneratorBase):
    FORMAT_KEY = "es"

    _REQUIRED_SCHEMA_KEYS = ("indexName",)

    def generate(self, odcs_doc: dict[str, Any], contract_id: str) -> str:
        hints = self.get_schema_gen_hints(odcs_doc)
        for key in self._REQUIRED_SCHEMA_KEYS:
            if not hints.get(key):
                raise GenerationError(
                    f"{contract_id}: schemaGeneration.es missing required key '{key}'"
                )
        dynamic = hints.get("dynamic", "strict")

        props = list(iter_schema_properties(odcs_doc))
        lat_fields: dict[str, str] = {}
        lon_field_names: set[str] = set()

        for prop in props:
            sh = semantic_hints(prop)
            if sh["is_latitude"]:
                lat_fields[prop.get("name", "")] = _geo_point_field_name(prop.get("name", ""))
            elif sh["is_longitude"]:
                lon_field_names.add(prop.get("name", ""))

        properties: dict[str, Any] = {}
        for prop in props:
            field_name, field_mapping = self._build_field_mapping(prop, contract_id)
            properties[field_name] = field_mapping

        # Synthesize geo_point field for each confirmed lat/lon pair.
        for lat_name, geo_name in lat_fields.items():
            expected_lon = lat_name.replace("_lat", "_lon") if "_lat" in lat_name else "lon"
            if expected_lon in lon_field_names:
                properties[geo_name] = {
                    "type": "geo_point",
                    "ignore_malformed": False,
                }

        mapping: dict[str, Any] = {
            "mappings": {
                "dynamic": dynamic,
                "properties": properties,
            }
        }
        return json.dumps(mapping, indent=2, ensure_ascii=True)

    def _build_field_mapping(
        self, prop: dict[str, Any], contract_id: str
    ) -> tuple[str, dict[str, Any]]:
        name = prop.get("name", "")
        physical_type = prop.get("physicalType", "")
        if physical_type not in PHYSICAL_TYPE_TO_ES:
            raise GenerationError(
                f"{contract_id}: field '{name}' has unknown physicalType '{physical_type}'"
            )

        sh = semantic_hints(prop)
        field_hints = self.get_field_gen_hints(prop)
        es_type = field_hints.get("type") or PHYSICAL_TYPE_TO_ES[physical_type]

        # Semantic overrides take priority over physicalType mapping.
        if sh["is_timestamp"] or sh["is_date"]:
            es_type = "date"
        elif sh["is_latitude"] or sh["is_longitude"]:
            es_type = "double"

        if es_type not in VALID_ES_TYPES:
            raise GenerationError(
                f"{contract_id}: field '{name}' has invalid ES type '{es_type}'"
            )

        field_mapping: dict[str, Any] = {"type": es_type}

        if es_type == "keyword":
            field_mapping["ignore_above"] = ES_KEYWORD_IGNORE_ABOVE

        elif es_type == "date":
            field_mapping["format"] = ES_DATE_FORMAT

        elif es_type in ("double", "float") and not sh["is_latitude"] and not sh["is_longitude"]:
            if not prop.get("required", True):
                field_mapping["null_value"] = 0.0

        elif es_type in ("integer", "long"):
            if not prop.get("required", True):
                field_mapping["null_value"] = 0

        return name, field_mapping

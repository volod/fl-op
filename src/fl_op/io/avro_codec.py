"""Avro format codec via fastavro."""

import pathlib
from typing import Any

import fastavro

from fl_op.io.base import FormatCodec


def _normalize_val(val: Any) -> Any:
    """Convert NumPy scalars to native Python types for fastavro compatibility."""
    if val is None:
        return None
    if hasattr(val, "item"):
        return val.item()
    return val


def _normalize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _normalize_val(v) for k, v in r.items()} for r in records]


def _avro_type_for(val: Any) -> Any:
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, int):
        return "long"
    if isinstance(val, float):
        return "double"
    if isinstance(val, list):
        item_type = "string"
        for item in val:
            if item is not None:
                item_type = _avro_type_for(item)
                break
        return {"type": "array", "items": item_type}
    if isinstance(val, dict):
        return {"type": "map", "values": "string"}
    return "string"


def _infer_schema(stem: str, sample: dict[str, Any]) -> dict[str, Any]:
    fields = []
    for key, val in sample.items():
        base = _avro_type_for(val)
        avro_type: Any = ["null", base]
        fields.append({"name": key, "type": avro_type, "default": None})
    return {
        "type": "record",
        "name": stem.capitalize(),
        "namespace": "org.fl_op.generated",
        "fields": fields,
    }


class AvroCodec(FormatCodec):
    """Reads and writes tabular data as Avro container files using fastavro."""

    @property
    def extension(self) -> str:
        return ".avro"

    def read(self, path: pathlib.Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        with path.open("rb") as fh:
            reader = fastavro.reader(fh)
            return [dict(record) for record in reader]

    def write(self, records: list[dict[str, Any]], path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = _normalize(records)
        if not normalized:
            schema = {
                "type": "record",
                "name": "Empty",
                "namespace": "org.fl_op.generated",
                "fields": [],
            }
            parsed = fastavro.parse_schema(schema)
            with path.open("wb") as fh:
                fastavro.writer(fh, parsed, [])
            return
        schema = _infer_schema(path.stem, normalized[0])
        parsed = fastavro.parse_schema(schema)
        with path.open("wb") as fh:
            fastavro.writer(fh, parsed, normalized)

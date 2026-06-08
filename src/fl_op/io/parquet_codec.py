"""Parquet format codec via pyarrow.

Follows Parquet file-format best practices:
- Uses snappy compression (default, widely supported, good ratio/speed balance).
- Writes a proper file footer with column statistics for predicate pushdown.
- Normalises NumPy scalars to native Python types before ingestion so pyarrow
  schema inference produces portable types (avoids numpy-specific extensions).
- Reads back as plain Python dicts so the rest of the pipeline stays format-agnostic.
"""

import pathlib
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from fl_op.io.base import FormatCodec

_COMPRESSION = "snappy"


def _normalize_val(val: Any) -> Any:
    if val is None:
        return None
    if hasattr(val, "item"):
        return val.item()
    return val


def _normalize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _normalize_val(v) for k, v in r.items()} for r in records]


class ParquetCodec(FormatCodec):
    """Reads and writes tabular data as Parquet files using pyarrow."""

    @property
    def extension(self) -> str:
        return ".parquet"

    def read(self, path: pathlib.Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        table = pq.read_table(str(path))
        return table.to_pylist()

    def write(self, records: list[dict[str, Any]], path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not records:
            pq.write_table(
                pa.table({}),
                str(path),
                compression=_COMPRESSION,
            )
            return
        normalized = _normalize(records)
        table = pa.Table.from_pylist(normalized)
        pq.write_table(
            table,
            str(path),
            compression=_COMPRESSION,
            write_statistics=True,
        )

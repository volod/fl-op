"""Format codec registry and data-directory detection helpers."""

import json
import pathlib
from typing import Any

from fl_op.core.constants import SUPPORTED_DATA_FORMATS
from fl_op.io.avro_codec import AvroCodec
from fl_op.io.base import FormatCodec
from fl_op.io.csv_codec import CsvCodec
from fl_op.io.parquet_codec import ParquetCodec

FORMAT_REGISTRY: dict[str, FormatCodec] = {
    "csv": CsvCodec(),
    "avro": AvroCodec(),
    "parquet": ParquetCodec(),
}


def get_codec(fmt: str) -> FormatCodec:
    """Return the codec for fmt; raise ValueError for unknown formats."""
    if fmt not in FORMAT_REGISTRY:
        raise ValueError(
            f"Unknown format '{fmt}'. Supported: {sorted(FORMAT_REGISTRY)}"
        )
    return FORMAT_REGISTRY[fmt]


def detect_format(data_dir: pathlib.Path) -> str:
    """Return the physical format recorded in data_dir/metadata.json.

    Reads run_metadata.data_format from the metadata file written by generate-data.
    """
    meta_path = data_dir / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Dataset metadata not found: {meta_path}")

    meta: Any = json.loads(meta_path.read_text())
    run_meta = meta.get("run_metadata") if isinstance(meta, dict) else None
    fmt = run_meta.get("data_format") if isinstance(run_meta, dict) else None
    if not fmt:
        raise ValueError(f"Dataset metadata missing run_metadata.data_format: {meta_path}")
    if fmt not in SUPPORTED_DATA_FORMATS:
        raise ValueError(
            f"Unsupported dataset format '{fmt}' in {meta_path}. "
            f"Supported: {sorted(SUPPORTED_DATA_FORMATS)}"
        )
    return fmt


def locate_source(
    data_dir: pathlib.Path, source_file: str, codec: FormatCodec
) -> pathlib.Path:
    """Build the physical path for source_file using codec's extension.

    Strips the extension from the registry source_file (e.g. 'vehicles.csv')
    and replaces it with codec.extension so callers remain format-agnostic.
    """
    stem = pathlib.Path(source_file).stem
    return data_dir / f"{stem}{codec.extension}"

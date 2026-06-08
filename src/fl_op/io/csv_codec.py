"""CSV format codec."""

import csv
import pathlib
from typing import Any

from fl_op.io.base import FormatCodec


class CsvCodec(FormatCodec):
    """Reads and writes tabular data as CSV."""

    @property
    def extension(self) -> str:
        return ".csv"

    def read(self, path: pathlib.Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        with path.open() as fh:
            return list(csv.DictReader(fh))

    def write(self, records: list[dict[str, Any]], path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not records:
            path.write_text("")
            return
        with path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)

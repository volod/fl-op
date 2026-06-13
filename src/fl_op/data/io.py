"""CSV and JSON read/write helpers for dataset generation."""

import csv
import json
import pathlib
from typing import Any


def _write_csv(records: list[dict[str, Any]], path: pathlib.Path) -> None:
    if not records:
        path.write_text("")
        return
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


def _write_json(obj: Any, path: pathlib.Path) -> None:
    with path.open("w") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _write_jsonl(records: list[dict[str, Any]], path: pathlib.Path) -> None:
    with path.open("w") as fh:
        for record in records:
            fh.write(json.dumps(record, default=str) + "\n")


def _load_csv_or_empty(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


def _merge_real_into_synthetic(
    real: list[dict[str, Any]],
    synthetic: list[dict[str, Any]],
    id_key: str,
) -> list[dict[str, Any]]:
    """Return real records merged with synthetic; real takes priority by id_key."""
    real_ids = {r[id_key] for r in real}
    filtered_synthetic = [s for s in synthetic if s[id_key] not in real_ids]
    return real + filtered_synthetic

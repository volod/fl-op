"""Abstract base class for physical format codecs."""

import abc
import pathlib
from typing import Any


class FormatCodec(abc.ABC):
    """Read and write dataset records in a specific physical format.

    All codecs operate on plain Python dicts so the pipeline and model
    logic remain format-agnostic.
    """

    @property
    @abc.abstractmethod
    def extension(self) -> str:
        """File extension including the leading dot, e.g. '.avro'."""

    @abc.abstractmethod
    def read(self, path: pathlib.Path) -> list[dict[str, Any]]:
        """Read records from path. Returns [] if path does not exist."""

    @abc.abstractmethod
    def write(self, records: list[dict[str, Any]], path: pathlib.Path) -> None:
        """Write records to path. Creates parent directories as needed."""

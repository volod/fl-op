"""Physical format codec package.

Provides a format-agnostic read/write interface so pipeline and model logic
remain decoupled from the on-disk serialization format.
"""

from fl_op.io.base import FormatCodec
from fl_op.io.registry import FORMAT_REGISTRY, detect_format, get_codec, locate_source

__all__ = [
    "FormatCodec",
    "FORMAT_REGISTRY",
    "detect_format",
    "get_codec",
    "locate_source",
]

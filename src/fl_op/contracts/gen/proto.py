"""Generate proto3 schemas from ODCS contracts and optionally compile them.

Generated .proto files contain no semantic metadata. They carry only field
names, proto3 scalar types, field numbers, and field descriptions as comments.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fl_op.contracts.gen.base import (
    PHYSICAL_TYPE_TO_PROTO,
    PROTO3_RESERVED_WORDS,
    GenerationError,
    GeneratorBase,
    iter_schema_properties,
)

logger = logging.getLogger(__name__)

_INDENT = "  "


class ProtoGenerator(GeneratorBase):
    FORMAT_KEY = "proto"

    _REQUIRED_SCHEMA_KEYS = ("package", "messageName")

    def generate(self, odcs_doc: dict[str, Any], contract_id: str) -> str:
        hints = self.get_schema_gen_hints(odcs_doc)
        for key in self._REQUIRED_SCHEMA_KEYS:
            if not hints.get(key):
                raise GenerationError(
                    f"{contract_id}: schemaGeneration.proto missing required key '{key}'"
                )
        syntax = hints.get("syntax", "proto3")

        lines: list[str] = [
            f'syntax = "{syntax}";',
            "",
            f"package {hints['package']};",
            "",
        ]

        description = odcs_doc.get("description") or {}
        purpose = description.get("purpose", "") if isinstance(description, dict) else ""
        if purpose:
            lines.append(f"// {purpose}")

        lines.append(f"message {hints['messageName']} {{")

        field_numbers: list[int] = []
        field_blocks: list[list[str]] = []

        for prop in iter_schema_properties(odcs_doc):
            block, field_number = self._build_field_lines(prop, contract_id)
            field_numbers.append(field_number)
            field_blocks.append(block)

        if len(field_numbers) != len(set(field_numbers)):
            dupes = [n for n in field_numbers if field_numbers.count(n) > 1]
            raise GenerationError(
                f"{contract_id}: duplicate proto field numbers: {sorted(set(dupes))}"
            )

        for block in field_blocks:
            for line in block:
                lines.append(line)

        lines.append("}")
        return "\n".join(lines) + "\n"

    def _build_field_lines(
        self, prop: dict[str, Any], contract_id: str
    ) -> tuple[list[str], int]:
        name = prop.get("name", "")
        physical_type = prop.get("physicalType", "")
        if physical_type not in PHYSICAL_TYPE_TO_PROTO:
            raise GenerationError(
                f"{contract_id}: field '{name}' has unknown physicalType '{physical_type}'"
            )
        if name in PROTO3_RESERVED_WORDS:
            raise GenerationError(
                f"{contract_id}: field '{name}' conflicts with a proto3 reserved word"
            )

        proto_type = PHYSICAL_TYPE_TO_PROTO[physical_type]
        required = prop.get("required", True)
        field_hints = self.get_field_gen_hints(prop)

        field_number = field_hints.get("fieldNumber")
        if not isinstance(field_number, int) or field_number < 1:
            raise GenerationError(
                f"{contract_id}: field '{name}' missing valid fieldGeneration.proto.fieldNumber"
            )

        qualifier = "" if required else "optional "
        description = prop.get("description", "")

        lines: list[str] = []
        if description:
            lines.append(f"{_INDENT}// {description}")
        lines.append(f"{_INDENT}{qualifier}{proto_type} {name} = {field_number};")
        return lines, field_number


class ProtoCompiler:
    """Runs protoc to compile a generated .proto file."""

    def compile(
        self,
        proto_path: Path,
        out_dir: Path,
        python_out: bool = True,
    ) -> bool:
        """Compile proto_path; return True on success, False if protoc is unavailable."""
        protoc = shutil.which("protoc")
        if protoc is None:
            logger.warning(
                "protoc not found on PATH; skipping compilation of %s. "
                "Install the protobuf compiler to enable compilation.",
                proto_path.name,
            )
            return False

        cmd = [protoc, f"--proto_path={proto_path.parent}", str(proto_path)]
        if python_out:
            out_dir.mkdir(parents=True, exist_ok=True)
            cmd.append(f"--python_out={out_dir}")

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info("Compiled %s -> %s", proto_path.name, out_dir)
            return True
        except subprocess.CalledProcessError as exc:
            logger.error("protoc failed for %s:\n%s", proto_path.name, exc.stderr)
            raise GenerationError(
                f"protoc compilation failed for {proto_path.name}: {exc.stderr}"
            ) from exc

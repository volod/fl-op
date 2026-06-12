"""Multi-format physical schema generation from ODCS contracts.

Entry points:
  fl-op contracts generate --format=avro|proto|es|parquet
  fl-op contracts check-generation --format=avro|proto|es|parquet
"""

import logging
import pathlib
from typing import Any, Optional

import yaml

from fl_op.contracts.gen.avro import AvroGenerator
from fl_op.contracts.gen.checker import GenerationCheckReport, check_generation
from fl_op.contracts.gen.es import EsGenerator
from fl_op.contracts.gen.parquet import ParquetGenerator
from fl_op.contracts.gen.proto import ProtoCompiler, ProtoGenerator
from fl_op.contracts.gen.base import GenerationError
from fl_op.core.paths import CONTRACTS_ROOT

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = ("avro", "proto", "es", "parquet")

_GENERATORS = {
    "avro": AvroGenerator(),
    "proto": ProtoGenerator(),
    "es": EsGenerator(),
    "parquet": ParquetGenerator(),
}

_FILE_EXTENSIONS = {
    "avro": ".avsc",
    "proto": ".proto",
    "es": ".es.json",
    "parquet": ".parquet.json",
}


def _load_odcs(path: pathlib.Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"ODCS file {path} is not a mapping document")
    return doc


def generate_schema(
    odcs_doc: dict[str, Any],
    contract_id: str,
    fmt: str,
    out_dir: pathlib.Path,
    compile_proto: bool = True,
) -> pathlib.Path:
    """Generate the physical schema for one contract; return the output path."""
    generator = _GENERATORS[fmt]
    content = generator.generate(odcs_doc, contract_id)
    ext = _FILE_EXTENSIONS[fmt]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{contract_id}{ext}"
    out_path.write_text(content, encoding="utf-8")
    logger.info("Generated %s -> %s", fmt, out_path)

    if fmt == "proto" and compile_proto:
        ProtoCompiler().compile(out_path, out_dir)

    return out_path


def run_generate(
    fmt: str,
    out_dir: Optional[pathlib.Path] = None,
    contract_id: Optional[str] = None,
    contracts_root: Optional[pathlib.Path] = None,
) -> bool:
    """Generate schemas for all (or one) ODCS contracts. Returns True on full success."""
    root = contracts_root or CONTRACTS_ROOT
    out_dir = out_dir or (root / "generated" / fmt)
    index = yaml.safe_load((root / "registry.yaml").read_text())
    contracts = index.get("contracts") or {}

    all_ok = True
    for cid, spec in contracts.items():
        if contract_id and cid != contract_id:
            continue
        odcs_ref = spec.get("odcs")
        if not odcs_ref:
            logger.debug("Skipping %s: no ODCS file", cid)
            continue
        try:
            odcs_doc = _load_odcs(root / odcs_ref)
            generate_schema(odcs_doc, cid, fmt, out_dir)
        except (GenerationError, ValueError) as exc:
            logger.error("Generation failed for %s [%s]: %s", cid, fmt, exc)
            all_ok = False

    # The canonical plan OUTPUT contract gets physical schemas too, so
    # downstream consumers can validate published plan artifacts without
    # this codebase.
    from fl_op.contracts.plan_schema_gen import (
        PLAN_CONTRACT_ID,
        PLAN_OUTPUT_FORMATS,
        write_plan_schema,
    )

    if fmt in PLAN_OUTPUT_FORMATS and contract_id in (None, PLAN_CONTRACT_ID):
        try:
            write_plan_schema(fmt, out_dir, root)
        except (GenerationError, ValueError) as exc:
            logger.error("Generation failed for %s [%s]: %s", PLAN_CONTRACT_ID, fmt, exc)
            all_ok = False

    return all_ok


def run_check_generation(
    fmt: str,
    contract_id: Optional[str] = None,
    contracts_root: Optional[pathlib.Path] = None,
) -> tuple[bool, list[GenerationCheckReport]]:
    """Check generation readiness for all (or one) contracts. Returns (ok, reports)."""
    root = contracts_root or CONTRACTS_ROOT
    index = yaml.safe_load((root / "registry.yaml").read_text())
    contracts = index.get("contracts") or {}

    reports: list[GenerationCheckReport] = []
    for cid, spec in contracts.items():
        if contract_id and cid != contract_id:
            continue
        odcs_ref = spec.get("odcs")
        if not odcs_ref:
            continue
        odcs_doc = _load_odcs(root / odcs_ref)
        reports.append(check_generation(odcs_doc, cid, fmt))

    return all(r.ok for r in reports), reports

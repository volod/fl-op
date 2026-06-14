"""Registry-driven domain dataset generator dispatch."""

import importlib
import pathlib
from dataclasses import dataclass
from typing import Any, Callable

from fl_op.contracts.registry import FileRegistry
from fl_op.core.constants import DEFAULT_DATA_FORMAT


@dataclass(frozen=True)
class GenerationRequest:
    """Domain-neutral generator inputs from the generate-data CLI."""

    vehicles: int
    implements: int
    orders: int
    depots: int
    seed: int | None
    data_path: str | None = None
    fmt: str = DEFAULT_DATA_FORMAT


DomainGenerator = Callable[[GenerationRequest], pathlib.Path | None]


def _load_callable(ref: str) -> DomainGenerator:
    module_name, sep, attr_name = ref.partition(":")
    if not sep or not module_name or not attr_name:
        raise ValueError(
            f"Generator reference '{ref}' must be in module:function form"
        )
    module = importlib.import_module(module_name)
    fn = getattr(module, attr_name)
    if not callable(fn):
        raise TypeError(f"Generator reference '{ref}' is not callable")
    return fn


def registered_generator_domains(registry: FileRegistry | None = None) -> list[str]:
    """Domains whose registry spec declares a dataset generator."""
    registry = registry or FileRegistry()
    domains = registry.index.get("domains") or {}
    return sorted(
        domain for domain, spec in domains.items() if isinstance(spec, dict) and spec.get("generator")
    )


def domain_generator_capabilities(
    domain: str, registry: FileRegistry | None = None
) -> dict[str, Any]:
    """Capability metadata for one generator-bearing domain.

    Wraps ``FileRegistry.generator_capabilities`` so the generate-data CLI and
    provenance tooling can report what a domain produces (canonical entities,
    staged contracts, source formats, declared extras) without reaching into the
    registry internals.
    """
    registry = registry or FileRegistry()
    return registry.generator_capabilities(domain)


def all_generator_capabilities(
    registry: FileRegistry | None = None,
) -> dict[str, dict[str, Any]]:
    """Capability metadata for every domain that declares a generator."""
    registry = registry or FileRegistry()
    return {
        domain: registry.generator_capabilities(domain)
        for domain in registered_generator_domains(registry)
    }


def run_domain_generator(
    domain: str,
    request: GenerationRequest,
    registry: FileRegistry | None = None,
) -> pathlib.Path | None:
    """Run the data generator declared by one domain pack."""
    registry = registry or FileRegistry()
    spec = registry.get_domain_spec(domain)
    ref = spec.get("generator")
    if not ref:
        raise KeyError(f"Domain '{domain}' does not declare a data generator")
    return _load_callable(str(ref))(request)

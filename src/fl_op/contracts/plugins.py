"""Domain-pack plugin discovery via Python entry points.

An external domain pack ships as an installed distribution that advertises an
entry point in the ``fl_op.domain_packs`` group, pointing at a callable that
returns its contribution: the domain spec (root, profile, generator,
``semanticModelRef``, ``version``, ...) plus the contract and profile specs it
registers, with *absolute* file refs into the installed package. The registry
merges discovered packs into the in-repo ``registry.yaml`` index at load time,
so installing the package is enough -- no edit to this repo's ``registry.yaml``
and no hardcoded import.

The contribution mirrors a ``registry.yaml`` slice, so a pack is just data the
registry already knows how to consume. Because ``pathlib`` resets on an absolute
right-hand side (``root / "/abs/x"`` == ``/abs/x``), a contribution's absolute
``odcs``/``mapping`` refs resolve correctly through the registry's existing
``self.root / ref`` joins without any special-casing.

Discovery is defensive: a broken or conflicting plugin is logged and skipped,
never breaking ``FileRegistry()``. ``FL_OP_DISABLE_PLUGINS=1`` turns discovery
off entirely (the in-repo registry is then authoritative on its own).
"""

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Entry-point group an external distribution advertises a domain pack under.
DOMAIN_PACK_ENTRY_POINT_GROUP = "fl_op.domain_packs"

# Set to a truthy value to skip plugin discovery (the in-repo registry stands
# alone). Useful for hermetic tests and reproducible CI.
PLUGINS_DISABLED_ENV = "FL_OP_DISABLE_PLUGINS"


@dataclass(frozen=True)
class DomainPackContribution:
    """One discovered domain pack's slice of the registry index."""

    domain: str
    spec: dict[str, Any]
    entry_point: str
    distribution: Optional[str] = None
    contracts: dict[str, Any] = field(default_factory=dict)
    profiles: dict[str, Any] = field(default_factory=dict)


def _plugins_disabled() -> bool:
    return os.environ.get(PLUGINS_DISABLED_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _entry_points() -> list[Any]:
    """Entry points advertised under the domain-pack group, across versions."""
    from importlib import metadata

    try:
        eps = metadata.entry_points(group=DOMAIN_PACK_ENTRY_POINT_GROUP)
    except TypeError:
        # Python <3.10 returns a group->list mapping and rejects group=.
        eps = metadata.entry_points().get(DOMAIN_PACK_ENTRY_POINT_GROUP, [])
    return list(eps)


def _coerce_contribution(
    raw: Any, entry_point: str, distribution: Optional[str]
) -> Optional[DomainPackContribution]:
    """Normalize a plugin's return value into a DomainPackContribution.

    Accepts a ready ``DomainPackContribution`` or a plain mapping with at least
    ``domain`` and ``spec`` keys (plus optional ``contracts``/``profiles``).
    """
    if isinstance(raw, DomainPackContribution):
        return raw
    if not isinstance(raw, dict):
        logger.warning(
            "Domain-pack plugin '%s' returned %s, expected a mapping; skipping",
            entry_point,
            type(raw).__name__,
        )
        return None
    domain = raw.get("domain")
    spec = raw.get("spec")
    if not isinstance(domain, str) or not domain or not isinstance(spec, dict):
        logger.warning(
            "Domain-pack plugin '%s' must provide a 'domain' string and 'spec' "
            "mapping; skipping",
            entry_point,
        )
        return None
    return DomainPackContribution(
        domain=domain,
        spec=dict(spec),
        entry_point=entry_point,
        distribution=distribution,
        contracts=dict(raw.get("contracts") or {}),
        profiles=dict(raw.get("profiles") or {}),
    )


def _distribution_name(entry_point: Any) -> Optional[str]:
    """The installed distribution an entry point belongs to, if exposed."""
    metadata = getattr(getattr(entry_point, "dist", None), "metadata", None)
    if metadata is None:
        return None
    try:
        return metadata.get("Name")
    except Exception:  # noqa: BLE001 - distribution metadata is best-effort only
        return None


def _load_contributions(entry_point: Any) -> list[DomainPackContribution]:
    """Invoke one entry point and normalize its (possibly multiple) packs."""
    name = getattr(entry_point, "name", str(entry_point))
    dist = _distribution_name(entry_point)
    try:
        target = entry_point.load()
        raw = target() if callable(target) else target
    except Exception as exc:  # noqa: BLE001 - one bad plugin must not break discovery
        logger.warning("Skipping domain-pack plugin '%s': %s", name, exc)
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    contributions: list[DomainPackContribution] = []
    for item in items:
        contribution = _coerce_contribution(item, name, dist)
        if contribution is not None:
            contributions.append(contribution)
    return contributions


@lru_cache(maxsize=1)
def discover_domain_packs() -> tuple[DomainPackContribution, ...]:
    """Discover installed domain packs through the entry-point group.

    Cached for the process so repeated ``FileRegistry()`` construction does not
    rescan installed distributions; call :func:`clear_plugin_cache` after
    installing a pack mid-process or in tests that script entry points.
    """
    if _plugins_disabled():
        return ()
    discovered: list[DomainPackContribution] = []
    for entry_point in _entry_points():
        discovered.extend(_load_contributions(entry_point))
    if discovered:
        logger.info(
            "Discovered %d domain-pack plugin(s): %s",
            len(discovered),
            ", ".join(sorted(c.domain for c in discovered)),
        )
    return tuple(discovered)


def clear_plugin_cache() -> None:
    """Forget the cached discovery result (tests; mid-process installs)."""
    discover_domain_packs.cache_clear()


def _conflicts(index: dict[str, Any], contribution: DomainPackContribution) -> list[str]:
    """Index keys a contribution would collide with (in-repo always wins)."""
    conflicts: list[str] = []
    if contribution.domain in (index.get("domains") or {}):
        conflicts.append(f"domain '{contribution.domain}'")
    existing_contracts = index.get("contracts") or {}
    conflicts += [
        f"contract '{cid}'" for cid in contribution.contracts if cid in existing_contracts
    ]
    existing_profiles = index.get("profiles") or {}
    conflicts += [
        f"profile '{pid}'" for pid in contribution.profiles if pid in existing_profiles
    ]
    return conflicts


def merge_contributions(
    index: dict[str, Any], contributions: tuple[DomainPackContribution, ...]
) -> dict[str, DomainPackContribution]:
    """Merge discovered packs into a registry index in place.

    Each contribution is applied atomically: if any of its domain, contract, or
    profile keys already exists in the index, the whole contribution is skipped
    with a warning so a plugin can never shadow a core domain or silently
    half-register. Returns the {domain: contribution} map of packs actually
    merged, for provenance/capability reporting.
    """
    domains = index.setdefault("domains", {})
    contracts = index.setdefault("contracts", {})
    profiles = index.setdefault("profiles", {})
    merged: dict[str, DomainPackContribution] = {}
    for contribution in contributions:
        conflicts = _conflicts(index, contribution)
        if conflicts:
            logger.warning(
                "Domain-pack plugin '%s' (%s) conflicts with the in-repo registry "
                "(%s); skipping",
                contribution.domain,
                contribution.entry_point,
                ", ".join(conflicts),
            )
            continue
        domains[contribution.domain] = dict(contribution.spec)
        contracts.update(contribution.contracts)
        profiles.update(contribution.profiles)
        merged[contribution.domain] = contribution
    return merged

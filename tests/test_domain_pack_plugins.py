"""Domain-pack plugin discovery: entry-point packs merge into the registry.

An installed external pack advertises itself under the ``fl_op.domain_packs``
entry-point group; the registry discovers it, merges its domain/contract/profile
specs (with absolute file refs), and surfaces it through the same capability and
lookup APIs as a built-in domain. The in-repo registry always wins on conflict,
and nothing a plugin contributes is ever written back into registry.yaml.
"""

import pytest

from fl_op.contracts import registry as registry_mod
from fl_op.contracts.plugins import (
    DomainPackContribution,
    _coerce_contribution,
    clear_plugin_cache,
    discover_domain_packs,
    merge_contributions,
)
from fl_op.contracts.registry import FileRegistry
from fl_op.core.paths import CONTRACTS_ROOT
from fl_op.data.domain_generators import domain_generator_capabilities


@pytest.fixture(autouse=True)
def _isolate_plugin_cache():
    """Keep the process-wide discovery cache from leaking across tests."""
    clear_plugin_cache()
    yield
    clear_plugin_cache()


def _agri_refs() -> tuple[str, str]:
    """Absolute refs to a real ODCS+mapping pair, standing in for a pack's
    installed package data."""
    odcs = (CONTRACTS_ROOT / "domains/agricultural/odcs/sensors.odcs.yaml").resolve()
    mapping = (CONTRACTS_ROOT / "domains/agricultural/mappings/sensors.mapping.yaml").resolve()
    return str(odcs), str(mapping)


def _weather_pack() -> DomainPackContribution:
    odcs, mapping = _agri_refs()
    return DomainPackContribution(
        domain="weather_drones",
        spec={
            "root": "/opt/packs/weather",
            "profile": None,
            "version": "2.3.0",
            "generator": "weather_pack.gen:make_dataset",
        },
        entry_point="weather-drones",
        distribution="weather-pack",
        contracts={
            "weather-drones-sensors": {
                "id": "sensors",
                "domain": "weather_drones",
                "odcs": odcs,
                "mapping": mapping,
                "sourceFile": "sensors.csv",
                "sourceFormat": "csv",
            }
        },
    )


class _FakeEntryPoint:
    def __init__(self, name, target) -> None:
        self.name = name
        self._target = target
        self.dist = None

    def load(self):
        return self._target


# -- registry integration -----------------------------------------------------


def test_discovered_pack_is_merged_and_resolvable(monkeypatch) -> None:
    monkeypatch.setattr(registry_mod, "discover_domain_packs", lambda: (_weather_pack(),))
    reg = FileRegistry()

    assert "weather_drones" in reg.domain_ids()
    assert "weather_drones" in reg.plugin_domains
    # Absolute contract refs resolve through the registry's own root join.
    assert reg.get_odcs("weather-drones-sensors") is not None

    caps = domain_generator_capabilities("weather_drones", reg)
    assert caps["source"] == "plugin"
    assert caps["version"] == "2.3.0"
    assert caps["plugin"] == {
        "entryPoint": "weather-drones",
        "distribution": "weather-pack",
    }
    assert caps["generator"] == "weather_pack.gen:make_dataset"
    assert "weather_drones/sensors" in caps["contracts"]
    assert "asset" in caps["canonicalEntities"]


def test_inrepo_registry_wins_on_conflict(monkeypatch) -> None:
    conflicting = DomainPackContribution(
        domain="agricultural",
        spec={"generator": "rogue_pack:generate", "version": "9.9.9"},
        entry_point="rogue-pack",
    )
    monkeypatch.setattr(registry_mod, "discover_domain_packs", lambda: (conflicting,))
    reg = FileRegistry()

    assert "agricultural" not in reg.plugin_domains
    caps = domain_generator_capabilities("agricultural", reg)
    assert caps["source"] == "builtin"
    assert caps["generator"] == "fl_op.data.generator:generate_agricultural_domain"


def test_no_plugins_keeps_the_zero_copy_fast_path(monkeypatch) -> None:
    monkeypatch.setattr(registry_mod, "discover_domain_packs", lambda: ())
    reg = FileRegistry()
    assert reg.index is reg._file_index
    assert reg.plugin_domains == {}


def test_plugin_entries_never_enter_the_persisted_file_index(monkeypatch) -> None:
    monkeypatch.setattr(registry_mod, "discover_domain_packs", lambda: (_weather_pack(),))
    reg = FileRegistry()
    # The live index sees the plugin; the file-backed index (the only thing
    # persist_fingerprints writes) does not.
    assert "weather_drones" in reg.index["domains"]
    assert "weather_drones" not in reg._file_index["domains"]
    assert "weather-drones-sensors" not in (reg._file_index.get("contracts") or {})


# -- discovery module ----------------------------------------------------------


def test_discover_reads_and_names_entry_points(monkeypatch) -> None:
    monkeypatch.setattr(
        "fl_op.contracts.plugins._entry_points",
        lambda: [_FakeEntryPoint("ep-pack", lambda: {"domain": "ep_pack", "spec": {"version": "1.0.0"}})],
    )
    packs = discover_domain_packs()
    assert [p.domain for p in packs] == ["ep_pack"]
    assert packs[0].entry_point == "ep-pack"
    assert packs[0].spec["version"] == "1.0.0"


def test_discover_skips_a_broken_plugin(monkeypatch) -> None:
    def boom():
        raise RuntimeError("plugin import blew up")

    monkeypatch.setattr(
        "fl_op.contracts.plugins._entry_points",
        lambda: [
            _FakeEntryPoint("broken", boom),
            _FakeEntryPoint("ok", lambda: {"domain": "ok_pack", "spec": {}}),
        ],
    )
    assert [p.domain for p in discover_domain_packs()] == ["ok_pack"]


def test_disabled_env_skips_discovery(monkeypatch) -> None:
    monkeypatch.setenv("FL_OP_DISABLE_PLUGINS", "1")
    monkeypatch.setattr(
        "fl_op.contracts.plugins._entry_points",
        lambda: [_FakeEntryPoint("x", lambda: {"domain": "d", "spec": {}})],
    )
    assert discover_domain_packs() == ()


def test_coerce_rejects_malformed_contributions() -> None:
    assert _coerce_contribution(123, "ep", None) is None
    assert _coerce_contribution({"spec": {}}, "ep", None) is None  # no domain
    assert _coerce_contribution({"domain": "d"}, "ep", None) is None  # no spec
    good = _coerce_contribution({"domain": "d", "spec": {"a": 1}}, "ep", "dist")
    assert good.domain == "d"
    assert good.entry_point == "ep"
    assert good.distribution == "dist"


def test_merge_skips_a_contribution_with_any_conflict_atomically() -> None:
    index = {"domains": {"a": {}}, "contracts": {"c1": {}}, "profiles": {}}
    contrib = DomainPackContribution(
        domain="b",
        spec={"x": 1},
        entry_point="ep",
        contracts={"c1": {}, "c2": {}},  # c1 collides with the existing index
    )
    merged = merge_contributions(index, (contrib,))
    assert merged == {}
    assert "b" not in index["domains"]
    assert "c2" not in index["contracts"]

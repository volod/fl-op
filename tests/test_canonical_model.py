"""Canonical model construction, immutability, and bundle identity."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from fl_op.canonical import (
    Asset,
    Capability,
    PlanningMode,
    PlanningSnapshot,
    TimeInterval,
    VersionDimensions,
    compute_bundle_id,
)


def _ts() -> datetime:
    return datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_asset_capability_lookup() -> None:
    asset = Asset(
        asset_id="tractor-1",
        asset_type="TRACTOR",
        roles=["mobile-prime-mover"],
        capabilities=[
            Capability(
                capability_id="c1",
                semantic_term="urn:xopt:capability:rated-power",
                value=180.0,
                canonical_unit="kW",
            )
        ],
    )
    assert asset.capability_value("urn:xopt:capability:rated-power") == 180.0
    assert asset.capability_value("urn:xopt:capability:missing") is None


def test_assets_are_frozen() -> None:
    asset = Asset(asset_id="a1", asset_type="TRACTOR")
    with pytest.raises(ValidationError):
        asset.asset_id = "a2"


def test_bundle_id_is_deterministic_and_order_independent() -> None:
    a = compute_bundle_id(["v1", "i1"], ["op1"], "v1")
    b = compute_bundle_id(["i1", "v1"], ["op1"], "v1")
    c = compute_bundle_id(["v1", "i2"], ["op1"], "v1")
    assert a == b
    assert a != c
    assert a.startswith("bundle-")


def test_snapshot_is_frozen_and_excludes_bridge_from_content() -> None:
    snap = PlanningSnapshot(
        snapshot_id="snap-1",
        effective_at=_ts(),
        generated_at=_ts(),
        planning_mode=PlanningMode.PERIODIC,
        planning_horizon=TimeInterval(**{"from": _ts()}),
        version_dimensions=VersionDimensions(optimization_profile_version="0.1.0"),
        snapshot_hash="abc",
    )
    with pytest.raises(ValidationError):
        snap.snapshot_id = "snap-2"

    content = snap.canonical_content()
    # The snapshot is purely canonical: no non-canonical solver-payload bridge.
    assert "solver_payload" not in content
    assert "snapshot_id" not in content
    assert "generated_at" not in content
    assert "snapshot_hash" not in content


def test_canonical_model_loads_with_required_identity_fields() -> None:
    from fl_op.contracts.canonical_model import load_canonical_model

    model = load_canonical_model()
    assert model.model_ref == "urn:xopt:model:canonical:0.1.0"
    assert set(model.entities()) >= {"asset", "location", "task", "forecast"}
    # Every entity declares at least one required identity binding, and every
    # declared field references a known semantic term.
    for entity in model.entities():
        assert model.required_bindings(entity), f"{entity} has no required field"
        for fld in model.fields_for(entity):
            assert model.has_term(fld.semantic_term), fld.semantic_term
    assert "asset.assetId" in model.required_bindings("asset")

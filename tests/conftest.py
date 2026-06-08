"""Shared test fixtures: small synthetic dataset (50v/200i/20o/5d)."""

import pathlib

import pytest

from fl_op.data.generator import run_generate
from fl_op.io import detect_format, get_codec, locate_source


@pytest.fixture(scope="session")
def dataset_dir(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Generate a small synthetic dataset once per test session."""
    base = tmp_path_factory.mktemp("data")
    import os

    orig_cwd = os.getcwd()
    os.chdir(base)
    try:
        run_generate(n_vehicles=50, n_implements=200, n_orders=20, n_depots=5, seed=99, data_path=None)
        dirs = sorted((base / ".data" / "generate-data").iterdir())
        return dirs[-1]
    finally:
        os.chdir(orig_cwd)


@pytest.fixture(scope="session")
def small_entities(dataset_dir: pathlib.Path):
    """Return (vehicles, implements, orders, depots, fields) as dicts."""
    codec = get_codec(detect_format(dataset_dir))

    def load(name: str):
        return codec.read(locate_source(dataset_dir, f"{name}.csv", codec))

    return {
        "vehicles": load("vehicles"),
        "implements": load("implements"),
        "orders": load("orders"),
        "depots": load("depots"),
        "fields": load("fields"),
        "operators": load("operators"),
    }

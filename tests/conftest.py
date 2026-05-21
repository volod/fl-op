"""Shared test fixtures: small synthetic dataset (50v/200i/20o/5d)."""

import pathlib
import tempfile

import pytest

from fl_op.data.generator import run_generate


@pytest.fixture(scope="session")
def dataset_dir(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Generate a small synthetic dataset once per test session."""
    base = tmp_path_factory.mktemp("data")
    # Temporarily redirect output to tmp
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
    """Return (vehicles, implements, orders, depots, fields) as dicts from CSV."""
    import csv

    def load(name: str):
        with (dataset_dir / name).open() as fh:
            return list(csv.DictReader(fh))

    return {
        "vehicles": load("vehicles.csv"),
        "implements": load("implements.csv"),
        "orders": load("orders.csv"),
        "depots": load("depots.csv"),
        "fields": load("fields.csv"),
        "operators": load("operators.csv"),
    }

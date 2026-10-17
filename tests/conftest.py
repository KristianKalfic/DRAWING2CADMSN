from pathlib import Path

import pytest

BCT_DATA = Path("/app/data")


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """No test writes into the real results folder; D2C_DATA points at the BCT mount."""
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    monkeypatch.setenv("D2C_DATA", str(BCT_DATA))


def pytest_collection_modifyitems(items):
    if BCT_DATA.is_dir():
        return
    skip = pytest.mark.skip(reason="BCT sample data not mounted at /app/data")
    for item in items:
        if item.get_closest_marker("bct"):
            item.add_marker(skip)

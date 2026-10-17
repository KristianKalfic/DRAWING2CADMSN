from pathlib import Path

import pytest

BCT_DATA = Path("/app/data")
TABLES = Path(__file__).parent / "fixtures" / "tables"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Kein Test schreibt in den echten results-Ordner; D2C_DATA zeigt auf den BCT-Mount, D2C_TABLES auf die Testtabellen."""
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    monkeypatch.setenv("D2C_DATA", str(BCT_DATA))
    monkeypatch.setenv("D2C_TABLES", str(TABLES))


def pytest_collection_modifyitems(items):
    if BCT_DATA.is_dir():
        return
    skip = pytest.mark.skip(reason="BCT-Beispieldaten sind nicht unter /app/data eingebunden")
    for item in items:
        if item.get_closest_marker("bct"):
            item.add_marker(skip)

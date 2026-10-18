import sys
from pathlib import Path

import pytest

from d2c.demo import DATA

REF_DATA = Path("/app/data")
TABLES = DATA / "tables"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Kein Test schreibt in den echten results-Ordner; D2C_DATA zeigt auf den Mount der Referenzdaten, D2C_TABLES auf die Testtabellen."""
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    monkeypatch.setenv("D2C_DATA", str(REF_DATA))
    monkeypatch.setenv("D2C_TABLES", str(TABLES))
    monkeypatch.setenv(
        "D2C_ALLOWED_HOSTS", "testserver"
    )  # Host des Starlette-TestClient; im Betrieb gelten nur Loopback und die Umgebungsvariable


@pytest.fixture
def fresh_session(monkeypatch):
    """Der MCP-Server arbeitet mit einer frischen Sitzung statt mit der des vorigen Tests; Aufruf per usefixtures."""
    from d2c.server import tools as srv
    from d2c.session import Session

    monkeypatch.setattr(srv, "session", Session())


@pytest.fixture(autouse=True)
def _lenient_export(request, monkeypatch):
    """Die Gates des Exports lehnen den ersten Nennexport mit einem Befund ab. Die meisten Tests prüfen anderes und
    exportieren deshalb ein zweites Mal; Tests mit der Markierung `export_gate` sehen die Ablehnung."""
    module = sys.modules.get("d2c.session")
    if module is None or request.node.get_closest_marker("export_gate"):
        return
    strict = module.Session.export

    def export(self, part, variant="nominal"):
        result = strict(self, part, variant)
        return strict(self, part, variant) if not result["ok"] and "next" in result else result

    monkeypatch.setattr(module.Session, "export", export)


def pytest_collection_modifyitems(items):
    if REF_DATA.is_dir():
        return
    skip = pytest.mark.skip(reason="Referenzdaten sind nicht unter /app/data eingebunden")
    for item in items:
        if item.get_closest_marker("refdata"):
            item.add_marker(skip)

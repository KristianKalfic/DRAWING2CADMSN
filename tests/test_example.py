"""examples/demo: das eingecheckte Beispielergebnis stimmt mit der Demo überein und lässt sich in der Web-Oberfläche öffnen."""

import json
import subprocess
import sys
from pathlib import Path

from starlette.testclient import TestClient

from d2c.demo import DATA
from d2c.web.app import create_app

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "demo"


PARTS = ("demo_bracket", "demo_shaft", "demo_flange")


def test_example_matches_a_fresh_demo_run():
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_example.py")], capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr


def test_example_opens_in_the_web_app(monkeypatch):
    monkeypatch.setenv("D2C_RESULTS", str(EXAMPLE))
    monkeypatch.setenv("D2C_DATA", str(DATA))
    with TestClient(create_app()) as client:
        parts = client.get("/api/parts").json()
        assert sorted((p["name"], p["all_ok"]) for p in parts) == sorted((name, True) for name in PARTS)
        for name in PARTS:
            assert client.get(f"/files/{name}/{name}.step").status_code == 200
            assert client.get(f"/api/parts/{name}/drawing.png").status_code == 200


def test_every_example_part_has_the_same_files_and_is_compared():
    """Jedes Teil bringt dieselben Dateien mit; check_example.py vergleicht Volumen und Hüllquader aller drei."""
    from importlib import util

    spec = util.spec_from_file_location("check_example", ROOT / "scripts" / "check_example.py")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert tuple(module.PARTS) == PARTS
    names = {"{p}.glb", "{p}.step", "marks.json", "ops.json", "pmi.json", "report.json", "review.json", "review.md", "spec.json"}
    for part in PARTS:
        assert {f.name for f in (EXAMPLE / part).iterdir()} == {n.format(p=part) for n in names}
        report = json.loads((EXAMPLE / part / "report.json").read_text("utf-8"))
        assert report["solid"]["volume"] > 0 and report["roundtrip"]["path"] == f"{part}.step"

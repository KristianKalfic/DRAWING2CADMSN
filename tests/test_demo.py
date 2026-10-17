"""python -m d2c.demo: the bundled demo bracket, built, checked and exported without a language model."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from d2c import demo

FIXTURES = Path(__file__).parent / "fixtures"


def test_demo_cli_ok(tmp_path):
    run = subprocess.run([sys.executable, "-m", "d2c.demo", "--out", str(tmp_path)],
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
    assert (tmp_path / "demo_bracket" / "demo_bracket.step").is_file()
    assert "All checks passed: yes" in run.stdout
    assert "t10" in run.stdout
    assert "FAIL" not in run.stdout


def test_demo_failed_check(tmp_path, monkeypatch, capsys):
    """Ein Modell, das seine erwarteten Größen verfehlt, scheitert: FAIL-Zeile, Urteil nein, Rückkehrcode 1, keine STEP-Datei."""
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    spec = json.loads((FIXTURES / "demo_spec.json").read_text("utf-8"))
    spec["expected"]["bbox"][0] = 81
    (fixtures / "demo_spec.json").write_text(json.dumps(spec), "utf-8")
    monkeypatch.setattr(demo, "FIXTURES", fixtures)
    monkeypatch.setenv("D2C_TABLES", str(fixtures / "tables"))  # main() then leaves the variable alone

    assert demo.main(["--out", str(tmp_path / "out")]) == 1
    out = capsys.readouterr().out
    assert "FAIL bbox_x: expected 81, actual 80" in out
    assert "All checks passed: no" in out
    assert not list((tmp_path / "out").rglob("*.step"))

"""Tests fuer issue_check.compare() / plausible() und die FreeCAD-Fehlerbehandlung.
Der eigentliche FreeCAD-Lauf wird nur getestet, wenn FreeCADCmd installiert ist."""
import json
import os
import shutil
import subprocess
import sys

import pytest

import issue_check as IC
from json_builder import normalize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return normalize(json.load(f))[0]


def codes(issues, sev=None):
    return sorted(i["code"] for i in issues if sev is None or i["severity"] == sev)


# ---------------------------------------------------------------- compare

def test_compare_gleich_innerhalb_toleranz():
    # A: Muster-Objekt, B: 4 Einzelbohrungen in anderer Reihenfolge, Laenge +0.02 mm
    assert IC.compare(load("extraktion_A.json"), load("extraktion_B_gleich.json")) == []


def test_compare_abweichungen():
    issues = IC.compare(load("extraktion_A.json"), load("extraktion_B_abweichend.json"))
    msgs = " | ".join(i["message"] for i in issues)
    assert "width" in msgs and "A=60 im Vergleich zu B=65" in msgs
    assert sum("diameter" in i["message"] for i in issues) == 4
    assert all(i["severity"] == "blocker" for i in issues)


def test_compare_rundungsrauschen_kein_falschalarm():
    a = {"features": [{"type": "hole", "x": 1e-12, "y": 30, "diameter": 8},
                      {"type": "hole", "x": -1e-12, "y": -30, "diameter": 8}]}
    b = {"features": [{"type": "hole", "x": -1e-12, "y": 30, "diameter": 8},
                      {"type": "hole", "x": 1e-12, "y": -30, "diameter": 8}]}
    assert IC.compare(a, b) == []


def test_compare_bohrungsanzahl_bei_gleicher_featureanzahl():
    a = {"features": [{"type": "hole", "x": 0, "y": 0, "diameter": 8}, {"type": "pocket"}]}
    b = {"features": [{"type": "hole", "x": 0, "y": 0, "diameter": 8},
                      {"type": "hole", "x": 10, "y": 0, "diameter": 8}]}
    assert "HOLE_COUNT" in codes(IC.compare(a, b))


# ---------------------------------------------------------------- plausible

def test_plausible_gutes_teil_nur_review_und_warnung():
    issues = IC.plausible(load("extraktion_A.json"))
    assert codes(issues, "blocker") == []
    assert codes(issues, "warning") == ["LOW_CONFIDENCE"]  # Tasche 0.6
    assert codes(issues, "review") == ["ASSUMPTION"]


def test_plausible_unplausibel():
    issues = IC.plausible(load("unplausibel.json"))
    c = codes(issues)
    assert "HOLE_OUTSIDE_BODY" in c          # (22,0) d8 bei L=50
    assert "HOLE_TOO_BIG" in c               # d40 >= min(50,30)
    assert "HOLES_OVERLAP" in c              # (0,0) d6 und (4,0) d6
    assert "FILLET_TOO_BIG" in c             # R20 > 30/2
    assert "LOW_CONFIDENCE" in c
    assert "ASSUMPTION" in c                 # Review-Eintrag ohne 'value' -> kein KeyError


def test_plausible_zylinder_bohrung_aussen():
    issues = IC.plausible(load("zylinder_bohrung_aussen.json"))
    outside = [i for i in issues if i["code"] == "HOLE_OUTSIDE_BODY"]
    assert len(outside) == 4                 # Teilkreis R18 + r2.5 > R20
    assert "HOLES_OVERLAP" not in codes(issues)


# ---------------------------------------------------------------- freecad (Fehlerbehandlung)

def test_freecad_fehlt_klare_meldung(tmp_path):
    step = tmp_path / "x.step"
    step.write_text("dummy")
    issues = IC.freecad(str(step), {}, "gibt_es_nicht_FreeCADCmd")
    assert codes(issues) == ["FREECAD_NOT_FOUND"]
    assert "--freecad" in issues[0]["message"]


def test_freecad_step_fehlt(tmp_path):
    assert codes(IC.freecad(str(tmp_path / "fehlt.step"), {}, "FreeCADCmd")) == ["STEP_MISSING"]


def test_cli_ohne_freecad_gesperrt(tmp_path):
    """Kompletter CLI-Lauf: ohne FreeCAD wird nicht freigegeben, Report wird trotzdem geschrieben."""
    step = tmp_path / "teil.step"
    step.write_text("dummy")
    a = os.path.join(DATA, "extraktion_A.json")
    b = os.path.join(DATA, "extraktion_B_gleich.json")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "issue_check.py"), a, b, str(step),
                        "--freecad", "gibt_es_nicht"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 1, r.stderr
    rep = json.load(open(tmp_path / "teil_issues.json", encoding="utf-8"))
    assert rep["released"] is False
    assert any(i["code"] == "FREECAD_NOT_FOUND" for i in rep["issues"])


FC = shutil.which("FreeCADCmd") or shutil.which("freecadcmd")


@pytest.mark.skipif(not FC, reason="FreeCAD nicht installiert")
def test_freecad_echt(tmp_path, monkeypatch):
    import server as S
    monkeypatch.setattr(S, "OUT", str(tmp_path))
    spec = load("extraktion_A.json")
    r = S.build_from_json(spec)
    e = S.export_step(r["part_id"])
    assert IC.freecad(e["path"], spec, FC) == []

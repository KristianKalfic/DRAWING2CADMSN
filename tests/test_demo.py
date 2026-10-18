"""python -m d2c.demo: die drei mitgelieferten Demo-Teile, ohne Sprachmodell gebaut, geprüft und exportiert."""

import json
import shutil
import subprocess
import sys

import cadquery as cq
import pytest

from d2c.demo import DATA
from d2c.demo import __main__ as demo
from d2c.drawing.vector import extract

PARTS = {"demo": "demo_bracket", "shaft": "demo_shaft", "flange": "demo_flange"}
BBOX = {"demo_bracket": (80, 50, 25), "demo_shaft": (30, 30, 110), "demo_flange": (60, 60, 8)}
VOLUME = {"demo_bracket": 43993.2, "demo_shaft": 56233.5, "demo_flange": 20954.4}  # mm³, Stand des Exports


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Die Demo einmal als Kommandozeile gebaut: Ergebnisordner und Ausgabe, geteilt von allen Erfolgstests."""
    out = tmp_path_factory.mktemp("demo")
    run = subprocess.run([sys.executable, "-m", "d2c.demo", "--out", str(out)], capture_output=True, text=True, timeout=600)
    return out, run


def test_demo_cli_ok(built):
    out, run = built
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1] == "3 of 3 parts exported, all checks passed"
    assert "FAILED" not in run.stdout
    assert "t10" in run.stdout and "t8" in run.stdout  # die Annahmen der Halterung und des Flanschs
    assert "failed" not in run.stderr  # keine Zwischenstände als Warnung


@pytest.mark.parametrize("name", PARTS.values())
def test_part_step_reimports_as_valid_solid(built, name):
    """Die geschriebene STEP-Datei lässt sich neu einlesen: ein gültiger Volumenkörper mit dem Hüllquader und Volumen des Teils."""
    out, _ = built
    step = out / name / f"{name}.step"
    shape = cq.importers.importStep(str(step))
    assert shape.solids().size() == 1
    assert shape.val().isValid()
    box = shape.val().BoundingBox()
    assert (box.xlen, box.ylen, box.zlen) == pytest.approx(BBOX[name], abs=0.01)
    assert shape.val().Volume() == pytest.approx(VOLUME[name], abs=0.1)
    report = json.loads((out / name / "report.json").read_text("utf-8"))
    assert report["roundtrip"]["reimport_valid"] and report["roundtrip"]["solids"] == 1


def test_demo_parts_are_distinct_models(built):
    """Drei verschiedene Körper, nicht dreimal dasselbe Teil."""
    out, _ = built
    steps = {name: (out / name / f"{name}.step").read_bytes() for name in PARTS.values()}
    assert len(set(steps.values())) == 3


def test_shaft_has_steps_and_chamfer(built):
    """Die Welle ist ein Drehkörper mit drei Absätzen; die Fase am Ende nimmt Material weg (Zylinder ohne Fase: 56376,6 mm³)."""
    out, _ = built
    shaft = cq.importers.importStep(str(out / "demo_shaft" / "demo_shaft.step")).val()
    radii = sorted({round(f.BoundingBox().xlen / 2, 3) for f in shaft.Faces() if f.geomType() == "CYLINDER"})  # volle Mäntel
    assert radii == [10, 12, 15]
    assert len([f for f in shaft.Faces() if f.geomType() == "CONE"]) == 1  # die Fase
    assert shaft.Volume() < 56376.6


def test_flange_shows_fit_and_orange_mark(built):
    """Die Passung H7 steht mit Grenzabmaßen in der Toleranzauswertung, die Annahme als orange Markierung in der Prüfliste."""
    out, _ = built
    review = (out / "demo_flange" / "review.md").read_text("utf-8")
    assert "| d12 | Ø12 H7 | 12 | 12 | 12.018 | 12.009 | fit ISO 286 |" in review
    assert "| rv1 | t8 | missing | Flange thickness 8 mm |" in review
    assert json.loads((out / "demo_flange" / "marks.json").read_text("utf-8")) == {"#ff8000": ["rv1"]}
    # Die Annahme steht als Maß im STEP, damit die Prüferin sie dort findet.
    pmi = json.loads((out / "demo_flange" / "pmi.json").read_text("utf-8"))
    assert any("ASSUMPTION" in " ".join(entry["descriptions"]) for entry in pmi["dimensions"])


@pytest.mark.parametrize("prefix", PARTS)
def test_drawing_is_vector_pdf_with_every_printed_dimension(prefix):
    """Zu jedem Teil gehört eine zweiseitige Vektor-PDF mit echtem Text; jedes gedruckte Maß der Spezifikation steht darin."""
    spec = json.loads((DATA / f"{prefix}_spec.json").read_text("utf-8"))
    pdf = DATA / ("demo_drawing.pdf" if prefix == "demo" else f"{prefix}_drawing.pdf")
    text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, encoding="utf-8", check=True).stdout
    assert text.count("\f") == 2  # zwei Seiten
    assert spec["title"] in text
    printed = [d["text"] for d in spec["dimensions"] if not d.get("assumed")]
    assert printed and all(t in text for t in printed), [t for t in printed if t not in text]
    assert "General tolerances ISO 2768-m" in text


def test_flange_drawing_circles_match_the_model():
    """Die Kreise der Flanschzeichnung haben die Durchmesser des Modells (Vektordaten, nicht gedruckte Zahlen)."""
    found = extract(DATA / "flange_drawing.pdf", scale=1, region=(0.21, 0.4, 0.79, 0.7))
    diameters = sorted(round(c["d_mm"], 2) for c in found["circles"])
    assert diameters == [5.5, 5.5, 5.5, 5.5, 12, 60]


def test_demo_failed_check(tmp_path, monkeypatch, capsys):
    """Ein Modell, das seine erwarteten Größen verfehlt, scheitert: Zeile FAILED, Rückkehrcode 1, keine STEP-Datei
    für dieses Teil; die übrigen Teile werden trotzdem gebaut."""
    fixtures = tmp_path / "fixtures"
    shutil.copytree(DATA, fixtures)
    spec = json.loads((DATA / "flange_spec.json").read_text("utf-8"))
    spec["expected"]["bbox"][0] = 61
    (fixtures / "flange_spec.json").write_text(json.dumps(spec), "utf-8")
    monkeypatch.setattr(demo, "DATA", fixtures)
    monkeypatch.setattr(demo, "PARTS", ("shaft", "flange"))
    monkeypatch.setenv("D2C_TABLES", str(fixtures / "tables"))  # main() lässt die Variable unberührt

    assert demo.main(["--out", str(tmp_path / "out")]) == 1
    out = capsys.readouterr().out
    assert "FAILED bbox_x: expected 61, actual 60" in out
    assert out.splitlines()[-1] == "1 of 2 parts exported, not all checks passed"
    assert (tmp_path / "out" / "demo_shaft" / "demo_shaft.step").is_file()
    assert not list((tmp_path / "out" / "demo_flange").rglob("*.step"))


def test_demo_failed_op(tmp_path, monkeypatch, capsys):
    """Ein Vorgang, der nicht baut (Fase größer als das Teil), meldet FAILED mit Vorgangsnummer und Rückkehrcode 1."""
    fixtures = tmp_path / "fixtures"
    shutil.copytree(DATA, fixtures)
    ops = json.loads((DATA / "shaft_ops.json").read_text("utf-8"))
    ops[1]["size"] = 50
    (fixtures / "shaft_ops.json").write_text(json.dumps(ops), "utf-8")
    monkeypatch.setattr(demo, "DATA", fixtures)
    monkeypatch.setattr(demo, "PARTS", ("shaft",))
    monkeypatch.setenv("D2C_TABLES", str(fixtures / "tables"))

    assert demo.main(["--out", str(tmp_path / "out")]) == 1
    out = capsys.readouterr().out
    assert "FAILED operation 1 (edge_finish)" in out
    assert out.splitlines()[-1] == "0 of 1 parts exported, not all checks passed"
    assert not list((tmp_path / "out").rglob("*.step"))

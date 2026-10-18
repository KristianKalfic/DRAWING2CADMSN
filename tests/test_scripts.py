"""Die Skripte von scripts/ (eval_refs.py, replay.py), als Teilprozesse auf in tmp aufgebauten Ordnern.

Referenzen sind hier als STEP geschriebene CadQuery-Platten; Beispieldaten berührt nur der Test mit dem Marker refdata.
"""

import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import cadquery as cq
import pytest
from conftest import REF_DATA

from d2c.config import results_root
from d2c.demo import DATA
from d2c.session import Session

SCRIPTS = Path(__file__).parents[1] / "scripts"
HEADER = ["| Part | ΔV % | ΔBBox max mm | Missing Ø | Extra Ø | MATCH |", "|---|---|---|---|---|---|"]
EVAL_KEYS = {
    "part",
    "dv_pct",
    "dbbox_max_mm",
    "missing_d",
    "extra_d",
    "match",
    "volume",
    "bbox_sorted",
    "ref_volume",
    "ref_bbox_sorted",
    "ref_valid",
}


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    """Ein Skript aus scripts/ in seinem eigenen Prozess, mit der Umgebung des Tests (D2C_RESULTS, D2C_REF_DATA)."""
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True, encoding="utf-8", timeout=300)


def plate(length: float = 40.0, hole: float | None = None) -> cq.Workplane:
    """Eine Platte von Länge x 30 x 10 mm, mit einer durchgehenden Bohrung dieses Durchmessers entlang Z, wenn gegeben."""
    body = cq.Workplane().box(length, 30, 10)
    return body if hole is None else body.faces(">Z").workplane().hole(hole)


def pocketed(depth: float) -> cq.Workplane:
    """Die 40-mm-Platte mit einer Tasche 10 x 10 mm dieser Tiefe in ihrer Deckfläche: kein Zylinder, gleiche Begrenzungsbox."""
    return plate().faces(">Z").workplane().rect(10, 10).cutBlind(-depth)


def save(shape: cq.Workplane | cq.Shape, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(shape, str(path), exportType="STEP")  # ausdrücklich: ein Name *.stp wird nicht erkannt
    return path


@pytest.fixture
def refs(tmp_path, monkeypatch) -> Path:
    """Ein leerer Referenzdatenordner, gesetzt als D2C_REF_DATA."""
    root = tmp_path / "refs"
    root.mkdir()
    monkeypatch.setenv("D2C_REF_DATA", str(root))
    return root


def add_part(name: str, drawing: str, model: cq.Workplane) -> None:
    """results/<name>/ mit einer gültigen spec.json zu `drawing` und dem Modell als <name>.step."""
    spec = json.loads((DATA / "demo_spec.json").read_text("utf-8")) | {"name": name, "drawing": drawing}
    folder = results_root() / name
    folder.mkdir(parents=True)
    (folder / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    save(model, folder / f"{name}.step")


def evaluate() -> tuple[subprocess.CompletedProcess, dict[str, dict]]:
    """eval_refs.py ausführen: der Prozess und die Zeilen von EVAL.json je Teil. Die Tabelle ist auf stdout und in EVAL.md."""
    done = run("eval_refs.py")
    assert done.returncode == 0, done.stderr
    assert (results_root() / "EVAL.md").read_text("utf-8") == done.stdout
    rows = json.loads((results_root() / "EVAL.json").read_text("utf-8"))
    return done, {row["part"]: row for row in rows}


def test_eval_identical_is_match(refs):
    save(plate(hole=5), refs / "Plate A" / "plate.stp")
    add_part("plate", "Plate A/plate.pdf", plate(hole=5))
    done, rows = evaluate()
    row = rows["plate"]
    assert (row["match"], row["dv_pct"], row["dbbox_max_mm"], row["ref_valid"]) == ("MATCH", 0, 0, True)
    assert (row["missing_d"], row["extra_d"]) == ([], [])
    assert done.stdout.splitlines() == [*HEADER, "| plate | 0.00 | 0.000 | - | - | MATCH |"]


def test_eval_detects_missing_diameter(refs):
    save(plate(hole=1), refs / "Plate A" / "plate.stp")
    add_part("plate", "Plate A/plate.pdf", plate())  # dem Modell fehlt die Bohrung O1
    done, rows = evaluate()
    row = rows["plate"]
    # die Bohrung ändert weder Volumen (0,07 %) noch Begrenzungsbox: allein ihr Durchmesser macht den Unterschied
    assert (row["match"], row["missing_d"], row["extra_d"]) == ("DIFF", [1.0], [])
    assert row["dv_pct"] == pytest.approx(0.0655, abs=1e-3) and row["dbbox_max_mm"] == 0
    assert "| plate | 0.07 | 0.000 | 1 | - | DIFF |" in done.stdout


def test_eval_detects_extra_diameter(refs):
    save(plate(), refs / "Plate A" / "plate.stp")
    add_part("plate", "Plate A/plate.pdf", plate(hole=1))  # das Modell hat eine Bohrung, die der Referenz fehlt
    done, rows = evaluate()
    assert (rows["plate"]["match"], rows["plate"]["missing_d"], rows["plate"]["extra_d"]) == ("DIFF", [], [1.0])
    assert "| plate | 0.07 | 0.000 | - | 1 | DIFF |" in done.stdout


def test_eval_ignores_outer_cylinders(refs):
    """Eine Rundung außen (konvexer Zylinder) mit leicht anderem Radius ist keine fehlende oder zusätzliche Bohrung."""
    save(plate(hole=5).edges("|Z and >X and >Y").fillet(3), refs / "Plate A" / "plate.stp")
    add_part("plate", "Plate A/plate.pdf", plate(hole=5).edges("|Z and >X and >Y").fillet(3.1))
    done, rows = evaluate()
    row = rows["plate"]
    assert (row["missing_d"], row["extra_d"], row["match"]) == ([], [], "MATCH")
    assert row["dv_pct"] < 0.1 and row["dbbox_max_mm"] == 0


def test_eval_resolves_folder_with_spaces(refs):
    folder = refs / "Complex Part 3"
    save(plate(hole=5), folder / "9 000 000 0.stp")
    save(plate(hole=7), folder / "9 000 000 0_PMI.stp")  # Köder mit anderen Bohrungen: darf nicht gewählt werden
    add_part("complex", "Complex Part 3/9 000 000 0.pdf", plate(hole=5))
    _, rows = evaluate()
    assert rows["complex"]["match"] == "MATCH"


def test_eval_resolves_decomposed_umlaut(refs):
    """macOS-Mounts listen Umlaute zerlegt (NFD), eine Spezifikation hält sie zusammengesetzt (NFC)."""
    save(plate(hole=5), refs / unicodedata.normalize("NFD", "Hauptträger") / "Hauptträger.stp")
    add_part("carrier", "Hauptträger/Hauptträger_dwg.pdf", plate(hole=5))
    _, rows = evaluate()
    assert rows["carrier"]["match"] == "MATCH"


def test_eval_json_keys(refs):
    save(plate(hole=5), refs / "Plate A" / "plate.stp")
    add_part("plate", "Plate A/plate.pdf", plate(hole=5))
    evaluate()
    rows = json.loads((results_root() / "EVAL.json").read_text("utf-8"))
    assert [set(row) for row in rows] == [EVAL_KEYS]
    assert rows[0]["part"] == "plate"


def test_eval_thresholds(refs):
    """MATCH verlangt Volumen innerhalb 1,0 %, jede sortierte bbox-Kante innerhalb 0,05 mm und dieselben Durchmesser."""
    save(plate(), refs / "Plate A" / "plate.stp")
    cases = {  # part: (model, expected match, volume deviation in %, bbox deviation in mm)
        "edge_within": (plate(40.04), "MATCH", 0.1, 0.04),
        "edge_beyond": (plate(40.06), "DIFF", 0.15, 0.06),
        "pocket_within": (pocketed(0.5), "MATCH", 0.4167, 0),
        "pocket_beyond": (pocketed(2), "DIFF", 1.6667, 0),
    }
    for part, (model, *_) in cases.items():
        add_part(part, "Plate A/plate.pdf", model)
    _, rows = evaluate()
    for part, (_, match, dv_pct, dbbox) in cases.items():
        assert rows[part]["match"] == match, part
        assert rows[part]["dv_pct"] == pytest.approx(dv_pct, abs=1e-3), part
        assert rows[part]["dbbox_max_mm"] == pytest.approx(dbbox, abs=1e-6), part


def test_eval_flags_unusable_references(refs):
    """Kein Ordner, zwei Kandidaten, eine unlesbare Datei oder kein Festkörper: eine Zeile 'reference invalid', niemals ein Misserfolg."""
    save(plate(), refs / "Two" / "a.stp")
    save(plate(), refs / "Two" / "b.stp")
    (refs / "Broken").mkdir()
    (refs / "Broken" / "ref.stp").write_text("not a STEP file")
    save(cq.Face.makePlane(10, 10), refs / "Sheet" / "ref.stp")
    for part, folder in {"nowhere": "Absent", "two": "Two", "broken": "Broken", "sheet": "Sheet"}.items():
        add_part(part, f"{folder}/plate.pdf", plate())
    done, rows = evaluate()
    assert {(row["match"], row["ref_valid"]) for row in rows.values()} == {("reference invalid", False)}
    for part in ("nowhere", "two", "broken"):  # nichts zum Vergleichen: keine Zahlen, und ein Hinweis nennt den Grund
        assert rows[part]["dv_pct"] is None and rows[part]["ref_volume"] is None
        assert f"{part}: " in done.stderr
    assert rows["sheet"]["dv_pct"] is not None and rows["sheet"]["ref_volume"] > 0  # sie lässt sich laden, also wird verglichen


def test_eval_without_results_is_an_empty_table(refs):
    done, rows = evaluate()
    assert (rows, done.stdout.splitlines()) == ({}, HEADER)
    assert json.loads((results_root() / "EVAL.json").read_text("utf-8")) == []


@pytest.fixture
def demo_part() -> Path:
    """results/demo_bracket/ mit der Spezifikation und den Ops des Demoteils, noch nichts gebaut."""
    folder = results_root() / "demo_bracket"
    folder.mkdir(parents=True)
    for name in ("spec", "ops"):
        shutil.copy(DATA / f"demo_{name}.json", folder / f"{name}.json")
    return folder


def test_replay_reports_ok_and_writes_nothing(demo_part):
    done = run("replay.py")
    assert (done.returncode, done.stdout) == (0, "demo_bracket: ok\n"), done.stderr
    assert sorted(p.name for p in demo_part.iterdir()) == ["ops.json", "spec.json"]


def test_replay_fails_on_bad_part(demo_part):
    """Ops, die ihre Spezifikation nicht mehr erfüllen, lassen den Lauf scheitern; jedes Teil bekommt trotzdem seine Zeile."""
    broken = results_root() / "broken"
    broken.mkdir()
    spec = json.loads((demo_part / "spec.json").read_text("utf-8")) | {"name": "broken"}
    ops = json.loads((demo_part / "ops.json").read_text("utf-8"))
    ops[0]["profile"]["w"] = 70  # die Spezifikation erwartet eine Platte von 80 Breite
    (broken / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (broken / "ops.json").write_text(json.dumps(ops), encoding="utf-8")
    done = run("replay.py")
    assert done.returncode == 1, done.stderr
    failed, fine = done.stdout.splitlines()
    assert failed == "broken: FAILED (bbox_x: expected 80, actual 70)"
    assert fine == "demo_bracket: ok"


def test_replay_fails_on_unknown_part(demo_part):
    done = run("replay.py", "demo_bracket", "nope")
    assert done.returncode == 1
    fine, unknown = done.stdout.splitlines()
    assert fine == "demo_bracket: ok" and unknown.startswith("nope: FAILED")


def test_replay_export_rewrites_step(demo_part):
    assert Session().export("demo_bracket")["ok"]  # eine Sitzung, die das Teil von der Platte lädt
    step = demo_part / "demo_bracket.step"
    step.unlink()
    done = run("replay.py", "--export", "demo_bracket")
    assert (done.returncode, done.stdout) == (0, "demo_bracket: ok\n"), done.stderr
    assert step.is_file()
    assert {"report.json", "review.json", "review.md"} <= {p.name for p in demo_part.iterdir()}


def test_replay_export_names_a_gate_and_exports_anyway(demo_part):
    spec = json.loads((demo_part / "spec.json").read_text("utf-8"))
    spec["review"].append({"id": "rv9", "refs": ["d80"], "issue": "ambiguous", "assumption": "Fase vereinfacht", "reason": "unklar"})
    (demo_part / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    done = run("replay.py", "--export", "demo_bracket")
    assert done.returncode == 0, done.stderr
    gate, fine = done.stdout.splitlines()
    assert gate.startswith("demo_bracket: Gate: Export refused") and "rv9" in gate and fine == "demo_bracket: ok"


@pytest.mark.refdata
def test_eval_private_reference_loads():
    """Mindestens eine Referenz der Referenzdaten lädt, ist gültig und hat ein Volumen."""
    steps = sorted(REF_DATA.rglob("*.stp"))
    assert steps
    shapes = [cq.Compound.makeCompound(cq.importers.importStep(str(p)).vals()) for p in steps]
    assert any(s.isValid() and s.Volume() > 0 for s in shapes)

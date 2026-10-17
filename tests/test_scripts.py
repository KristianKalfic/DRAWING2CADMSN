"""The scripts of scripts/ (eval_refs.py, replay.py), run as subprocesses on folders built in tmp.

References are CadQuery plates written to STEP here; sample data is only touched by the bct test.
"""

import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import cadquery as cq
import pytest
from conftest import BCT_DATA

from d2c.session import Session, results_root

SCRIPTS = Path(__file__).parents[1] / "scripts"
FIXTURES = Path(__file__).parent / "fixtures"
HEADER = ["| Part | ΔV % | ΔBBox max mm | Missing Ø | Extra Ø | MATCH |", "|---|---|---|---|---|---|"]
EVAL_KEYS = {"part", "dv_pct", "dbbox_max_mm", "missing_d", "extra_d", "match", "volume", "bbox_sorted",
             "ref_volume", "ref_bbox_sorted", "ref_valid"}


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    """A script of scripts/ in its own process, with the environment of the test (D2C_RESULTS, D2C_REF_DATA)."""
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=300)


def plate(length: float = 40.0, hole: float | None = None) -> cq.Workplane:
    """A plate of length x 30 x 10 mm, with a through hole of that diameter along Z if given."""
    body = cq.Workplane().box(length, 30, 10)
    return body if hole is None else body.faces(">Z").workplane().hole(hole)


def pocketed(depth: float) -> cq.Workplane:
    """The 40 mm plate with a 10 x 10 mm pocket of that depth in its top face: no cylinder, same bounding box."""
    return plate().faces(">Z").workplane().rect(10, 10).cutBlind(-depth)


def save(shape: cq.Workplane | cq.Shape, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(shape, str(path), exportType="STEP")  # explicit: a *.stp name is not recognized
    return path


@pytest.fixture
def refs(tmp_path, monkeypatch) -> Path:
    """An empty reference data folder, set as D2C_REF_DATA."""
    root = tmp_path / "refs"
    root.mkdir()
    monkeypatch.setenv("D2C_REF_DATA", str(root))
    return root


def add_part(name: str, drawing: str, model: cq.Workplane) -> None:
    """results/<name>/ with a valid spec.json for `drawing` and the model as <name>.step."""
    spec = json.loads((FIXTURES / "demo_spec.json").read_text("utf-8")) | {"name": name, "drawing": drawing}
    folder = results_root() / name
    folder.mkdir(parents=True)
    (folder / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    save(model, folder / f"{name}.step")


def evaluate() -> tuple[subprocess.CompletedProcess, dict[str, dict]]:
    """Run eval_refs.py: the process and the rows of EVAL.json by part. The table is on stdout and in EVAL.md."""
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
    add_part("plate", "Plate A/plate.pdf", plate())  # the model lacks the O1 bore
    done, rows = evaluate()
    row = rows["plate"]
    # the bore moves neither volume (0.07 %) nor bounding box: its diameter alone makes the difference
    assert (row["match"], row["missing_d"], row["extra_d"]) == ("DIFF", [1.0], [])
    assert row["dv_pct"] == pytest.approx(0.0655, abs=1e-3) and row["dbbox_max_mm"] == 0
    assert "| plate | 0.07 | 0.000 | 1 | - | DIFF |" in done.stdout


def test_eval_detects_extra_diameter(refs):
    save(plate(), refs / "Plate A" / "plate.stp")
    add_part("plate", "Plate A/plate.pdf", plate(hole=1))  # the model has a bore the reference lacks
    done, rows = evaluate()
    assert (rows["plate"]["match"], rows["plate"]["missing_d"], rows["plate"]["extra_d"]) == ("DIFF", [], [1.0])
    assert "| plate | 0.07 | 0.000 | - | 1 | DIFF |" in done.stdout


def test_eval_resolves_folder_with_spaces(refs):
    folder = refs / "Komplexe Zeichnung 3"
    save(plate(hole=5), folder / "1 763 1487 0.stp")
    save(plate(hole=7), folder / "1 763 1487 0_PMI.stp")  # decoy with other holes: must not be taken
    add_part("complex", "Komplexe Zeichnung 3/1 763 1487 0.pdf", plate(hole=5))
    _, rows = evaluate()
    assert rows["complex"]["match"] == "MATCH"


def test_eval_resolves_decomposed_umlaut(refs):
    """macOS mounts list umlauts decomposed (NFD), while a spec holds them composed (NFC)."""
    save(plate(hole=5), refs / unicodedata.normalize("NFD", "Trägerplatte") / "Trägerplatte.stp")
    add_part("carrier", "Trägerplatte/Trägerplatte_dwg.pdf", plate(hole=5))
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
    """MATCH needs volume within 1.0 %, every sorted bbox edge within 0.05 mm and the same diameters."""
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
    """No folder, two candidates, an unreadable file or no solid: a row 'reference invalid', never a failure."""
    save(plate(), refs / "Two" / "a.stp")
    save(plate(), refs / "Two" / "b.stp")
    (refs / "Broken").mkdir()
    (refs / "Broken" / "ref.stp").write_text("not a STEP file")
    save(cq.Face.makePlane(10, 10), refs / "Sheet" / "ref.stp")
    for part, folder in {"nowhere": "Absent", "two": "Two", "broken": "Broken", "sheet": "Sheet"}.items():
        add_part(part, f"{folder}/plate.pdf", plate())
    done, rows = evaluate()
    assert {(row["match"], row["ref_valid"]) for row in rows.values()} == {("reference invalid", False)}
    for part in ("nowhere", "two", "broken"):  # nothing to compare with: no numbers, and a note says why
        assert rows[part]["dv_pct"] is None and rows[part]["ref_volume"] is None
        assert f"{part}: " in done.stderr
    assert rows["sheet"]["dv_pct"] is not None and rows["sheet"]["ref_volume"] > 0  # it loads, so it is compared


def test_eval_without_results_is_an_empty_table(refs):
    done, rows = evaluate()
    assert (rows, done.stdout.splitlines()) == ({}, HEADER)
    assert json.loads((results_root() / "EVAL.json").read_text("utf-8")) == []


@pytest.fixture
def demo_part() -> Path:
    """results/demo_bracket/ with the spec and ops of the demo part, nothing built yet."""
    folder = results_root() / "demo_bracket"
    folder.mkdir(parents=True)
    for name in ("spec", "ops"):
        shutil.copy(FIXTURES / f"demo_{name}.json", folder / f"{name}.json")
    return folder


def test_replay_reports_ok_and_writes_nothing(demo_part):
    done = run("replay.py")
    assert (done.returncode, done.stdout) == (0, "demo_bracket: ok\n"), done.stderr
    assert sorted(p.name for p in demo_part.iterdir()) == ["ops.json", "spec.json"]


def test_replay_fails_on_bad_part(demo_part):
    """Ops that no longer satisfy their spec fail the run; every part still gets its line."""
    broken = results_root() / "broken"
    broken.mkdir()
    spec = json.loads((demo_part / "spec.json").read_text("utf-8")) | {"name": "broken"}
    ops = json.loads((demo_part / "ops.json").read_text("utf-8"))
    ops[0]["profile"]["w"] = 70  # the spec expects a plate 80 wide
    (broken / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (broken / "ops.json").write_text(json.dumps(ops), encoding="utf-8")
    done = run("replay.py")
    assert done.returncode == 1, done.stderr
    failed, fine = done.stdout.splitlines()
    assert failed == "broken: FAILED (bbox_x: expected 80, got 70)"
    assert fine == "demo_bracket: ok"


def test_replay_fails_on_unknown_part(demo_part):
    done = run("replay.py", "demo_bracket", "nope")
    assert done.returncode == 1
    fine, unknown = done.stdout.splitlines()
    assert fine == "demo_bracket: ok" and unknown.startswith("nope: FAILED")


def test_replay_export_rewrites_step(demo_part):
    assert Session().export("demo_bracket")["ok"]  # a session that loads the part from disk
    step = demo_part / "demo_bracket.step"
    step.unlink()
    done = run("replay.py", "--export", "demo_bracket")
    assert (done.returncode, done.stdout) == (0, "demo_bracket: ok\n"), done.stderr
    assert step.is_file()
    assert {"report.json", "review.json", "review.md"} <= {p.name for p in demo_part.iterdir()}


@pytest.mark.bct
def test_eval_bct_fuss_reference():
    """The reference of the sample part Fuss loads, is valid and has a volume."""
    reference = cq.importers.importStep(str(BCT_DATA / "Fuss" / "Fuss.stp")).vals()
    shape = cq.Compound.makeCompound(reference)
    assert shape.isValid()
    assert shape.Volume() > 0

"""Isolierter Worker für den Modellaufbau (Prozessgrenze, Berichtsprotokoll) und AST-Prüfung der Skripte."""
import json
import math
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import cadquery as cq
import pytest
from pydantic import TypeAdapter, ValidationError

from d2c import worker
from d2c.export import read_pmi
from d2c.ops import Op
from d2c.sandbox import check_script, run_worker, validation_error_message

KEYS = {"ok", "error", "solid", "dims", "build_notes", "roundtrip"}
FORBIDDEN_NAMES = "open exec eval compile __import__ globals locals vars getattr setattr delattr input breakpoint"


@pytest.mark.parametrize("changes, details", [
    ({"profile": {"type": "path", "start": [0, 0], "segments": []}},
     ("segments", "mindestens 1", "too_short")),
    ({"profile": {"type": "hull", "circles": [[0, 0, 2]]}},
     ("circles", "mindestens 2", "too_short")),
    ({"profile": {"type": "circle", "center": [0, 0, 0], "d": 5}},
     ("center", "höchstens 2", "too_long")),
    ({"profile": {"type": "triangle"}},
     ("triangle", "type", "rect", "circle", "slot", "path", "hull", "union_tag_invalid")),
    ({"profile": {}}, ("profile", "Typangabe", "type", "union_tag_not_found")),
    ({"both": "vielleicht"}, ("both", "true", "false", "bool_parsing")),
])
def test_validation_errors_keep_repair_details(changes, details):
    with pytest.raises(ValidationError) as caught:
        TypeAdapter(list[Op]).validate_python([box() | changes])
    message = validation_error_message(caught.value)
    assert all(detail in message for detail in details), message


def box(w=20.0, h=10.0, t=5.0, center=(0, 0), mode="add"):
    """Extrude-Operation: Quader w × h auf XY von z=0 bis z=t."""
    return {"op": "extrude", "profile": {"type": "rect", "center": center, "w": w, "h": h},
            "plane": {"name": "XY"}, "distance": t, "mode": mode}


def script(code):
    return {"op": "script", "code": code}


def payload(*ops, **changes):
    return {"ops": list(ops), "expected": None, "marks": [], "export_path": None} | changes


def failed(error):
    """Bericht eines Laufs ohne erzeugtes Modell: Nur `error` ist gesetzt."""
    return {"ok": False, "error": error, "solid": None, "dims": [], "build_notes": [], "roundtrip": None}


def fake_popen(returncode, stdout=b"", stderr=b""):
    """Ersatz für `subprocess.Popen`: liefert die Ergebnisse sofort und speichert den erhaltenen Aufruf."""
    class Process:
        def __init__(self, args, **kwargs):
            popen.call = {"args": args, **kwargs}
            self.returncode = returncode
            kwargs["stderr"].write(stderr)
            kwargs["stderr"].flush()

        def communicate(self, input=None, timeout=None):
            popen.call["timeout"] = timeout
            return stdout, None

    popen = Process
    return popen


def test_worker_builds_box(tmp_path):
    hole = {"op": "holes", "plane": {"name": "XY", "offset": 5}, "positions": [[5, 2]], "d": 4}
    expected = {"bbox": [20, 10, 5], "holes": [{"d": 4, "at": [5, 2, 0], "axis": [0, 0, 1], "dim_ids": []}]}
    report = run_worker(payload(box(), hole, expected=expected))
    assert set(report) == KEYS
    assert (report["ok"], report["error"]) == (True, None)
    assert report["solid"]["ok"] and report["solid"]["volume"] == pytest.approx(1000 - math.pi * 4 * 5)
    assert [d["check"] for d in report["dims"]] == ["bbox_x", "bbox_y", "bbox_z", "hole"]
    assert all(d["ok"] for d in report["dims"])
    assert report["build_notes"] == [{"op_index": 1, "drilled_d": 4.0, "holes": 1}]
    assert report["roundtrip"]["ok"] is True

    # Eine fehlgeschlagene Prüfung ist ein Ergebnis, kein Fehler: `ok` setzt eine gültige Körperprüfung und bestandene Prüfungen aller Maße voraus.
    wrong_size = run_worker(payload(box(), expected={"bbox": [20, 10, 6]}))
    assert [d["ok"] for d in wrong_size["dims"]] == [True, True, False]
    flip = "result = cq.Workplane().add(cq.Shape.cast(result.val().Solids()[0].wrapped.Reversed()))"
    inside_out = run_worker(payload(box(), script(flip)))  # negatives Volumen
    assert inside_out["solid"]["ok"] is False
    for report in (wrong_size, inside_out):
        assert (report["ok"], report["error"]) == (False, None)

    # Ausgaben von `print()` und OpenCascade dürfen das Protokoll nicht stören:
    # Auf stdout steht genau eine JSON-Zeile; alles Weitere geht auf stderr.
    (tmp_path / "bad.step").write_text("not a STEP file")
    noisy = "print('Hallo')\ntry:\n    cq.importers.importStep('bad.step')\nexcept:\n    pass"
    request = json.dumps(payload(box(), script(noisy))).encode()
    raw = subprocess.run([sys.executable, "-I", "-m", "d2c.worker"], input=request, capture_output=True, cwd=tmp_path)
    assert len(raw.stdout.splitlines()) == 1
    assert json.loads(raw.stdout)["ok"] is True
    assert b"Hallo" in raw.stderr and b"StepFile" in raw.stderr


def test_worker_reports_build_error(monkeypatch):
    assert run_worker(payload(box(mode="cut"))) == failed(
        "BuildError: op 0 (extrude): Der erste Vorgang muss den Modus 'add' haben.")

    # Auch jede andere Fehlerursache muss als Bericht erscheinen und darf nie zum Absturz führen.
    bad_ops = run_worker(payload({"op": "extrude", "distance": 0}, script(123)))
    assert bad_ops == failed(bad_ops["error"]) and bad_ops["error"].startswith("ValidationError: ")
    assert "1.script.code" in bad_ops["error"]  # Ein Skript mit falschem Typ muss der Worker melden, nicht die Prüfung.
    assert "muss Text sein" in bad_ops["error"]
    assert "Input should be a valid string" not in bad_ops["error"]
    bad_expected = run_worker(payload(box(), expected={}))
    assert bad_expected == failed(bad_expected["error"]) and bad_expected["error"].startswith("ValidationError: ")
    assert "bbox: fehlt" in bad_expected["error"]
    bad_mode = run_worker(payload(box(mode="ungültig")))
    assert "'add'" in bad_mode["error"] and "literal_error" in bad_mode["error"]
    assert " or " not in bad_mode["error"]
    syntax = run_worker(payload(box(), script("def (")))
    assert syntax == failed(syntax["error"]) and syntax["error"].startswith("BuildError: op 1 (script): SyntaxError")
    empty = run_worker(payload(box(), box(mode="cut")))  # the cut removes the whole block
    assert empty == failed("BuildError: Das Modell ist leer. Prüfe, ob ein Schnitt das gesamte Material entfernt hat.")
    assert run_worker(payload()) == failed("BuildError: Keine Vorgänge angegeben.")

    # Dasselbe gilt für Ausnahmen aus der Prüfung oder dem Export, unabhängig vom Typ.
    def explode(*args):
        raise RuntimeError("Export fehlgeschlagen")

    monkeypatch.setattr(worker, "step_roundtrip", explode)
    assert worker.make_report(payload(box())) == failed("RuntimeError: Export fehlgeschlagen")


def test_timeout_kills(monkeypatch):
    start = time.monotonic()
    assert run_worker(payload(box(), script("while True: pass")), timeout=2) == failed("Zeitüberschreitung nach 2s")
    assert time.monotonic() - start < 4

    # Eine Druckschleife füllt stderr. Die Ausgabe landet in einer Datei statt im Arbeitsspeicher des Elternprozesses; bei Zeitüberschreitung wird die ganze Prozessgruppe beendet.
    real_popen, started = subprocess.Popen, []

    def recording_popen(*args, **kwargs):
        started.append((real_popen(*args, **kwargs), kwargs))
        return started[-1][0]

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    chatter = "while True:\n    print('x' * 1000)\n    sum(range(10**5))"  # Ohne Begrenzung erreicht die Ausgabe zuerst die Dateigrenze von 200 MiB.
    flood = run_worker(payload(box(), script(chatter)), timeout=2)
    assert flood == failed("Zeitüberschreitung nach 2s")
    process, kwargs = started[0]
    assert kwargs["stdout"] == subprocess.PIPE and kwargs["stderr"] not in (subprocess.PIPE, subprocess.STDOUT, None)
    assert kwargs["start_new_session"] is True
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)
    # Die Adressraumgrenze fängt eine ausufernde Speicherbelegung (4 GiB Listenplätze) als Fehler ab.
    greedy = run_worker(payload(box(), script("slots = [0] * 2**29")))
    assert greedy == failed(greedy["error"]) and greedy["error"].startswith("BuildError: op 1 (script): MemoryError")

    # Der Aufruf nutzt einen isolierten Interpreter, enthält nur `PATH` in der Umgebung und verwendet ein anschließend gelöschtes Arbeitsverzeichnis.
    spy = fake_popen(0, stdout=json.dumps(failed("seen")).encode())
    monkeypatch.setattr(subprocess, "Popen", spy)
    monkeypatch.setenv("D2C_TIMEOUT", "2.5")  # Das Standardzeitlimit wird bei jedem Aufruf neu eingelesen.
    run_worker(payload(box()))
    call = spy.call
    assert call["args"] == [sys.executable, "-I", "-m", "d2c.worker"]
    assert call["env"] == {"PATH": os.environ["PATH"]}
    assert call["timeout"] == 2.5
    assert Path(call["cwd"]) != Path.cwd() and not Path(call["cwd"]).exists()
    monkeypatch.delenv("D2C_TIMEOUT")
    run_worker(payload(box()))
    assert spy.call["timeout"] == 60

    # Grenzen im Kindprozess: 4 GiB Adressraum, 200 MiB Dateigröße und CPU-Zeit 4 * ceil(2.5 s) + 10 s.
    probe = ("import json, resource as r\n"
             "print(json.dumps([r.getrlimit(k) for k in (r.RLIMIT_AS, r.RLIMIT_FSIZE, r.RLIMIT_CPU)]))")
    limits = real_popen([sys.executable, "-c", probe], preexec_fn=call["preexec_fn"], stdout=subprocess.PIPE)
    assert json.loads(limits.communicate()[0]) == [[4 * 2**30] * 2, [200 * 2**20] * 2, [22] * 2]

    # Exitstatus eines Workers ohne Bericht: SIGKILL oder SIGXCPU bedeutet CPU-Zeitlimit, jeder andere Status einen Absturz.
    for signal_exit in (-9, -24):
        monkeypatch.setattr(subprocess, "Popen", fake_popen(signal_exit))
        assert run_worker(payload(box()), timeout=2) == failed("Zeitüberschreitung nach 2s (CPU-Limit)")
    for code, stdout in ((1, b""), (0, b"not json")):  # die letzten 500 Zeichen von stderr, einschließlich nicht dekodierbarer Bytes
        monkeypatch.setattr(subprocess, "Popen", fake_popen(code, stdout, stderr=b"x" * 600 + b"\xff" + b"boom"))
        assert run_worker(payload(box())) == failed("Worker abgestürzt: " + "x" * 495 + "\ufffd" + "boom")
    finished = json.dumps(failed("BuildError: kept")).encode() + b"\n"
    monkeypatch.setattr(subprocess, "Popen", fake_popen(-11, finished))  # Ein Segfault beim Beenden macht den Bericht nicht rückgängig.
    assert run_worker(payload(box())) == failed("BuildError: kept")


def test_script_import_os_rejected(monkeypatch):
    for code in ("import os", "from os import path", "import math", "def f():\n    import cadquery"):
        with pytest.raises(ValueError, match="Importanweisungen sind in Skripten nicht zulässig"):
            check_script(code)

    # `run_worker` meldet die Ablehnung, ohne einen Prozess zu starten.
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("the worker was started"))
    report = run_worker(payload(box(), script("x = 1"), script("import os")))
    assert report == failed(
        "ValueError: op 2 (script): Importanweisungen sind in Skripten nicht zulässig (cq und math sind bereits verfügbar).")


def test_script_open_rejected():
    for name in FORBIDDEN_NAMES.split():
        for code in (f"{name}('x')", f"f = {name}"):  # aufgerufen oder nur referenziert
            with pytest.raises(ValueError, match=name):
                check_script(code)
    # Es werden nur vollständige Namen geprüft; ein Attribut mit demselben Namen wie eine eingebaute Funktion stellt keine Gefahr dar.
    check_script("opening = max(1, 2)\nevaluated = [input_ for input_ in range(3)]\nresult.open\nresult.vars()")


def test_script_dunder_rejected():
    for code in (
        "result.__class__",
        "x = result.__class__.__bases__",
        "_hidden = 1",
        "result._private",
        "f = lambda: __builtins__",
        "print(f'{result.__dict__}')",
        # Ein Schlüsselwort in einem Klassenmuster greift ebenfalls auf ein Attribut zu.
        "match result:\n    case cq.Workplane(__class__=c):\n        pass",
    ):
        with pytest.raises(ValueError, match="Unterstrich"):
            check_script(code)

    # Benötigte Modellskripte werden zugelassen; nicht kompilierbarer Code passiert die Prüfung ebenfalls, damit der Worker den Fehler melden kann.
    check_script("result = result.faces('>Z').workplane().hole(math.sqrt(16))\nprint(len([i for i in range(3)]))")
    check_script("def (")


def test_export_roundtrip_via_worker(tmp_path, monkeypatch):
    path = tmp_path / "plate.step"
    marks = [{"id": "rv1", "near": [[0, 0, 5.2]]}]  # 0,2 mm über der Oberseite
    report = run_worker(payload(box(), export_path=str(path), marks=marks))
    assert report["ok"] is True
    roundtrip = report["roundtrip"]
    assert (roundtrip["ok"], roundtrip["path"]) == (True, str(path))
    assert roundtrip["marks"] == [{"id": "rv1", "faces": 1}]
    back = cq.importers.importStep(str(path)).val()
    assert back.isValid() and back.Volume() == pytest.approx(1000)
    assert read_pmi(str(path))["schema"] == "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"
    assert roundtrip["pmi"] == {"written": 0, "read_back": 0}  # no spec in the payload, so no dimensions to carry

    # Ohne `export_path` exportiert und importiert der Worker weiterhin erneut: nach `model.step` im eigenen Arbeitsverzeichnis.
    monkeypatch.chdir(tmp_path)
    scratch = run_worker(payload(box()))
    assert (scratch["roundtrip"]["ok"], scratch["roundtrip"]["path"]) == (True, "model.step")
    assert not (tmp_path / "model.step").exists()

    # Für `ok` muss auch der Rückimport bestehen: Eine Markierung ohne passende Fläche lässt die Prüfung fehlschlagen, obwohl der Körper gültig ist.
    lost_mark = run_worker(payload(box(), marks=[{"id": "far", "near": [[100, 0, 0]]}]))
    assert (lost_mark["solid"]["ok"], lost_mark["roundtrip"]["ok"], lost_mark["ok"]) == (True, False, False)
    assert lost_mark["error"] is None


def pmi_spec() -> dict:
    """A spec for the 20 x 10 x 5 block with one hole: a diameter with a fit and an assumed thickness."""
    return {
        "name": "plate", "drawing": "Demo/plate.pdf", "title": "Plate", "scale": "1:1", "views": [], "features": [],
        "dimensions": [
            {"id": "d4", "kind": "diameter", "value": 4, "text": "Ø4 H7", "source": None, "confidence": 0.95,
             "fit": "H7"},
            {"id": "t", "kind": "length", "value": 5, "text": "", "source": None, "confidence": 0.6,
             "assumed": True, "assumption_basis": "side view"},
        ],
        "expected": {"bbox": [20, 10, 5], "holes": [{"d": 4, "at": [5, 2, 0], "axis": [0, 0, 1], "dim_ids": ["d4"]}]},
        "review": [{"id": "rv1", "refs": ["t"], "issue": "missing", "assumption": "thickness 5 mm",
                    "reason": "not dimensioned", "mark_near": [[0, 0, 0], [0, 0, 5]]}],
    }


def test_worker_writes_pmi_and_glb(tmp_path):
    spec = pmi_spec()
    hole = {"op": "holes", "plane": {"name": "XY", "offset": 5}, "positions": [[5, 2]], "d": 4}
    marks = [{"id": "rv1", "near": spec["review"][0]["mark_near"]}]
    step, glb = tmp_path / "plate.step", tmp_path / "plate.glb"
    report = run_worker(payload(
        box(), hole, expected=spec["expected"], marks=marks, export_path=str(step), pmi_spec=spec, glb_path=str(glb)))
    assert (report["ok"], report["roundtrip"]["pmi"]) == (True, {"written": 2, "read_back": 2})
    assert sorted((d["type"], d["value"], d["fit"]) for d in read_pmi(str(step))["dimensions"]) == [
        ("diameter", 4.0, "H7"), ("distance", 5.0, None)]
    assert glb.read_bytes()[:4] == b"glTF"
    assert json.loads((tmp_path / "marks.json").read_text()) == {"#ff8000": ["rv1"]}
    assert json.loads((tmp_path / "pmi.json").read_text()) == read_pmi(str(step))

    # without a spec there is no PMI, without a glb path no mesh and no JSON files; the colors are written all the same
    plain = tmp_path / "plain"
    plain.mkdir()
    again = run_worker(payload(box(), hole, marks=marks, export_path=str(plain / "plate.step")))
    assert again["roundtrip"]["pmi"] == {"written": 0, "read_back": 0}
    assert read_pmi(str(plain / "plate.step"))["colors"] == {"#99abb0": 1, "#ff8000": 2}
    assert [p.name for p in plain.iterdir()] == ["plate.step"]


def test_worker_marks_tapped_holes_green(tmp_path):
    tapped = {"op": "holes", "plane": {"name": "XY", "offset": 5}, "positions": [[5, 2]], "d": 5, "kind": "tapped",
              "thread": "M5"}
    path, glb = tmp_path / "tapped.step", tmp_path / "tapped.glb"
    assert run_worker(payload(box(), tapped, export_path=str(path), glb_path=str(glb)))["ok"] is True
    assert read_pmi(str(path))["colors"] == {"#99abb0": 1, "#65f073": 1}  # the tap drill of M5 is O4.2
    blob = glb.read_bytes()
    assert len(json.loads(blob[20:20 + struct.unpack_from("<I", blob, 12)[0]])["materials"]) == 2  # steel, green
    (thread,) = worker.thread_holes(worker.OPS.validate_python([box(), tapped]))
    assert thread["at"] == pytest.approx((5, 2, 5)) and thread["axis"] == pytest.approx((0, 0, -1))
    assert thread["d"] == 4.2


def test_worker_tolerance_widens_the_dimension_checks():
    """A build moved to the tolerance mid is not 20.0 wide any more: the caller says how far it may be off."""
    mid = box(w=19.9)
    narrow = run_worker(payload(mid, expected={"bbox": [20, 10, 5]}))
    assert ([d["ok"] for d in narrow["dims"]], narrow["ok"]) == ([False, True, True], False)
    assert run_worker(payload(mid, expected={"bbox": [20, 10, 5]}, tol=0.2))["ok"] is True

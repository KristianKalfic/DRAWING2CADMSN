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
from helpers import OPS

from d2c.cad import sandbox, worker
from d2c.cad.export import read_pmi
from d2c.cad.sandbox import check_script, run_worker

KEYS = {"ok", "error", "solid", "dims", "dims_found", "build_notes", "roundtrip"}  # eines erfolgreichen Aufbaus
FORBIDDEN_NAMES = (
    "open exec eval compile __import__ globals locals vars getattr setattr delattr input breakpoint "
    "os sys subprocess builtins tempfile io exporters importers occ_impl"
)
# Angriffe aus dem Sicherheitsaudit (security-1) und verwandte Wege; keiner darf den Worker erreichen.
ATTACKS = [
    "cq.exporters.os.system('id')",
    "cq.occ_impl.exporters.os.system('id')",
    "cq.exporters.export(result, '/tmp/pwn.step')",
    "result.val().exportStep('/tmp/pwn.step')",
    "cq.Shape.importBrep('/etc/passwd')",
    "getattr(result, 'val')()",
    "x = getattr",
    "__import__('os').system('id')",
    "result.__class__.__bases__[0].__subclasses__()",
    "[c for c in ().__class__.__base__.__subclasses__()]",
    "g = (i for i in [1])\nf = g.gi_frame.f_back.f_globals",
    "'{0.__class__}'.format(result)",
    "cq.Workplane.mro()",
    "tempfile.mkdtemp()",
    "io.open('/etc/passwd')",
    "builtins.open",
    "result.text('a', 5, 1, fontPath='/etc/passwd')",  # Leseorakel über die Schriftdatei
    "cq.Compound.makeText('a', 5, 1, fontPath='/etc/passwd')",
    "result.text('a', 5, 1, 'cut', True, 'Arial', '/etc/passwd')",
    "cq.Compound.makeText('a', 5, 1, 'Arial', '/etc/passwd')",
    "result.text('a', 5, 1, **{'font' + 'Path': '/etc/passwd'})",
    "result.text(*['a', 5, 1, 'cut', True, 'Arial', '/etc/passwd'])",
    "t = result.text\nt('a', 5, 1, 'cut', True, 'Arial', '/etc/passwd')",
    "result.val().exportSvg(fileName='/tmp/x.svg')",
    "result.importDXF(filename='/etc/passwd')",
]


def box(w=20.0, h=10.0, t=5.0, center=(0, 0), mode="add"):
    """Extrude-Operation: Quader w × h auf XY von z=0 bis z=t."""
    return {
        "op": "extrude",
        "profile": {"type": "rect", "center": center, "w": w, "h": h},
        "plane": {"name": "XY"},
        "distance": t,
        "mode": mode,
    }


def script(code):
    return {"op": "script", "code": code}


def payload(*ops, **changes):
    return {"ops": list(ops), "expected": None, "marks": [], "export_path": None} | changes


def failed(error):
    """Bericht eines Laufs ohne erzeugtes Modell: Nur `error` ist gesetzt."""
    return {"ok": False, "error": error, "solid": None, "dims": [], "build_notes": [], "roundtrip": None, "dims_found": None}


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
    # Das Skript selbst kann keine Dateien lesen; die OpenCascade-Meldung entsteht daher in einem vorgeschalteten Aufruf.
    noisy = "print('Hallo')"
    request = json.dumps(payload(box(), script(noisy))).encode()
    wrapper = (
        "import d2c.cad.worker as w\nfrom OCP.STEPControl import STEPControl_Reader\nmake = w.make_report\n"
        "def noisy(p):\n    STEPControl_Reader().ReadFile('bad.step')\n    return make(p)\nw.make_report = noisy\nw.main()"
    )
    raw = subprocess.run([sys.executable, "-I", "-c", wrapper], input=request, capture_output=True, cwd=tmp_path)
    assert len(raw.stdout.splitlines()) == 1
    assert json.loads(raw.stdout)["ok"] is True
    assert b"Hallo" in raw.stderr and b"StepFile" in raw.stderr


def test_worker_reports_build_error(monkeypatch):
    assert run_worker(payload(box(mode="cut"))) == failed("BuildError: op 0 (extrude): The first operation must have mode 'add'.")

    # Auch jede andere Fehlerursache muss als Bericht erscheinen und darf nie zum Absturz führen.
    bad_ops = run_worker(payload({"op": "extrude", "distance": 0}, script(123)))
    assert bad_ops == failed(bad_ops["error"]) and bad_ops["error"].startswith("ValidationError: ")
    assert "1.script.code" in bad_ops["error"]  # Ein Skript mit falschem Typ muss der Worker melden, nicht die Prüfung.
    assert "must be text" in bad_ops["error"]
    assert "Input should be a valid string" not in bad_ops["error"]
    bad_expected = run_worker(payload(box(), expected={}))
    assert bad_expected == failed(bad_expected["error"]) and bad_expected["error"].startswith("ValidationError: ")
    assert "bbox: is missing" in bad_expected["error"]
    bad_mode = run_worker(payload(box(mode="ungültig")))
    assert "'add'" in bad_mode["error"] and "literal_error" in bad_mode["error"]
    assert " or " not in bad_mode["error"]
    syntax = run_worker(payload(box(), script("def (")))
    assert syntax == failed(syntax["error"]) and syntax["error"].startswith("BuildError: op 1 (script): SyntaxError")
    empty = run_worker(payload(box(), box(mode="cut")))  # der Schnitt entfernt den ganzen Block
    assert empty == failed("BuildError: The model is empty. Check whether a cut removed all material.")
    assert run_worker(payload()) == failed("BuildError: No operations given.")

    # Dasselbe gilt für Ausnahmen aus der Prüfung oder dem Export, unabhängig vom Typ.
    def explode(*args):
        raise RuntimeError("Export fehlgeschlagen")

    monkeypatch.setattr(worker, "step_roundtrip", explode)
    assert worker.make_report(payload(box())) == failed("RuntimeError: Export fehlgeschlagen")


def test_timeout_kills(monkeypatch):
    start = time.monotonic()
    assert run_worker(payload(box(), script("while True: pass")), timeout=2) == failed("Timeout after 2s")
    assert time.monotonic() - start < 4

    # Eine Druckschleife füllt stderr. Die Ausgabe landet in einer Datei statt im Arbeitsspeicher des Elternprozesses; bei Zeitüberschreitung wird die ganze Prozessgruppe beendet.
    real_popen, started = subprocess.Popen, []

    def recording_popen(*args, **kwargs):
        started.append((real_popen(*args, **kwargs), kwargs))
        return started[-1][0]

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    chatter = "while True:\n    print('x' * 1000)\n    sum(range(10**5))"  # Ohne Begrenzung erreicht die Ausgabe zuerst die Dateigrenze von 200 MiB.
    flood = run_worker(payload(box(), script(chatter)), timeout=2)
    assert flood == failed("Timeout after 2s")
    process, kwargs = started[0]
    assert kwargs["stdout"] == subprocess.PIPE and kwargs["stderr"] not in (subprocess.PIPE, subprocess.STDOUT, None)
    assert kwargs["start_new_session"] is True
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)

    # Der Aufruf nutzt einen isolierten Interpreter, enthält nur `PATH` und `HOME` (= Arbeitsordner) in der Umgebung und verwendet ein anschließend gelöschtes Arbeitsverzeichnis.
    spy = fake_popen(0, stdout=json.dumps(failed("seen")).encode())
    monkeypatch.setattr(subprocess, "Popen", spy)
    monkeypatch.setenv("D2C_TIMEOUT", "2.5")  # Das Standardzeitlimit wird bei jedem Aufruf neu eingelesen.
    run_worker(payload(box()))
    call = spy.call
    assert call["args"] == [sys.executable, "-I", "-m", "d2c.cad.worker"]
    assert call["env"] == {"PATH": os.environ["PATH"], "HOME": call["cwd"]}
    assert call["timeout"] == 2.5
    assert Path(call["cwd"]) != Path.cwd() and not Path(call["cwd"]).exists()
    monkeypatch.delenv("D2C_TIMEOUT")
    run_worker(payload(box()))
    assert spy.call["timeout"] == 60

    # Grenzen im Kindprozess: 200 MiB Dateigröße und CPU-Zeit 4 * ceil(2.5 s) + 10 s; dazu 4 GiB Adressraum, wo das System sie setzen kann.
    probe = "import json, resource as r\nprint(json.dumps([r.getrlimit(k) for k in (r.RLIMIT_AS, r.RLIMIT_FSIZE, r.RLIMIT_CPU)]))"
    limits = real_popen([sys.executable, "-c", probe], preexec_fn=call["preexec_fn"], stdout=subprocess.PIPE)
    (as_soft, as_hard), fsize, cpu = json.loads(limits.communicate()[0])
    assert (fsize, cpu) == ([200 * 2**20] * 2, [22] * 2)
    assert sys.platform == "darwin" or (as_soft, as_hard) == (4 * 2**30, 4 * 2**30)

    # Exitstatus eines Workers ohne Bericht: SIGKILL oder SIGXCPU bedeutet CPU-Zeitlimit, jeder andere Status einen Absturz.
    for signal_exit in (-9, -24):
        monkeypatch.setattr(subprocess, "Popen", fake_popen(signal_exit))
        assert run_worker(payload(box()), timeout=2) == failed("Timeout after 2s (CPU limit)")
    for code, stdout in ((1, b""), (0, b"not json")):  # die letzten 500 Zeichen von stderr, einschließlich nicht dekodierbarer Bytes
        monkeypatch.setattr(subprocess, "Popen", fake_popen(code, stdout, stderr=b"x" * 600 + b"\xff" + b"boom"))
        assert run_worker(payload(box())) == failed("Worker crashed: " + "x" * 495 + "\ufffd" + "boom")
    finished = json.dumps(failed("BuildError: kept")).encode() + b"\n"
    monkeypatch.setattr(subprocess, "Popen", fake_popen(-11, finished))  # Ein Segfault beim Beenden macht den Bericht nicht rückgängig.
    assert run_worker(payload(box())) == failed("BuildError: kept")


# macOS lässt RLIMIT_AS nicht setzen (siehe `_limits` in d2c/cad/sandbox.py); die Grenze greift im Docker/Linux-Betrieb.
@pytest.mark.skipif(sys.platform == "darwin", reason="macOS kann die Adressraumgrenze nicht setzen")
def test_memory_limit_catches_greedy_allocation():
    # Die Adressraumgrenze fängt eine ausufernde Speicherbelegung (4 GiB Listenplätze) als Fehler ab.
    greedy = run_worker(payload(box(), script("slots = [0] * 2**29")))
    assert greedy == failed(greedy["error"]) and greedy["error"].startswith("BuildError: op 1 (script): MemoryError")


def refuse_limit(*args):
    raise OSError("setrlimit abgelehnt")


def test_limits_reraises_setrlimit_error_on_linux(monkeypatch):
    # Auf Linux darf der Worker nie ohne Grenze laufen: Der Fehler von setrlimit wird weitergereicht.
    monkeypatch.setattr(sandbox.resource, "setrlimit", refuse_limit)
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    with pytest.raises(OSError, match="setrlimit abgelehnt"):
        sandbox._limits(10)


def test_limits_skips_setrlimit_error_on_macos(monkeypatch):
    # macOS lehnt RLIMIT_AS in dieser Größe ab; dort wird die Grenze übersprungen.
    calls = []
    monkeypatch.setattr(sandbox.resource, "setrlimit", lambda *args: (calls.append(args), refuse_limit())[1])
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    sandbox._limits(10)
    assert len(calls) == 3  # alle drei Grenzen wurden versucht


def test_script_import_os_rejected(monkeypatch):
    for code in ("import os", "from os import path", "import math", "def f():\n    import cadquery"):
        with pytest.raises(ValueError, match="Import statements are not allowed in scripts"):
            check_script(code)

    # `run_worker` meldet die Ablehnung, ohne einen Prozess zu starten.
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("the worker was started"))
    report = run_worker(payload(box(), script("x = 1"), script("import os")))
    assert report == failed("ValueError: op 2 (script): Import statements are not allowed in scripts (cq and math are already available).")


def test_script_open_rejected():
    for name in FORBIDDEN_NAMES.split():
        for code in (f"{name}('x')", f"f = {name}"):  # aufgerufen oder nur referenziert
            with pytest.raises(ValueError, match=name):
                check_script(code)
    # Es werden nur vollständige Namen geprüft; ähnliche Namen und eigene Variablen bleiben möglich.
    check_script("opening = max(1, 2)\nevaluated = [input_ for input_ in range(3)]\nimportant = exported = 1")
    check_script("x = result" + ".translate((0, 0, 0))" * 20000)  # zu tief für ast.parse: meldet der Worker, nicht die Vorprüfung
    for code in ("result.open", "result.vars()", "cq.exporters", "result.system"):  # auch als Attribut
        with pytest.raises(ValueError, match="not allowed"):
            check_script(code)


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
        with pytest.raises(ValueError, match="underscore"):
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
    assert roundtrip["pmi"] == {"written": 0, "read_back": 0}  # keine Spezifikation in der Nutzlast, also keine Maße zu übertragen

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
    """Eine Spezifikation für den 20 x 10 x 5 Block mit einer Bohrung: ein Durchmesser mit Passung und eine angenommene Stärke."""
    return {
        "name": "plate",
        "drawing": "Demo/plate.pdf",
        "title": "Plate",
        "scale": "1:1",
        "views": [],
        "features": [],
        "dimensions": [
            {"id": "d4", "kind": "diameter", "value": 4, "text": "Ø4 H7", "source": None, "confidence": 0.95, "fit": "H7"},
            {
                "id": "t",
                "kind": "length",
                "value": 5,
                "text": "",
                "source": None,
                "confidence": 0.6,
                "assumed": True,
                "assumption_basis": "side view",
            },
        ],
        "expected": {"bbox": [20, 10, 5], "holes": [{"d": 4, "at": [5, 2, 0], "axis": [0, 0, 1], "dim_ids": ["d4"]}]},
        "review": [
            {
                "id": "rv1",
                "refs": ["t"],
                "issue": "missing",
                "assumption": "thickness 5 mm",
                "reason": "not dimensioned",
                "mark_near": [[0, 0, 0], [0, 0, 5]],
            }
        ],
    }


def test_worker_writes_pmi_and_glb(tmp_path):
    spec = pmi_spec()
    hole = {"op": "holes", "plane": {"name": "XY", "offset": 5}, "positions": [[5, 2]], "d": 4}
    marks = [{"id": "rv1", "near": spec["review"][0]["mark_near"]}]
    step, glb = tmp_path / "plate.step", tmp_path / "plate.glb"
    report = run_worker(
        payload(box(), hole, expected=spec["expected"], marks=marks, export_path=str(step), pmi_spec=spec, glb_path=str(glb))
    )
    assert (report["ok"], report["roundtrip"]["pmi"]) == (True, {"written": 2, "read_back": 2})
    assert sorted((d["type"], d["value"], d["fit"]) for d in read_pmi(str(step))["dimensions"]) == [
        ("diameter", 4.0, "H7"),
        ("distance", 5.0, None),
    ]
    assert glb.read_bytes()[:4] == b"glTF"
    assert json.loads((tmp_path / "marks.json").read_text()) == {"#ff8000": ["rv1"]}
    assert json.loads((tmp_path / "pmi.json").read_text()) == read_pmi(str(step))

    # ohne Spezifikation kein PMI, ohne glb-Pfad kein Netz und keine JSON-Dateien; die Farben werden trotzdem geschrieben
    plain = tmp_path / "plain"
    plain.mkdir()
    again = run_worker(payload(box(), hole, marks=marks, export_path=str(plain / "plate.step")))
    assert again["roundtrip"]["pmi"] == {"written": 0, "read_back": 0}
    assert read_pmi(str(plain / "plate.step"))["colors"] == {"#99abb0": 1, "#ff8000": 2}
    assert [p.name for p in plain.iterdir()] == ["plate.step"]


def test_worker_marks_only_the_pmi_faces_of_an_assumed_dimension(tmp_path):
    """Ein Punkt auf der Ecke (10, 5, 5) träfe drei Flächen; die angenommene Stärke markiert nur ihre beiden Ebenen (z=5 und z=0)."""
    spec = pmi_spec() | {"expected": {"bbox": [20, 10, 5]}}
    spec["review"][0]["mark_near"] = [[10, 5, 5]]
    marks = [{"id": "rv1", "near": spec["review"][0]["mark_near"]}]
    step = tmp_path / "plate.step"
    report = run_worker(payload(box(), expected=spec["expected"], marks=marks, export_path=str(step), pmi_spec=spec))
    assert report["ok"] is True and report["roundtrip"]["marks"] == [{"id": "rv1", "faces": 2}]
    assert read_pmi(str(step))["colors"] == {"#99abb0": 1, "#ff8000": 2}
    unpinned = run_worker(
        payload(box(), marks=marks, export_path=str(tmp_path / "plain.step"))
    )  # ohne Spezifikation: die Punkte entscheiden
    assert unpinned["roundtrip"]["marks"] == [{"id": "rv1", "faces": 3}]


def test_worker_marks_tapped_holes_green(tmp_path):
    tapped = {"op": "holes", "plane": {"name": "XY", "offset": 5}, "positions": [[5, 2]], "d": 5, "kind": "tapped", "thread": "M5"}
    path, glb = tmp_path / "tapped.step", tmp_path / "tapped.glb"
    assert run_worker(payload(box(), tapped, export_path=str(path), glb_path=str(glb)))["ok"] is True
    assert read_pmi(str(path))["colors"] == {"#99abb0": 1, "#65f073": 1}  # die Kernlochbohrung von M5 ist Ø4,2
    blob = glb.read_bytes()
    assert len(json.loads(blob[20 : 20 + struct.unpack_from("<I", blob, 12)[0]])["materials"]) == 2  # steel, green
    (thread,) = worker.thread_holes(OPS.validate_python([box(), tapped]))
    assert thread["at"] == pytest.approx((5, 2, 5)) and thread["axis"] == pytest.approx((0, 0, -1))
    assert thread["d"] == pytest.approx(4.2, abs=1e-3)


def test_worker_tolerance_widens_the_dimension_checks():
    """Ein um die Toleranzmitte verschobener Bau ist nicht mehr 20,0 breit: der Aufrufer sagt, wie weit er abweichen darf."""
    mid = box(w=19.9)
    narrow = run_worker(payload(mid, expected={"bbox": [20, 10, 5]}))
    assert ([d["ok"] for d in narrow["dims"]], narrow["ok"]) == ([False, True, True], False)
    assert run_worker(payload(mid, expected={"bbox": [20, 10, 5]}, tol=0.2))["ok"] is True


@pytest.mark.parametrize(
    "code, name",
    [
        ('cq.exporters.os.system("id > /tmp/pwned")', "os"),  # der Ausbruch aus dem Prüfbericht
        ("cq.occ_impl.shapes.os.listdir('/')", "os"),
        ("cq.occ_impl.exporters.svg.sys.modules", "sys"),
        ("math.__loader__", "underscore"),
        ("().__class__", "underscore"),
        ("x = os", "os"),
        ("cq.exporters.subprocess.run(['id'])", "subprocess"),
        ("cq.exporters.io.open('/etc/passwd')", "io"),
        ("pathlib.Path('/etc/passwd')", "pathlib"),
        ("cq.occ_impl.shapes.shutil.rmtree('/x')", "shutil"),
        ("y = cq.exporters.importlib", "importlib"),
        ("cq.x.builtins", "builtins"),
        ("cq.x.ctypes", "ctypes"),
        ("cq.x.pickle.loads(b'')", "pickle"),
        ("tools.system('id')", "system"),
        ("tools.popen('id')", "popen"),
        ("tools.execv('/bin/sh', [])", "execv"),
        ("tools.spawnl(0, 'x')", "spawnl"),
        ("tools.fork()", "fork"),
        ("tools.remove('x')", "remove"),
        ("tools.unlink('x')", "unlink"),
        ("tools.rename('a', 'b')", "rename"),
        ("tools.open('x')", "open"),
        ("tools.loads(b'')", "loads"),
        ("tools.mro()", "mro"),
        ("tools.subclasses()", "subclasses"),
    ],
)
def test_script_os_access_rejected(code, name):
    with pytest.raises(ValueError, match=name) as caught:
        check_script(code)
    assert name in ("underscore", "mro", "subclasses", "loads") or "operating system functions" in str(caught.value)


def test_script_os_message_is_readable():
    with pytest.raises(ValueError) as caught:
        check_script('cq.exporters.os.system("id")')
    assert str(caught.value) == "Access to operating system functions is not allowed in run_script: `os`"


def test_run_worker_reports_os_access_without_starting(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("the worker was started"))
    report = run_worker(payload(box(), script('cq.exporters.os.system("id > /tmp/pwned")')))
    assert report == failed("ValueError: op 1 (script): Access to operating system functions is not allowed in run_script: `os`")


@pytest.mark.parametrize("code", ATTACKS)
def test_attacks_are_rejected_before_the_worker_starts(code, monkeypatch):
    with pytest.raises(ValueError, match="not allowed"):
        check_script(code)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("the worker was started"))
    assert run_worker(payload(script(code)))["error"].startswith("ValueError: op 0 (script)")


def test_text_without_font_file_stays_allowed():
    check_script("result = result.text('a', 5, 1, font='Arial', kind='bold')\nresult = cq.Workplane('XY').sweep(path=result)")


def test_script_namespace_has_no_cq_module():
    """Auch wenn die Vorprüfung umgangen würde: im Namensraum des Skripts fehlt das Modul cq samt Untermodulen."""
    from d2c.cad.ops import ScriptOp

    for code in (
        "result = cq.exporters",
        "result = cq.occ_impl",
        "result = cq.os",
        "result = os",
        "result = open('x')",
        "result = __import__('os')",
        "result = eval('1')",
    ):
        with pytest.raises((AttributeError, NameError)):
            ScriptOp(op="script", code=code).apply(None)


def test_script_namespace_keeps_cadquery_classes():
    from d2c.cad.ops import ScriptOp

    code = (
        "plate = cq.Workplane('XY').rect(10, 5).extrude(2)\n"
        "hole = Workplane('XY').circle(1).extrude(2)\n"
        "v = Vector(1, 0, 0)\n"
        "n = len(sorted([3, 1, 2]))\n"
        "result = plate.cut(hole) if isinstance(v, Vector) and n == 3 else plate"
    )
    shape, _ = ScriptOp(op="script", code=code).apply(None)
    assert abs(shape.Volume() - (100 - math.pi * 2)) < 1e-6


def test_script_examples_from_real_runs_still_build():
    """Die Skripte der Läufe (nur CadQuery-Aufrufe, math, Schleifen): hier in der Form ohne Teilenamen."""
    codes = [
        "pts = [(0,17),(164,17),(164,0),(358,0),(358,140),(164,140),(164,170),(0,170)]\n"
        "result = cq.Workplane('XY').workplane(offset=4.5).polyline(pts).close().extrude(11.5).edges('|Z').fillet(6)\n"
        "for (x, yo, ye) in [(30, 170, 147), (220, 140, 117)]:\n"
        "    d = 1 if ye > yo else -1\n"
        "    result = result.cut(cq.Workplane('XY').workplane(offset=4.5).center(x, ye - d*8.5).circle(8.5).extrude(11.5))\n",
        "for a in (180, 60, 300):\n"
        "    c = math.cos(math.radians(a)); s = math.sin(math.radians(a))\n"
        "    arm = cq.Workplane('XY').moveTo(50*c, 50*s).circle(6).extrude(22)\n"
        "    result = result.union(arm)\n"
        "ang = math.asin((15-6)/50.0)\n"
        "def rot(x, y):\n    return (x*math.cos(ang) - y*math.sin(ang), x*math.sin(ang) + y*math.cos(ang))\n"
        "sel = [e for e in result.edges('|Z').vals() if 12 < math.hypot(e.Center().x, e.Center().y) < 60]\n"
        "print(len(sel), [(round(e.Center().x, 1), round(e.Center().y, 1)) for e in sel], rot(1, 2))\n"
        "result = result.newObject(sel).fillet(2)\n",
        "plate = cq.Workplane('XY').circle(40).extrude(3)\n"
        "outer = cq.Workplane('XY').circle(15).extrude(50).rotate((0,0,0),(0,1,0),45)\n"
        "low = cq.Workplane('XY').box(200,200,50,centered=(True,True,False)).translate((0,0,-50))\n"
        "result = plate.union(outer.cut(low))\n",
    ]
    for code in codes:
        check_script(code)
    report = run_worker(payload(*[script(c) for c in codes[:1]]))
    assert report["error"] is None and report["solid"]["ok"], report
    report = run_worker(payload(box(40, 40, 22), script(codes[1])))
    assert report["error"] is None, report
    report = run_worker(payload(script(codes[2])))
    assert report["error"] is None and report["solid"]["ok"], report

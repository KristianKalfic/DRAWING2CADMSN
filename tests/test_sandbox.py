"""Isolated build worker (process boundary, report protocol) and the AST check for scripts."""
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import cadquery as cq
import pytest

from d2c import worker
from d2c.sandbox import check_script, run_worker

KEYS = {"ok", "error", "solid", "dims", "build_notes", "roundtrip"}
FORBIDDEN_NAMES = "open exec eval compile __import__ globals locals vars getattr setattr delattr input breakpoint"


def box(w=20.0, h=10.0, t=5.0, center=(0, 0), mode="add"):
    """Extrude op: a w x h block on XY from z=0 to z=t."""
    return {"op": "extrude", "profile": {"type": "rect", "center": center, "w": w, "h": h},
            "plane": {"name": "XY"}, "distance": t, "mode": mode}


def script(code):
    return {"op": "script", "code": code}


def payload(*ops, **changes):
    return {"ops": list(ops), "expected": None, "marks": [], "export_path": None} | changes


def failed(error):
    """The report of a run that made no model: only `error` is set."""
    return {"ok": False, "error": error, "solid": None, "dims": [], "build_notes": [], "roundtrip": None}


def fake_popen(returncode, stdout=b"", stderr=b""):
    """Stand-in for subprocess.Popen: finishes at once with these results and keeps the call it got."""
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

    # a failed check is a result, not an error: ok needs the solid check and every dimension to pass
    wrong_size = run_worker(payload(box(), expected={"bbox": [20, 10, 6]}))
    assert [d["ok"] for d in wrong_size["dims"]] == [True, True, False]
    flip = "result = cq.Workplane().add(cq.Shape.cast(result.val().Solids()[0].wrapped.Reversed()))"
    inside_out = run_worker(payload(box(), script(flip)))  # negative volume
    assert inside_out["solid"]["ok"] is False
    for report in (wrong_size, inside_out):
        assert (report["ok"], report["error"]) == (False, None)

    # print() in a script and OpenCascade's own messages must not break the protocol:
    # stdout holds exactly one JSON line, everything else goes to stderr
    (tmp_path / "bad.step").write_text("not a STEP file")
    noisy = "print('hello')\ntry:\n    cq.importers.importStep('bad.step')\nexcept:\n    pass"
    request = json.dumps(payload(box(), script(noisy))).encode()
    raw = subprocess.run([sys.executable, "-I", "-m", "d2c.worker"], input=request, capture_output=True, cwd=tmp_path)
    assert len(raw.stdout.splitlines()) == 1
    assert json.loads(raw.stdout)["ok"] is True
    assert b"hello" in raw.stderr and b"StepFile" in raw.stderr


def test_worker_reports_build_error(monkeypatch):
    assert run_worker(payload(box(mode="cut"))) == failed(
        "BuildError: op 0 (extrude): the first operation must have mode 'add'")

    # every other way to fail is a report as well, never a crash
    bad_ops = run_worker(payload({"op": "extrude", "distance": 0}, script(123)))
    assert bad_ops == failed(bad_ops["error"]) and bad_ops["error"].startswith("ValidationError: ")
    assert "1.script.code" in bad_ops["error"]  # a script of the wrong type is the worker's to report, not the check's
    bad_expected = run_worker(payload(box(), expected={}))
    assert bad_expected == failed(bad_expected["error"]) and bad_expected["error"].startswith("ValidationError: ")
    syntax = run_worker(payload(box(), script("def (")))
    assert syntax == failed(syntax["error"]) and syntax["error"].startswith("BuildError: op 1 (script): SyntaxError")
    empty = run_worker(payload(box(), box(mode="cut")))  # the cut removes the whole block
    assert empty == failed("BuildError: the model is empty (did a cut remove everything?)")
    assert run_worker(payload()) == failed("BuildError: no operations")

    # so is an exception from the checks or the export, whatever its type
    def explode(*args):
        raise RuntimeError("export exploded")

    monkeypatch.setattr(worker, "step_roundtrip", explode)
    assert worker.make_report(payload(box())) == failed("RuntimeError: export exploded")


def test_timeout_kills(monkeypatch):
    start = time.monotonic()
    assert run_worker(payload(box(), script("while True: pass")), timeout=2) == failed("timeout after 2s")
    assert time.monotonic() - start < 4

    # a print loop floods stderr: it goes to a file, not into the parent's memory, and the whole group dies at the timeout
    real_popen, started = subprocess.Popen, []

    def recording_popen(*args, **kwargs):
        started.append((real_popen(*args, **kwargs), kwargs))
        return started[-1][0]

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    chatter = "while True:\n    print('x' * 1000)\n    sum(range(10**5))"  # unthrottled it hits the 200 MiB file limit first
    flood = run_worker(payload(box(), script(chatter)), timeout=2)
    assert flood == failed("timeout after 2s")
    process, kwargs = started[0]
    assert kwargs["stdout"] == subprocess.PIPE and kwargs["stderr"] not in (subprocess.PIPE, subprocess.STDOUT, None)
    assert kwargs["start_new_session"] is True
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)
    # the address space limit turns a runaway allocation (4 GiB of list slots) into an error
    greedy = run_worker(payload(box(), script("slots = [0] * 2**29")))
    assert greedy == failed(greedy["error"]) and greedy["error"].startswith("BuildError: op 1 (script): MemoryError")

    # the call: isolated interpreter, only PATH in the environment, a scratch directory that is removed again
    spy = fake_popen(0, stdout=json.dumps(failed("seen")).encode())
    monkeypatch.setattr(subprocess, "Popen", spy)
    monkeypatch.setenv("D2C_TIMEOUT", "2.5")  # the default timeout is read on every call
    run_worker(payload(box()))
    call = spy.call
    assert call["args"] == [sys.executable, "-I", "-m", "d2c.worker"]
    assert call["env"] == {"PATH": os.environ["PATH"]}
    assert call["timeout"] == 2.5
    assert Path(call["cwd"]) != Path.cwd() and not Path(call["cwd"]).exists()
    monkeypatch.delenv("D2C_TIMEOUT")
    run_worker(payload(box()))
    assert spy.call["timeout"] == 60

    # limits as set in the child: address space 4 GiB, file size 200 MiB, CPU 4 * ceil(2.5 s) + 10 s
    probe = ("import json, resource as r\n"
             "print(json.dumps([r.getrlimit(k) for k in (r.RLIMIT_AS, r.RLIMIT_FSIZE, r.RLIMIT_CPU)]))")
    limits = real_popen([sys.executable, "-c", probe], preexec_fn=call["preexec_fn"], stdout=subprocess.PIPE)
    assert json.loads(limits.communicate()[0]) == [[4 * 2**30] * 2, [200 * 2**20] * 2, [22] * 2]

    # exit status of a worker that left no report: SIGKILL or SIGXCPU is the CPU limit, anything else a crash
    for signal_exit in (-9, -24):
        monkeypatch.setattr(subprocess, "Popen", fake_popen(signal_exit))
        assert run_worker(payload(box()), timeout=2) == failed("timeout after 2s (cpu limit)")
    for code, stdout in ((1, b""), (0, b"not json")):  # the last 500 characters of stderr, undecodable bytes included
        monkeypatch.setattr(subprocess, "Popen", fake_popen(code, stdout, stderr=b"x" * 600 + b"\xff" + b"boom"))
        assert run_worker(payload(box())) == failed("worker crashed: " + "x" * 495 + "\ufffd" + "boom")
    finished = json.dumps(failed("BuildError: kept")).encode() + b"\n"
    monkeypatch.setattr(subprocess, "Popen", fake_popen(-11, finished))  # a segfault at exit does not undo the report
    assert run_worker(payload(box())) == failed("BuildError: kept")


def test_script_import_os_rejected(monkeypatch):
    for code in ("import os", "from os import path", "import math", "def f():\n    import cadquery"):
        with pytest.raises(ValueError, match="imports are not allowed"):
            check_script(code)

    # run_worker reports the rejection and never starts a process
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("the worker was started"))
    report = run_worker(payload(box(), script("x = 1"), script("import os")))
    assert report == failed(
        "ValueError: op 2 (script): imports are not allowed in scripts (cq and math are already available)")


def test_script_open_rejected():
    for name in FORBIDDEN_NAMES.split():
        for code in (f"{name}('x')", f"f = {name}"):  # called, or only referenced
            with pytest.raises(ValueError, match=name):
                check_script(code)
    # whole names only, and only names: an attribute called like a builtin is no threat
    check_script("opening = max(1, 2)\nevaluated = [input_ for input_ in range(3)]\nresult.open\nresult.vars()")


def test_script_dunder_rejected():
    for code in (
        "result.__class__",
        "x = result.__class__.__bases__",
        "_hidden = 1",
        "result._private",
        "f = lambda: __builtins__",
        "print(f'{result.__dict__}')",
        # a keyword in a class pattern looks up an attribute as well
        "match result:\n    case cq.Workplane(__class__=c):\n        pass",
    ):
        with pytest.raises(ValueError, match="underscore"):
            check_script(code)

    # what a model script needs passes, and so does code that cannot compile (the worker reports that)
    check_script("result = result.faces('>Z').workplane().hole(math.sqrt(16))\nprint(len([i for i in range(3)]))")
    check_script("def (")


def test_export_roundtrip_via_worker(tmp_path, monkeypatch):
    path = tmp_path / "plate.step"
    marks = [{"id": "rv1", "near": [[0, 0, 5.2]]}]  # 0.2 mm over the top face
    report = run_worker(payload(box(), export_path=str(path), marks=marks))
    assert report["ok"] is True
    roundtrip = report["roundtrip"]
    assert (roundtrip["ok"], roundtrip["path"]) == (True, str(path))
    assert roundtrip["marks"] == [{"id": "rv1", "faces": 1}]
    back = cq.importers.importStep(str(path)).val()
    assert back.isValid() and back.Volume() == pytest.approx(1000)

    # without export_path the worker still exports and re-imports: into model.step in its own scratch directory
    monkeypatch.chdir(tmp_path)
    scratch = run_worker(payload(box()))
    assert (scratch["roundtrip"]["ok"], scratch["roundtrip"]["path"]) == (True, "model.step")
    assert not (tmp_path / "model.step").exists()

    # ok needs the round trip too: a mark that finds no face fails it while the solid is fine
    lost_mark = run_worker(payload(box(), marks=[{"id": "far", "near": [[100, 0, 0]]}]))
    assert (lost_mark["solid"]["ok"], lost_mark["roundtrip"]["ok"], lost_mark["ok"]) == (True, False, False)
    assert lost_mark["error"] is None

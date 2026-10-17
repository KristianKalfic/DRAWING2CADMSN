"""Build worker: the process `python -I -m d2c.worker` that d2c.sandbox.run_worker starts.

stdin holds one JSON payload {"ops": [...], "expected": {...} | null, "marks": [...], "export_path": str | null}
and optionally "pmi_spec": the PartSpec as a dict (the STEP then carries its semantic dimensions), "glb_path":
str (when the checks pass, the GLB is written there, marks.json and pmi.json next to it) and "tol": the largest
deviation in mm that the dimension checks accept (default checks.LINEAR_TOL).
stdout gets exactly one JSON line, the report with the keys ok, error, solid, dims, build_notes, roundtrip.
"""
import json
import os
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from d2c.checks import LINEAR_TOL, dimension_check, solid_check, step_roundtrip
from d2c.export import pmi_from, read_pmi, write_glb
from d2c.ops import BuildError, HolesOp, Op, build
from d2c.sandbox import error_report, validation_error_message
from d2c.spec import Expected, PartSpec

OPS = TypeAdapter(list[Op])
SCRATCH_STEP = "model.step"  # where the model is exported when the payload names no path: the scratch cwd


def thread_holes(ops: list[Op]) -> list[dict]:
    """One entry per hole of a tapped op for write_step: a point on its axis, the drill direction, the drilled d."""
    found = []
    for op in ops:
        if isinstance(op, HolesOp) and op.kind == "tapped":
            plane = op.plane.workplane().plane
            drill = plane.zDir * (1 if op.flip else -1)
            found += [{"at": plane.toWorldCoords(c).toTuple(), "axis": drill.toTuple(), "d": op._drilled_d()}
                      for c in op._centers()]
    return found


def make_report(payload: dict) -> dict:
    """Build, check and export the model of the payload. Whatever fails on the way becomes `error`."""
    try:
        ops = OPS.validate_python(payload["ops"])
        want = payload.get("expected")
        expected = Expected.model_validate(want) if want is not None else None
        spec = PartSpec.model_validate(payload["pmi_spec"]) if payload.get("pmi_spec") else None
        tol, marks = payload.get("tol", LINEAR_TOL), payload.get("marks", [])
        wp, notes = build(ops)
        if not wp.objects:  # CadQuery keeps no object for an empty compound, and the checks cannot read that
            raise BuildError("Das Modell ist leer. Prüfe, ob ein Schnitt das gesamte Material entfernt hat.")
        solid = solid_check(wp)
        dims = dimension_check(wp, expected, tol) if expected is not None else []
        pmi = pmi_from(spec, ops, wp.val(), {"dims": dims}, tol) if spec is not None else []
        threads = thread_holes(ops)
        step = payload.get("export_path") or SCRATCH_STEP
        roundtrip = step_roundtrip(wp, step, marks, pmi, threads)
        ok = solid["ok"] and all(d["ok"] for d in dims) and roundtrip["ok"]
        if ok and payload.get("glb_path"):
            glb = Path(payload["glb_path"])
            write_glb(wp.val(), str(glb), marks, threads)  # writes marks.json next to the GLB
            glb.with_name("pmi.json").write_text(
                json.dumps(read_pmi(step), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except ValidationError as e:
        return error_report(f"ValidationError: {validation_error_message(e)}")
    except Exception as e:  # invalid input, build, check and export errors alike: the caller always gets a report
        return error_report(f"{type(e).__name__}: {e}")
    return {
        "ok": ok,
        "error": None,
        "solid": solid,
        "dims": dims,
        "build_notes": notes,
        "roundtrip": roundtrip,
    }


def main() -> None:
    """Read the payload from stdin and write the report as one JSON line to the real stdout."""
    with os.fdopen(os.dup(1), "w") as report_out:  # the real stdout, kept for the report
        os.dup2(2, 1)  # print() in scripts and OpenCascade's own messages now land on stderr
        report_out.write(json.dumps(make_report(json.load(sys.stdin))) + "\n")


if __name__ == "__main__":
    main()

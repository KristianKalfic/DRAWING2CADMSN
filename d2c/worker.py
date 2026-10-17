"""Build worker: the process `python -I -m d2c.worker` that d2c.sandbox.run_worker starts.

stdin holds one JSON payload {"ops": [...], "expected": {...} | null, "marks": [...], "export_path": str | null}.
stdout gets exactly one JSON line, the report with the keys ok, error, solid, dims, build_notes, roundtrip.
"""
import json
import os
import sys

from pydantic import TypeAdapter

from d2c.checks import dimension_check, solid_check, step_roundtrip
from d2c.ops import BuildError, Op, build
from d2c.sandbox import error_report
from d2c.spec import Expected

OPS = TypeAdapter(list[Op])
SCRATCH_STEP = "model.step"  # where the model is exported when the payload names no path: the scratch cwd


def make_report(payload: dict) -> dict:
    """Build, check and export the model of the payload. Whatever fails on the way becomes `error`."""
    try:
        ops = OPS.validate_python(payload["ops"])
        want = payload.get("expected")
        expected = Expected.model_validate(want) if want is not None else None
        wp, notes = build(ops)
        if not wp.objects:  # CadQuery keeps no object for an empty compound, and the checks cannot read that
            raise BuildError("the model is empty (did a cut remove everything?)")
        solid = solid_check(wp)
        dims = dimension_check(wp, expected) if expected is not None else []
        roundtrip = step_roundtrip(wp, payload.get("export_path") or SCRATCH_STEP, payload.get("marks", []))
    except Exception as e:  # invalid input, build, check and export errors alike: the caller always gets a report
        return error_report(f"{type(e).__name__}: {e}")
    return {
        "ok": solid["ok"] and all(d["ok"] for d in dims) and roundtrip["ok"],
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

"""Open an exported STEP file in FreeCAD and measure it: a check of the result that does not use CadQuery.

FreeCAD runs headless (freecadcmd) outside the container, it is not part of the image. This script needs
only the standard library, so any recent Python will do. FreeCAD also builds on OpenCASCADE: what is
independent is the STEP reader and the measuring code, not the geometry kernel.

Without --spec the solid is checked: valid, exactly one solid, positive volume. With --spec (the spec.json
that export_step writes) the size and every hole of its `expected` are checked as well, with the thresholds
of d2c/checks.py: 0.01 mm, axes parallel from |cos| 0.9999, holes are concave cylinders.

Usage: python scripts/freecad_check.py part.step [--spec spec.json] [--freecad PATH]

Exit code: 0 all checks passed, 1 a check failed or FreeCAD could not read the file, 2 FreeCAD not found
(argparse also uses 2 for a malformed command line), which lets CI tell a missing tool from a failed check.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

LINEAR_TOL = 0.01  # mm, as in d2c/checks.py
AXIS_COS = 0.9999  # smallest |cos| of the angle between two axes that count as parallel, as in d2c/checks.py
TIMEOUT = 120  # seconds for one FreeCAD run
FREECAD_PATHS = (  # where the headless binary usually is when it is not on PATH
    "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    "C:/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe",
)

# Runs inside FreeCAD: reads the STEP named in D2C_STEP, writes the measurement as JSON to D2C_FC_OUT.
# A cylinder is concave (a hole) if the outward normal of its face points at the axis.
MEASURE = r'''
import json, os
import FreeCAD, Part

shape = Part.read(os.environ["D2C_STEP"])
box = shape.optimalBoundingBox() if hasattr(shape, "optimalBoundingBox") else shape.BoundBox
cylinders = []
for face in shape.Faces:
    surface = face.Surface
    if surface.__class__.__name__ != "Cylinder":
        continue
    u0, u1, v0, v1 = face.ParameterRange
    u, v = (u0 + u1) / 2, (v0 + v1) / 2
    radial = face.valueAt(u, v) - surface.Center
    radial = radial - surface.Axis * radial.dot(surface.Axis)
    cylinders.append({"d": 2 * surface.Radius, "axis": list(surface.Axis), "point": list(surface.Center),
                      "concave": face.normalAt(u, v).dot(radial) < 0})
json.dump({"version": ".".join(FreeCAD.Version()[:3]), "valid": shape.isValid(), "solids": len(shape.Solids),
           "volume": shape.Volume, "bbox": [box.XLength, box.YLength, box.ZLength], "cylinders": cylinders},
          open(os.environ["D2C_FC_OUT"], "w"))
'''


def find_freecad(path: str | None) -> str | None:
    """The FreeCAD binary: the given path or name, else one on PATH, else a usual install location."""
    if path:
        return path if Path(path).is_file() else shutil.which(path)
    on_path = shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
    return on_path or next((p for p in FREECAD_PATHS if Path(p).is_file()), None)


def no_freecad(message: str) -> None:
    print(f"{message} Install FreeCAD or pass --freecad PATH to freecadcmd (FreeCADCmd.exe on Windows).",
          file=sys.stderr)
    sys.exit(2)


def measure(exe: str, step: Path) -> dict:
    """Let FreeCAD measure the STEP; exits with code 1 if it delivers nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        script, result = Path(tmp, "measure.py"), Path(tmp, "result.json")
        script.write_text(MEASURE, encoding="utf-8")
        env = dict(os.environ, D2C_STEP=str(step.resolve()), D2C_FC_OUT=str(result))
        try:
            done = subprocess.run([exe, str(script)], env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            sys.exit(f"FreeCAD did not finish within {TIMEOUT} s on {step}")
        except OSError as e:
            no_freecad(f"FreeCAD could not be started ({e}).")
        if not result.is_file():
            lines = re.sub(r"\x1b\[[0-9;]*m", "", done.stdout + done.stderr).splitlines()
            tail = "\n".join([line for line in lines if line.strip()][-5:])
            sys.exit(f"FreeCAD could not read {step} (exit {done.returncode}):\n{tail}")
        return json.loads(result.read_text(encoding="utf-8"))


def unit(v: list[float]) -> list[float]:
    length = math.sqrt(sum(c * c for c in v))
    return [c / length for c in v]


def xyz(v: list[float]) -> str:
    return "(" + ", ".join(f"{c:g}" for c in v) + ")"


def is_hole(cylinder: dict, hole: dict) -> bool:
    """A concave cylinder with the diameter of the hole, its axis through hole['at'] and parallel to it."""
    axis = unit(cylinder["axis"])
    rel = [p - q for p, q in zip(hole["at"], cylinder["point"])]
    along = sum(r * a for r, a in zip(rel, axis))
    distance = math.sqrt(sum((r - along * a) ** 2 for r, a in zip(rel, axis)))
    cos = abs(sum(a * b for a, b in zip(axis, unit(hole["axis"]))))
    return (cylinder["concave"] and abs(cylinder["d"] - hole["d"]) <= LINEAR_TOL and distance <= LINEAR_TOL
            and cos >= AXIS_COS)


def evaluate(measured: dict, expected: dict | None) -> list[tuple[str, bool]]:
    """One (description, ok) per check."""
    solid = (f"solid: valid={measured['valid']}, solids={measured['solids']}, volume={measured['volume']:.2f} mm3",
             measured["valid"] and measured["solids"] == 1 and measured["volume"] > 0)
    checks = [solid]
    if expected is not None:
        size = measured["bbox"]
        checks.append((f"bbox: expected {' x '.join(f'{v:g}' for v in expected['bbox'])}, "
                       f"FreeCAD {' x '.join(f'{v:.3f}' for v in size)}",
                       all(abs(want - got) <= LINEAR_TOL for want, got in zip(expected["bbox"], size))))
        concave = [c for c in measured["cylinders"] if c["concave"]]
        for hole in expected.get("holes", []):
            checks.append((f"hole d={hole['d']:g} at {xyz(hole['at'])} axis {xyz(hole['axis'])}",
                           any(is_hole(c, hole) for c in concave)))
    return checks


def load_expected(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))["expected"]
    except (OSError, ValueError, KeyError) as e:
        sys.exit(f"cannot read the expected values from {path}: {e!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", type=Path, help="the STEP file to check")
    parser.add_argument("--spec", help="spec.json whose expected size and holes are checked as well")
    parser.add_argument("--freecad", help="path to freecadcmd (FreeCADCmd.exe on Windows); default: PATH, "
                                          "then the usual install folders")
    args = parser.parse_args()
    exe = find_freecad(args.freecad)
    if exe is None:
        no_freecad(f"FreeCAD not found: {args.freecad}." if args.freecad else "FreeCAD not found.")
    if not args.step.is_file():
        sys.exit(f"STEP file not found: {args.step}")
    expected = load_expected(args.spec) if args.spec else None
    measured = measure(exe, args.step)

    diameters = Counter(round(c["d"], 3) for c in measured["cylinders"] if c["concave"])
    print(f"FreeCAD {measured['version']} read {args.step}")
    print(f"  concave cylinders: {sum(diameters.values())} "
          f"({', '.join(f'd={d:g}' + (f' x{n}' if n > 1 else '') for d, n in sorted(diameters.items()))})")
    checks = evaluate(measured, expected)
    for text, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {text}")
    failed = sum(not ok for _, ok in checks)
    print("FreeCAD check: OK" if not failed else f"FreeCAD check: FAILED ({failed} of {len(checks)} checks)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Open an exported STEP file in FreeCAD and measure it: a check of the result that does not use CadQuery.

FreeCAD runs headless (freecadcmd) outside the container and is not part of the image. This script needs only the
standard library, so any current Python will do. FreeCAD is also built on OpenCASCADE: the STEP reader and the
measuring code are independent, the geometry kernel is not.

Without --spec the solid is checked: valid, exactly one solid, positive volume. With --spec (the spec.json written by
export_step) the size and every hole of `expected` are checked too, using the thresholds of d2c/cad/checks.py: 0.01 mm
(adjustable with --tol MM), axes parallel from |cos| 0.9999, holes are concave cylinders.

A STEP file at the tolerance midpoint (`<part>_mid.step`) differs from the nominal size of the spec (for example
diameter 30 versus 30.042). With --mid the script replaces the expected size and hole diameters with the measured values
of the build from `report_mid.json` next to the STEP file (as written by export_step with variant="mid"; holes are
matched by position). This keeps the script on the standard library and still checks that FreeCAD measures the same as
the build. If report_mid.json is missing, the script exits with code 1.

Usage: python scripts/freecad_check.py part.step [--spec spec.json] [--freecad PATH] [--tol MM] [--mid]

Exit code: 0 all checks passed, 1 a check failed or FreeCAD could not read the file, 2 FreeCAD not found (argparse also
uses 2 for an invalid command line), so CI can tell a missing tool from a failed check.
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

LINEAR_TOL = 0.01  # mm, wie in d2c/spec.py
AXIS_COS = 0.9999  # kleinster |cos| des Winkels zweier Achsen, der als parallel zählt, wie in d2c/spec.py
TIMEOUT = 120  # Sekunden je FreeCAD-Lauf
FREECAD_PATHS = (  # wo das kopflose Programm üblicherweise liegt, wenn es nicht im PATH ist
    "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    "C:/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe",
)

# Läuft in FreeCAD: liest die in D2C_STEP genannte STEP, schreibt die Messung als JSON nach D2C_FC_OUT.
# Ein Zylinder ist konkav (eine Bohrung), wenn die äußere Normale seiner Fläche zur Achse weist.
MEASURE = r"""
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
"""


def find_freecad(path: str | None) -> str | None:
    """Das FreeCAD-Programm: der gegebene Pfad oder Name, sonst einer im PATH, sonst ein üblicher Installationsort."""
    if path:
        return path if Path(path).is_file() else shutil.which(path)
    on_path = shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
    return on_path or next((p for p in FREECAD_PATHS if Path(p).is_file()), None)


def no_freecad(message: str) -> None:
    print(f"{message} Install FreeCAD or point --freecad PATH to freecadcmd (FreeCADCmd.exe on Windows).", file=sys.stderr)
    sys.exit(2)


def measure(exe: str, step: Path) -> dict:
    """FreeCAD die STEP messen lassen; Beendigung mit Code 1, wenn es nichts liefert."""
    with tempfile.TemporaryDirectory() as tmp:
        script, result = Path(tmp, "measure.py"), Path(tmp, "result.json")
        script.write_text(MEASURE, encoding="utf-8")
        env = dict(os.environ, D2C_STEP=str(step.resolve()), D2C_FC_OUT=str(result))
        try:
            done = subprocess.run([exe, str(script)], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            sys.exit(f"FreeCAD did not finish {step} within {TIMEOUT} s")
        except OSError as e:
            no_freecad(f"FreeCAD could not be started ({e}).")
        if not result.is_file():
            lines = re.sub(r"\x1b\[[0-9;]*m", "", done.stdout + done.stderr).splitlines()
            tail = "\n".join([line for line in lines if line.strip()][-5:])
            sys.exit(f"FreeCAD could not read {step} (exit code {done.returncode}):\n{tail}")
        return json.loads(result.read_text(encoding="utf-8"))


def unit(v: list[float]) -> list[float]:
    length = math.sqrt(sum(c * c for c in v))
    return [c / length for c in v]


def xyz(v: list[float]) -> str:
    return "(" + ", ".join(f"{c:g}" for c in v) + ")"


def is_hole(cylinder: dict, hole: dict, tol: float = LINEAR_TOL) -> bool:
    """Ein konkaver Zylinder mit dem Durchmesser der Bohrung, seine Achse durch hole['at'] und parallel zu ihr."""
    axis = unit(cylinder["axis"])
    rel = [p - q for p, q in zip(hole["at"], cylinder["point"])]
    along = sum(r * a for r, a in zip(rel, axis))
    distance = math.sqrt(sum((r - along * a) ** 2 for r, a in zip(rel, axis)))
    cos = abs(sum(a * b for a, b in zip(axis, unit(hole["axis"]))))
    return cylinder["concave"] and abs(cylinder["d"] - hole["d"]) <= tol and distance <= tol and cos >= AXIS_COS


def evaluate(measured: dict, expected: dict | None, tol: float = LINEAR_TOL) -> list[tuple[str, bool]]:
    """Je Prüfung ein (Beschreibung, ok)."""
    solid = (
        f"Solid: valid={measured['valid']}, solids={measured['solids']}, volume={measured['volume']:.2f} mm³",
        measured["valid"] and measured["solids"] == 1 and measured["volume"] > 0,
    )
    checks = [solid]
    if expected is not None:
        size = measured["bbox"]
        checks.append(
            (
                f"bbox: expected {' x '.join(f'{v:g}' for v in expected['bbox'])}, FreeCAD {' x '.join(f'{v:.3f}' for v in size)}",
                all(abs(want - got) <= tol for want, got in zip(expected["bbox"], size)),
            )
        )
        concave = [c for c in measured["cylinders"] if c["concave"]]
        for hole in expected.get("holes", []):
            checks.append(
                (f"hole d={hole['d']:g} at {xyz(hole['at'])} axis {xyz(hole['axis'])}", any(is_hole(c, hole, tol) for c in concave))
            )
    return checks


def load_expected(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))["expected"]
    except (OSError, ValueError, KeyError) as e:
        sys.exit(f"Cannot read the expected values from {path}: {e!r}")


def apply_mid_report(expected: dict, step: Path) -> dict:
    """Die Sollwerte auf die Messwerte aus report_mid.json neben der STEP-Datei setzen; Beendigung mit Code 1, wenn sie fehlt."""
    path = step.with_name("report_mid.json")
    try:
        checks = json.loads(path.read_text(encoding="utf-8"))["dims"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        sys.exit(f"--mid needs {path} next to the STEP file: {e!r}")
    expected = {**expected, "bbox": list(expected["bbox"]), "holes": [dict(h) for h in expected.get("holes", [])]}
    for check in checks:
        if not check.get("actual"):
            continue
        if check["check"] in ("bbox_x", "bbox_y", "bbox_z"):
            expected["bbox"]["xyz".index(check["check"][-1])] = check["actual"]
        elif check["check"] == "hole":
            at = check["expected"]["at"]
            for hole in expected["holes"]:
                if all(abs(a - b) <= 1e-6 for a, b in zip(hole["at"], at)) and abs(hole["d"] - check["expected"]["d"]) <= 1e-6:
                    hole["d"] = check["actual"]["d"]
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", type=Path, help="the STEP file to check")
    parser.add_argument("--spec", help="spec.json whose expected size and holes are checked in addition")
    parser.add_argument("--freecad", help="path to freecadcmd (FreeCADCmd.exe on Windows); default: PATH, then the usual install locations")
    parser.add_argument("--tol", type=float, default=LINEAR_TOL, help="threshold in mm for size and holes (default 0.01)")
    parser.add_argument(
        "--mid", action="store_true", help="STEP at tolerance midpoint: the expected values come from report_mid.json next to the STEP file"
    )
    args = parser.parse_args()
    exe = find_freecad(args.freecad)
    if exe is None:
        no_freecad(f"FreeCAD not found: {args.freecad}." if args.freecad else "FreeCAD not found.")
    if not args.step.is_file():
        sys.exit(f"STEP file not found: {args.step}")
    expected = load_expected(args.spec) if args.spec else None
    if args.mid and expected is not None:
        expected = apply_mid_report(expected, args.step)
    measured = measure(exe, args.step)

    diameters = Counter(round(c["d"], 3) for c in measured["cylinders"] if c["concave"])
    print(f"FreeCAD {measured['version']} read {args.step}")
    print(
        f"  concave cylinders: {sum(diameters.values())} "
        f"({', '.join(f'd={d:g}' + (f' x{n}' if n > 1 else '') for d, n in sorted(diameters.items()))})"
    )
    checks = evaluate(measured, expected, args.tol)
    for text, ok in checks:
        print(f"  {'ok    ' if ok else 'FAILED'}  {text}")
    failed = sum(not ok for _, ok in checks)
    print("FreeCAD check: OK" if not failed else f"FreeCAD check: FAILED ({failed} of {len(checks)} checks)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Eine exportierte STEP-Datei in FreeCAD öffnen und messen: eine Prüfung des Ergebnisses ohne CadQuery.

FreeCAD läuft kopflos (freecadcmd) außerhalb des Containers, es gehört nicht zum Image. Dieses Skript braucht
nur die Standardbibliothek, also reicht jedes aktuelle Python. FreeCAD baut ebenfalls auf OpenCASCADE: unabhängig
sind der STEP-Leser und der Messcode, nicht der Geometriekern.

Ohne --spec wird der Festkörper geprüft: gültig, genau ein Festkörper, positives Volumen. Mit --spec (die spec.json,
die export_step schreibt) werden auch Größe und jede Bohrung von `expected` geprüft, mit den Schwellwerten von
d2c/checks.py: 0,01 mm, Achsen parallel ab |cos| 0,9999, Bohrungen sind konkave Zylinder.

Aufruf: python scripts/freecad_check.py part.step [--spec spec.json] [--freecad PFAD]

Rückkehrcode: 0 alle Prüfungen bestanden, 1 eine Prüfung durchgefallen oder FreeCAD konnte die Datei nicht lesen,
2 FreeCAD nicht gefunden (argparse benutzt 2 ebenfalls für eine fehlerhafte Kommandozeile), damit CI ein fehlendes
Werkzeug von einer durchgefallenen Prüfung unterscheiden kann.
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

LINEAR_TOL = 0.01  # mm, wie in d2c/checks.py
AXIS_COS = 0.9999  # kleinster |cos| des Winkels zweier Achsen, der als parallel zählt, wie in d2c/checks.py
TIMEOUT = 120  # Sekunden je FreeCAD-Lauf
FREECAD_PATHS = (  # wo das kopflose Programm üblicherweise liegt, wenn es nicht im PATH ist
    "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    "C:/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe",
)

# Läuft in FreeCAD: liest die in D2C_STEP genannte STEP, schreibt die Messung als JSON nach D2C_FC_OUT.
# Ein Zylinder ist konkav (eine Bohrung), wenn die äußere Normale seiner Fläche zur Achse weist.
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
    """Das FreeCAD-Programm: der gegebene Pfad oder Name, sonst einer im PATH, sonst ein üblicher Installationsort."""
    if path:
        return path if Path(path).is_file() else shutil.which(path)
    on_path = shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
    return on_path or next((p for p in FREECAD_PATHS if Path(p).is_file()), None)


def no_freecad(message: str) -> None:
    print(f"{message} Install FreeCAD or pass --freecad PATH to freecadcmd (FreeCADCmd.exe on Windows).",
          file=sys.stderr)
    sys.exit(2)


def measure(exe: str, step: Path) -> dict:
    """FreeCAD die STEP messen lassen; Beendigung mit Code 1, wenn es nichts liefert."""
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
    """Ein konkaver Zylinder mit dem Durchmesser der Bohrung, seine Achse durch hole['at'] und parallel zu ihr."""
    axis = unit(cylinder["axis"])
    rel = [p - q for p, q in zip(hole["at"], cylinder["point"])]
    along = sum(r * a for r, a in zip(rel, axis))
    distance = math.sqrt(sum((r - along * a) ** 2 for r, a in zip(rel, axis)))
    cos = abs(sum(a * b for a, b in zip(axis, unit(hole["axis"]))))
    return (cylinder["concave"] and abs(cylinder["d"] - hole["d"]) <= LINEAR_TOL and distance <= LINEAR_TOL
            and cos >= AXIS_COS)


def evaluate(measured: dict, expected: dict | None) -> list[tuple[str, bool]]:
    """Je Prüfung ein (Beschreibung, ok)."""
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

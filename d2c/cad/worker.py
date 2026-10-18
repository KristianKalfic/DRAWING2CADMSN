"""Aufbau-Worker: Prozess `python -I -m d2c.cad.worker`, den d2c.cad.sandbox.run_worker startet.

stdin liefert eine JSON-Nutzlast {"ops": [...], "expected": {...} | null, "marks": [...], "export_path": str | null}
und optional "pmi_spec": die PartSpec als dict (die STEP-Datei enthält dann ihre semantischen Maße sowie Form- und
Lagetoleranzen; was sich keiner Fläche zuordnen lässt, steht als Warnung in build_notes), "glb_path":
str (bestehen die Prüfungen, wird dort das GLB geschrieben, daneben marks.json und pmi.json) und "tol": die größte
Abweichung in mm, die die Maßprüfungen akzeptieren (Standard spec.LINEAR_TOL). "dimensions" sind die Maße der
Spezifikation ({"id", "kind", "value", "text", "assumed"}): der Bericht nennt dann, welche davon das Modell enthält
(dims_found).
stdout erhält genau eine JSON-Zeile, den Bericht mit den Schlüsseln ok, error, solid, dims, dims_found, build_notes, roundtrip.
"""

import json
import os
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from d2c.cad.checks import DIM_TOL, circle_diameters, dimension_check, dims_found, metrics, solid_check
from d2c.cad.export import gdt_from, pin_marks, pmi_from, read_pmi, step_roundtrip, unplaced_pmi, write_glb
from d2c.cad.ops import BuildError, HolesOp, Op, build
from d2c.errors import error_report, validation_error_message
from d2c.spec import LINEAR_TOL, Expected, PartSpec

OPS = TypeAdapter(list[Op])
SCRATCH_STEP = "model.step"  # Exportpfad des Modells, wenn im payload keiner angegeben ist: das temporäre Arbeitsverzeichnis.


def thread_holes(ops: list[Op]) -> list[dict]:
    """Ein Eintrag je Bohrung einer Gewindeop für write_step: ein Punkt auf der Achse, die Bohrrichtung, der gebohrte Durchmesser d."""
    found = []
    for op in ops:
        if isinstance(op, HolesOp) and op.kind == "tapped":
            plane = op.plane.workplane().plane
            drill = plane.zDir * (1 if op.flip else -1)
            found += [{"at": plane.toWorldCoords(c).toTuple(), "axis": drill.toTuple(), "d": op._drilled_d()} for c in op._centers()]
    return found


def export_warnings(dims: list[dict], found: dict | None) -> list[dict]:
    """build_notes für Maße ohne Modellbezug und für Bohrungen, deren Achse Werkstoff trifft; beides ist kein Fehler."""
    notes = []
    if found and found["missing"]:
        ids = ", ".join(m["id"] for m in found["missing"])
        total = len(found["missing"]) + len(found["found"])
        notes.append({"warning": f"{len(found['missing'])} of {total} printed dimensions cannot be found in the model: {ids}"})
    for d in dims:
        if d["check"] == "hole" and d["through"] is False:
            at = ", ".join(f"{x:g}" for x in d["expected"]["at"])
            notes.append(
                {
                    "warning": f"The hole Ø{d['expected']['d']:g} at ({at}) is not a through hole: "
                    "its axis meets material (blind hole or closed)"
                }
            )
    return notes


def make_report(payload: dict) -> dict:
    """Baut, prüft und exportiert das Modell aus dem payload. Jeder Fehler auf dem Weg wird als `error` zurückgegeben."""
    try:
        ops = OPS.validate_python(payload["ops"])
        want = payload.get("expected")
        expected = Expected.model_validate(want) if want is not None else None
        spec = PartSpec.model_validate(payload["pmi_spec"]) if payload.get("pmi_spec") else None
        tol, marks = payload.get("tol", LINEAR_TOL), payload.get("marks", [])
        wp, notes = build(ops)
        if not wp.objects:  # CadQuery behält für einen leeren Verbund kein Objekt, und die Prüfungen können das nicht lesen
            raise BuildError("The model is empty. Check whether a cut removed all material.")
        solid = solid_check(wp) | {"cyl_d": metrics(wp.val())["cyl_diameters"], "circle_d": circle_diameters(wp.val())}
        dims = dimension_check(wp, expected, tol) if expected is not None else []
        found = dims_found(wp.val(), payload["dimensions"], max(DIM_TOL, tol)) if payload.get("dimensions") else None
        if payload.get("export_path"):  # Warnungen nur beim Export: Während des Aufbaus fehlen Merkmale noch
            notes += export_warnings(dims, found)
        pmi = pmi_from(spec, wp.val(), {"dims": dims}, tol) if spec is not None else []
        marks = pin_marks(marks, pmi, wp.val())
        gdt = gdt_from(spec, wp.val()) if spec is not None else None
        if spec is not None and (unplaced := unplaced_pmi(spec, pmi, gdt)):
            notes.append({"warning": f"No PMI for {', '.join(unplaced)}: no matching face at the point (faces_near or near)"})
        threads = thread_holes(ops)
        step = payload.get("export_path") or SCRATCH_STEP
        roundtrip = step_roundtrip(wp, step, marks, pmi, threads, gdt)
        ok = solid["ok"] and all(d["ok"] for d in dims) and roundtrip["ok"]
        if ok and payload.get("glb_path"):
            glb = Path(payload["glb_path"])
            write_glb(wp.val(), str(glb), marks, threads)  # schreibt marks.json neben das GLB
            glb.with_name("pmi.json").write_text(json.dumps(read_pmi(step), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except ValidationError as e:
        return error_report(f"ValidationError: {validation_error_message(e)}")
    except Exception as e:  # Auch bei ungültigen Eingaben sowie Aufbau-, Prüf- und Exportfehlern erhält der Aufrufer einen Bericht.
        return error_report(f"{type(e).__name__}: {e}")
    return {
        "ok": ok,
        "error": None,
        "solid": solid,
        "dims": dims,
        "dims_found": found,
        "build_notes": notes,
        "roundtrip": roundtrip,
    }


def main() -> None:
    """Liest das payload aus stdin und schreibt den Bericht als einzelne JSON-Zeile auf stdout."""
    with os.fdopen(os.dup(1), "w") as report_out:  # Echte stdout-Ausgabe für den Bericht sichern.
        os.dup2(2, 1)  # print() in Skripten und Meldungen von OpenCascade gehen nun auf stderr.
        report_out.write(json.dumps(make_report(json.load(sys.stdin))) + "\n")


if __name__ == "__main__":
    main()

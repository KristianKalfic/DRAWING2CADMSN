"""Aufbau-Worker: Prozess `python -I -m d2c.worker`, den d2c.sandbox.run_worker startet.

stdin enthält die JSON-Nutzlast `payload` {"ops": [...], "expected": {...} | null, "marks": [...], "export_path": str | null}.
stdout erhält genau eine JSON-Zeile: den Bericht mit den Schlüsseln ok, error, solid, dims, build_notes und roundtrip.
"""
import json
import os
import sys

from pydantic import TypeAdapter, ValidationError

from d2c.checks import dimension_check, solid_check, step_roundtrip
from d2c.ops import BuildError, Op, build
from d2c.sandbox import error_report, validation_error_message
from d2c.spec import Expected

OPS = TypeAdapter(list[Op])
SCRATCH_STEP = "model.step"  # Exportpfad des Modells, wenn im payload keiner angegeben ist: das temporäre Arbeitsverzeichnis.


def make_report(payload: dict) -> dict:
    """Baut, prüft und exportiert das Modell aus dem payload. Jeder Fehler auf dem Weg wird als `error` zurückgegeben."""
    try:
        ops = OPS.validate_python(payload["ops"])
        want = payload.get("expected")
        expected = Expected.model_validate(want) if want is not None else None
        wp, notes = build(ops)
        if not wp.objects:  # CadQuery speichert für ein leeres Compound kein Objekt, das die Prüfungen lesen könnten.
            raise BuildError("Das Modell ist leer. Prüfe, ob ein Schnitt das gesamte Material entfernt hat.")
        solid = solid_check(wp)
        dims = dimension_check(wp, expected) if expected is not None else []
        roundtrip = step_roundtrip(wp, payload.get("export_path") or SCRATCH_STEP, payload.get("marks", []))
    except ValidationError as e:
        return error_report(f"ValidationError: {validation_error_message(e)}")
    except Exception as e:  # Auch bei ungültigen Eingaben sowie Aufbau-, Prüf- und Exportfehlern erhält der Aufrufer einen Bericht.
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
    """Liest das payload aus stdin und schreibt den Bericht als einzelne JSON-Zeile auf stdout."""
    with os.fdopen(os.dup(1), "w") as report_out:  # Echte stdout-Ausgabe für den Bericht sichern.
        os.dup2(2, 1)  # print() in Skripten und Meldungen von OpenCascade gehen nun auf stderr.
        report_out.write(json.dumps(make_report(json.load(sys.stdin))) + "\n")


if __name__ == "__main__":
    main()

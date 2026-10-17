"""Gespeicherte Teile aus ihrer spec.json und ops.json neu aufbauen und je Teil eine Zeile ausgeben.

Ohne --export werden die Ops aufgebaut und gegen die erwarteten Größen der Spezifikation geprüft; nichts wird
geschrieben. Mit --export geht jedes Teil durch Session.export in einer frischen Sitzung, die es aus dem
results-Ordner lädt und die STEP-, Report- und Review-Dateien erneut schreibt, damit eine Änderung am Aufbau oder
am Export bestehende Teile ohne neue Modellsitzung erreicht. Ein Teil, das nicht ok ist, verliert diese Dateien,
wie bei export_step. Die Teile sind jeder Ordner des results-Ordners mit einer ops.json, oder die genannten.
Der Rückkehrcode ist 1, wenn ein Teil nicht ok ist.

Aufruf: python scripts/replay.py [--export] [teil ...]
"""

import argparse
import json
import sys
from collections.abc import Callable

from d2c.review import validation
from d2c.sandbox import run_worker
from d2c.session import Session, results_root
from d2c.spec import PartSpec


def rebuild(part: str) -> dict:
    """Der Worker-Report der gespeicherten Ops eines Teils, aufgebaut gegen seine gespeicherte Spezifikation. Nichts wird geschrieben."""
    folder = results_root() / part
    spec = PartSpec.model_validate_json((folder / "spec.json").read_text(encoding="utf-8"))
    ops = json.loads((folder / "ops.json").read_text(encoding="utf-8"))
    return run_worker({
        "ops": ops,
        "expected": spec.expected.model_dump(mode="json"),
        "marks": [{"id": r.id, "near": r.mark_near} for r in spec.review if r.mark_near],
        "export_path": None,
    })


def export(part: str) -> dict:
    """Der Worker-Report eines Exports durch eine frische Sitzung, die das Teil aus dem results-Ordner lädt."""
    return Session().export(part)["report"]


def problem(build: Callable[[str], dict], part: str) -> str | None:
    """Warum das Teil nach `build` nicht ok ist, oder None, wenn es ok ist."""
    try:
        report = build(part)
    except (KeyError, ValueError, OSError) as e:  # unbekanntes Teil, unlesbare Dateien
        return str(e.args[0] if isinstance(e, KeyError) else e)
    if report["ok"]:
        return None
    failed = (r for r in validation(report) if not r["ok"])
    return "; ".join(f"{r['check']}: expected {r['expected']}, got {r['actual']}" for r in failed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild stored parts from their ops.json.")
    parser.add_argument("--export", action="store_true", help="write the STEP, report and review files again")
    parser.add_argument("parts", nargs="*", metavar="part", help="default: every part with an ops.json")
    args = parser.parse_args()
    parts = args.parts or sorted(p.parent.name for p in results_root().glob("*/ops.json"))
    if not parts:
        print(f"no parts in {results_root()}", file=sys.stderr)
    failed = 0
    for part in parts:
        why = problem(export if args.export else rebuild, part)
        print(f"{part}: ok" if why is None else f"{part}: FAILED ({' '.join(why.split())})", flush=True)
        failed += why is not None
    return int(failed > 0)


if __name__ == "__main__":
    sys.exit(main())

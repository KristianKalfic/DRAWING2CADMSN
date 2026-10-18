"""python -m d2c.demo [--out DIR]: build, check and export the three demo parts from d2c/demo.

Runs the whole chain for each part (specification, ops, checks, STEP export, review list) without a language model,
network or sample data: a bracket, a stepped shaft with chamfer (revolve) and a flange with an H7 fit and one assumption.
Prints one PASSED or FAILED line per check, then the assumptions and the files written, and ends with the line
"n of 3 parts exported, ..."; the exit code is 0 only if all parts are exported.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from d2c.demo import DATA
from d2c.quality import review
from d2c.session import Session
from d2c.spec import PartSpec

PARTS = ("demo", "shaft", "flange")  # Dateipräfix je Teil: <Präfix>_spec.json und <Präfix>_ops.json in d2c/demo


def build_part(session: Session, prefix: str) -> bool:
    """Ein Teil bauen, prüfen und exportieren; True, wenn der Export bestanden hat."""
    spec = PartSpec.model_validate_json((DATA / f"{prefix}_spec.json").read_text("utf-8"))
    print(f"Part {spec.name} ({spec.title})")
    session.submit_spec(spec)
    for index, op in enumerate(json.loads((DATA / f"{prefix}_ops.json").read_text("utf-8"))):
        error = session.apply(spec.name, op)["error"]
        if error:
            print(f"FAILED operation {index} ({op['op']}): {error}")
            return False

    result = session.export(spec.name)
    for row in review.validation(result["report"]):
        print(f"{'PASSED' if row['ok'] else 'FAILED'} {row['check']}: expected {row['expected']}, actual {row['actual']}")
    print("Assumptions (no., dimension, assumption, reason):")
    for a in review.assumptions(spec, result["report"]):
        print(f"  {a['id']}  {', '.join(r['id'] for r in a['refs'])}  {a['assumption']}  ({a['reason']})")
    if result["ok"]:
        print("Files written:")
        for path in result["files"].values():
            print(f"  {path}")
    return result["ok"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m d2c.demo", description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("/tmp/d2c-demo"), help="results folder (default: %(default)s)")
    out = parser.parse_args(argv).out
    os.environ["D2C_RESULTS"] = str(out.resolve())  # absolut: die Bau-Sitzung läuft in einem anderen Arbeitsverzeichnis
    os.environ.setdefault("D2C_TABLES", str(DATA / "tables"))

    session = Session()
    exported = 0
    for prefix in PARTS:
        exported += build_part(session, prefix)
        print()
    ok = exported == len(PARTS)
    print(f"{exported} of {len(PARTS)} parts exported, {'all' if ok else 'not all'} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    # Die Sitzung meldet jeden Zwischenstand des Aufbaus mit offenen Prüfungen als Warnung; in der Demo ist das Rauschen.
    logging.getLogger("d2c.session").setLevel(logging.ERROR)
    sys.exit(main())

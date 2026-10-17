"""python -m d2c.demo [--out DIR]: build, check and export the demo bracket of tests/fixtures.

Runs the whole chain (spec, ops, checks, STEP export, review list) without a language model, network or
sample data. Prints one PASS or FAIL line per check, the assumptions and the files written; the exit code
is 0 only if the export is ok.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from d2c import review
from d2c.session import Session
from d2c.spec import PartSpec

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _verdict(ok: bool) -> int:
    print(f"All checks passed: {'yes' if ok else 'no'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m d2c.demo", description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("/tmp/d2c-demo"), help="results folder (default: %(default)s)")
    out = parser.parse_args(argv).out
    os.environ["D2C_RESULTS"] = str(out.resolve())  # absolute: the build worker runs in another working directory
    os.environ.setdefault("D2C_TABLES", str(FIXTURES / "tables"))

    spec = PartSpec.model_validate_json((FIXTURES / "demo_spec.json").read_text("utf-8"))
    session = Session()
    session.submit_spec(spec)
    for index, op in enumerate(json.loads((FIXTURES / "demo_ops.json").read_text("utf-8"))):
        error = session.apply(spec.name, op)["error"]
        if error:
            print(f"FAIL op {index} ({op['op']}): {error}")
            return _verdict(False)

    result = session.export(spec.name)
    for row in review.validation(result["report"]):
        print(f"{'PASS' if row['ok'] else 'FAIL'} {row['check']}: expected {row['expected']}, actual {row['actual']}")
    print("Assumptions (id, dimension, assumption, reason):")
    for a in review.assumptions(spec, result["report"]):
        print(f"  {a['id']}  {', '.join(r['id'] for r in a['refs'])}  {a['assumption']}  ({a['reason']})")
    if result["ok"]:
        print("Files written:")
        for path in result["files"].values():
            print(f"  {path}")
    return _verdict(result["ok"])


if __name__ == "__main__":
    sys.exit(main())

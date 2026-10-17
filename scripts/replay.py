"""Rebuild stored parts from their spec.json and ops.json and print one line per part.

Without --export the ops are built and checked against the expected sizes of the spec; nothing is
written. With --export each part goes through Session.export in a fresh session, which loads it from the
results folder and writes the STEP, report and review files again, so a change to the build or the
export reaches existing parts without a new model session. A part that is not ok loses these files, as
with export_step. The parts are every folder of the results folder with an ops.json, or the ones named.
The exit code is 1 if any part is not ok.

Usage: python scripts/replay.py [--export] [part ...]
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
    """The worker report of the stored ops of a part, built against its stored spec. Nothing is written."""
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
    """The worker report of an export by a fresh session, which loads the part from the results folder."""
    return Session().export(part)["report"]


def problem(build: Callable[[str], dict], part: str) -> str | None:
    """Why the part is not ok after `build`, or None if it is."""
    try:
        report = build(part)
    except (KeyError, ValueError, OSError) as e:  # unknown part, unreadable files
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

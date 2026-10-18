"""Check the example results examples/demo against a fresh demo run, or rewrite them.

Without an argument the script rebuilds the three demo parts in a temporary folder and compares volume and bounding box
of each part with its checked-in report.json (tolerance 0.01 mm and 1e-6 relative). The exit code is 1 on a deviation.
With --update it replaces examples/demo with the fresh result, for example after a change to the export or review list.
The drawings of the examples are the demo drawings under d2c/demo (D2C_DATA=d2c/demo shows them in the web interface).

Usage: python scripts/check_example.py [--update]
"""

import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

from d2c.demo import DATA
from d2c.demo.__main__ import main as demo_main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "demo"
PARTS = {"demo_bracket": "demo", "demo_shaft": "shaft", "demo_flange": "flange"}  # Teil -> Dateipräfix in d2c/demo
BBOX_TOL = 0.01  # mm
VOLUME_TOL = 1e-6  # relativ


def build(out: Path) -> dict[str, dict]:
    """Die Demo nach `out` bauen und die report.json aller Teile lesen."""
    with contextlib.redirect_stdout(io.StringIO()):
        code = demo_main(["--out", str(out)])
    if code != 0:
        raise SystemExit("The demo failed: python -m d2c.demo")
    return {part: json.loads((out / part / "report.json").read_text("utf-8")) for part in PARTS}


def differences(fresh: dict, stored: dict) -> list[str]:
    a, b = fresh["solid"], stored["solid"]
    found = []
    if abs(a["volume"] - b["volume"]) > VOLUME_TOL * b["volume"]:
        found.append(f"volume {a['volume']:.4f} instead of {b['volume']:.4f} mm³")
    for i, (x, y) in enumerate(zip(a["bbox"], b["bbox"], strict=True)):
        if abs(x - y) > BBOX_TOL:
            found.append(f"bounding box[{i}] {x:.4f} instead of {y:.4f} mm")
    return found


def update(out: Path) -> None:
    """examples/demo mit dem frischen Ergebnis überschreiben; absolute Pfade im Bericht werden relativ."""
    for part, prefix in PARTS.items():
        target = EXAMPLE / part
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(out / part, target)
        report = target / "report.json"
        data = json.loads(report.read_text("utf-8"))
        data["roundtrip"]["path"] = f"{part}.step"
        report.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
        original = json.loads((DATA / f"{prefix}_spec.json").read_text("utf-8"))["drawing"]
        for name in ("spec.json", "review.md"):  # der Pfad der Zeichnung zeigt auf die Datei unter d2c/demo
            file = target / name
            file.write_text(file.read_text("utf-8").replace(original, f"{prefix}_drawing.pdf"), "utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--update", action="store_true", help="rewrite examples/demo")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        fresh = build(Path(tmp))
        if args.update:
            update(Path(tmp))
            print(f"{EXAMPLE} updated")
            return 0
    found = [
        f"{part}: {text}"
        for part, report in fresh.items()
        for text in differences(report, json.loads((EXAMPLE / part / "report.json").read_text("utf-8")))
    ]
    print("examples/demo matches the fresh run" if not found else "Deviation: " + "; ".join(found))
    return int(bool(found))


if __name__ == "__main__":
    sys.exit(main())

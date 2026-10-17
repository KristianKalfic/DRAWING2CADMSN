"""Compare every exported model in the results folder with the reference STEP of its drawing.

The reference of a part is the one *.stp without _PMI in the folder of D2C_REF_DATA (default /app/data)
that the first path component of the drawing in its spec.json names. A row shows the volume deviation in
percent, the largest deviation of the sorted bounding box edges in mm, the cylinder diameters (rounded to
0.01 mm) that the model lacks and those it has in excess, and MATCH if the volume is within 1.0 %, every
edge within 0.05 mm and the diameters are the same. A reference that is not a valid solid, or that cannot
be found or read, gives "reference invalid" instead.

The table goes to stdout and to <results>/EVAL.md, the rows to <results>/EVAL.json. The exit code is
always 0: the table is information, not a pass or fail. The server never sees reference data, this script
is for development and QA.

Usage: python scripts/eval_refs.py
"""

import json
import os
import sys
import unicodedata
from pathlib import Path

import cadquery as cq

from d2c.checks import metrics
from d2c.session import results_root
from d2c.spec import PartSpec

VOLUME_TOL_PCT = 1.0  # largest volume deviation of a MATCH, in percent of the reference volume
BBOX_TOL = 0.05  # mm, largest deviation of a sorted bounding box edge of a MATCH
HEADERS = ["Part", "ΔV %", "ΔBBox max mm", "Missing Ø", "Extra Ø", "MATCH"]


def ref_root() -> Path:
    """The folder that holds the reference folders: D2C_REF_DATA, read on every call."""
    return Path(os.environ.get("D2C_REF_DATA", "/app/data"))


def find_reference(drawing: str) -> Path:
    """The reference STEP of a drawing: the one *.stp without _PMI in the folder its first path component names.

    ValueError if there is no such folder or not exactly one such file.
    """
    first = unicodedata.normalize("NFC", drawing.split("/")[0])
    # Both sides as NFC: macOS mounts list umlauts decomposed, a spec holds them composed.
    folders = [p for p in ref_root().iterdir() if p.is_dir() and unicodedata.normalize("NFC", p.name) == first]
    if not folders:
        raise ValueError(f"no reference folder {first!r} in {ref_root()}")
    steps = [p for p in folders[0].iterdir() if p.suffix.lower() == ".stp" and not p.stem.endswith("_PMI")]
    if len(steps) != 1:
        raise ValueError(f"{len(steps)} files *.stp without _PMI in {folders[0]}, need exactly one")
    return steps[0]


def measure(path: Path, part: str) -> dict:
    """The metrics of a STEP file of a part plus `valid`: a valid shape with a solid of positive volume.

    {} if the file cannot be read; the reason goes to stderr.
    """
    try:
        shape = cq.Compound.makeCompound(cq.importers.importStep(str(path)).vals())
        found = metrics(shape)
        valid = shape.isValid() and bool(shape.Solids()) and found["volume"] > 0
    except Exception as e:  # OpenCascade raises more than ValueError for a damaged or empty file
        print(f"{part}: cannot read {path} ({type(e).__name__}: {e})", file=sys.stderr)
        return {}
    return found | {"valid": valid}


def compare(part: str, mine: dict, ref: dict) -> dict:
    """The row of a part: its model against the reference. A model or reference that could not be read is {}."""
    dv_pct = dbbox = None
    missing, extra = [], []
    if mine and ref:
        if ref["volume"] > 0:
            dv_pct = abs(mine["volume"] - ref["volume"]) / ref["volume"] * 100
        dbbox = max(abs(a - b) for a, b in zip(mine["bbox_sorted"], ref["bbox_sorted"]))
        missing = sorted(set(ref["cyl_diameters"]) - set(mine["cyl_diameters"]))
        extra = sorted(set(mine["cyl_diameters"]) - set(ref["cyl_diameters"]))
    ref_valid = ref.get("valid", False)
    if not ref_valid:
        match = "reference invalid"
    elif mine and dv_pct <= VOLUME_TOL_PCT and dbbox <= BBOX_TOL and not (missing or extra):
        match = "MATCH"
    else:
        match = "DIFF"
    return {
        "part": part, "dv_pct": dv_pct, "dbbox_max_mm": dbbox, "missing_d": missing, "extra_d": extra,
        "match": match, "volume": mine.get("volume"), "bbox_sorted": mine.get("bbox_sorted"),
        "ref_volume": ref.get("volume"), "ref_bbox_sorted": ref.get("bbox_sorted"), "ref_valid": ref_valid,
    }


def evaluate(folder: Path) -> dict:
    """The row of one part folder, results/<part>/."""
    mine = measure(folder / f"{folder.name}.step", folder.name)
    try:
        spec = PartSpec.model_validate_json((folder / "spec.json").read_text(encoding="utf-8"))
        ref = measure(find_reference(spec.drawing), folder.name)
    except (OSError, ValueError) as e:  # no usable spec.json, or not exactly one reference file
        print(f"{folder.name}: no reference ({e})", file=sys.stderr)
        ref = {}
    return compare(folder.name, mine, ref)


def cells(row: dict) -> list[str]:
    """The table cells of a row, in the order of HEADERS; "-" where there is nothing to show."""
    dv_pct, dbbox = row["dv_pct"], row["dbbox_max_mm"]
    return [
        row["part"],
        "-" if dv_pct is None else f"{dv_pct:.2f}",
        "-" if dbbox is None else f"{dbbox:.3f}",
        ", ".join(f"{d:g}" for d in row["missing_d"]) or "-",
        ", ".join(f"{d:g}" for d in row["extra_d"]) or "-",
        row["match"],
    ]


def render(rows: list[dict]) -> str:
    """The rows as a Markdown table."""
    lines = ["| " + " | ".join(HEADERS) + " |", "|" + "---|" * len(HEADERS)]
    lines += ["| " + " | ".join(cells(row)) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def main() -> None:
    with os.fdopen(os.dup(1), "w", encoding="utf-8") as out:  # the real stdout, kept for the table
        os.dup2(2, 1)  # OpenCascade prints read errors to stdout: they go to stderr, not into the table
        root = results_root()
        folders = [p for p in sorted(root.iterdir()) if (p / f"{p.name}.step").is_file()] if root.is_dir() else []
        rows = [evaluate(folder) for folder in folders]
        table = render(rows)
        out.write(table)
    root.mkdir(parents=True, exist_ok=True)
    (root / "EVAL.md").write_text(table, encoding="utf-8")
    (root / "EVAL.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

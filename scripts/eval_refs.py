"""Jedes exportierte Modell im results-Ordner gegen die Referenz-STEP seiner Zeichnung vergleichen.

Die Referenz eines Teils ist die eine *.stp ohne _PMI im Ordner von D2C_REF_DATA (Standard /app/data), den die
erste Pfadkomponente der Zeichnung in seiner spec.json nennt. Eine Zeile zeigt die Volumenabweichung in Prozent,
die größte Abweichung der sortierten Begrenzungsboxkanten in mm, die Zylinderdurchmesser (gerundet auf
0,01 mm), die dem Modell fehlen und die es zu viel hat, und MATCH, wenn das Volumen innerhalb von 1,0 %,
jede Kante innerhalb von 0,05 mm und die Durchmesser gleich sind. Eine Referenz, die kein gültiger Festkörper
ist oder fehlt oder nicht lesbar ist, gibt "reference invalid" statt dessen.

Die Tabelle geht nach stdout und nach <results>/EVAL.md, die Zeilen nach <results>/EVAL.json. Der Rückkehrcode
ist immer 0: die Tabelle ist Information, nicht bestanden oder durchgefallen. Der Server sieht nie
Referenzdaten, dieses Skript ist für Entwicklung und QA.

Aufruf: python scripts/eval_refs.py
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

VOLUME_TOL_PCT = 1.0  # größte Volumenabweichung eines MATCH, in Prozent des Referenzvolumens
BBOX_TOL = 0.05  # mm, größte Abweichung einer sortierten Begrenzungsboxkante eines MATCH
HEADERS = ["Part", "ΔV %", "ΔBBox max mm", "Missing Ø", "Extra Ø", "MATCH"]


def ref_root() -> Path:
    """Der Ordner mit den Referenzordnern: D2C_REF_DATA, bei jedem Aufruf gelesen."""
    return Path(os.environ.get("D2C_REF_DATA", "/app/data"))


def find_reference(drawing: str) -> Path:
    """Die Referenz-STEP einer Zeichnung: die eine *.stp ohne _PMI in dem Ordner, den ihre erste Pfadkomponente nennt.

    ValueError, wenn es keinen solchen Ordner oder nicht genau eine solche Datei gibt.
    """
    first = unicodedata.normalize("NFC", drawing.split("/")[0])
    # Beide Seiten als NFC: macOS-Mounts listen Umlaute zerlegt, eine Spezifikation hält sie zusammengesetzt.
    folders = [p for p in ref_root().iterdir() if p.is_dir() and unicodedata.normalize("NFC", p.name) == first]
    if not folders:
        raise ValueError(f"no reference folder {first!r} in {ref_root()}")
    steps = [p for p in folders[0].iterdir() if p.suffix.lower() == ".stp" and not p.stem.endswith("_PMI")]
    if len(steps) != 1:
        raise ValueError(f"{len(steps)} files *.stp without _PMI in {folders[0]}, need exactly one")
    return steps[0]


def measure(path: Path, part: str) -> dict:
    """Die Metriken einer STEP-Datei eines Teils plus `valid`: eine gültige Form mit Festkörper positiven Volumens.

    {} wenn die Datei nicht lesbar ist; der Grund geht nach stderr.
    """
    try:
        shape = cq.Compound.makeCompound(cq.importers.importStep(str(path)).vals())
        found = metrics(shape)
        valid = shape.isValid() and bool(shape.Solids()) and found["volume"] > 0
    except Exception as e:  # OpenCascade wirft bei einer beschädigten oder leeren Datei mehr als ValueError
        print(f"{part}: cannot read {path} ({type(e).__name__}: {e})", file=sys.stderr)
        return {}
    return found | {"valid": valid}


def compare(part: str, mine: dict, ref: dict) -> dict:
    """Die Zeile eines Teils: sein Modell gegen die Referenz. Ein Modell oder eine Referenz, die nicht lesbar waren, sind {}."""
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
    """Die Zeile eines Teilordners, results/<teil>/."""
    mine = measure(folder / f"{folder.name}.step", folder.name)
    try:
        spec = PartSpec.model_validate_json((folder / "spec.json").read_text(encoding="utf-8"))
        ref = measure(find_reference(spec.drawing), folder.name)
    except (OSError, ValueError) as e:  # keine brauchbare spec.json, oder nicht genau eine Referenzdatei
        print(f"{folder.name}: no reference ({e})", file=sys.stderr)
        ref = {}
    return compare(folder.name, mine, ref)


def cells(row: dict) -> list[str]:
    """Die Tabellenzellen einer Zeile, in der Reihenfolge von HEADERS; "-", wo nichts zu zeigen ist."""
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
    """Die Zeilen als Markdown-Tabelle."""
    lines = ["| " + " | ".join(HEADERS) + " |", "|" + "---|" * len(HEADERS)]
    lines += ["| " + " | ".join(cells(row)) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def main() -> None:
    with os.fdopen(os.dup(1), "w", encoding="utf-8") as out:  # das echte stdout, für die Tabelle aufgehoben
        os.dup2(2, 1)  # OpenCascade gibt Lesefehler auf stdout aus: sie gehen nach stderr, nicht in die Tabelle
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

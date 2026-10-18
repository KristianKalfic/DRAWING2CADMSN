"""Gemeinsame Testhilfen: Demo-Daten, Farben und Kennungen des STEP-Exports, Maße und Operationen als Wörterbücher."""

import json

from pydantic import TypeAdapter

from d2c.cad.ops import Op
from d2c.demo import DATA

DEMO_PDF = DATA / "demo_drawing.pdf"
# Bereiche der Demo-Zeichnung als Seitenanteile (x0, y0, x1, y1), Ursprung oben links.
DEMO_FRONT_VIEW = (0.21, 0.202, 0.781, 0.333)
DEMO_TOP_VIEW = (0.21, 0.404, 0.781, 0.69)
DEMO_TITLE_BLOCK = (0.467, 0.838, 0.962, 0.973)

AP242 = "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"
STEEL, ORANGE, GREEN = "#99abb0", "#ff8000", "#65f073"  # Die von `read_pmi` gemeldeten Farben.
OPS = TypeAdapter(list[Op])


def dimension(dim_id: str, kind: str, value: float, text: str, **more) -> dict:
    """Ein gelesenes Maß als Wörterbuch für eine Spezifikation."""
    return {"id": dim_id, "kind": kind, "value": value, "text": text, "source": None, "confidence": 0.95} | more


def demo_spec() -> dict:
    """Die Demo-Spezifikation als Wörterbuch, bei jedem Aufruf frisch, damit ein Test genau eine Sache ändert."""
    return json.loads((DATA / "demo_spec.json").read_text("utf-8"))


def demo_ops() -> list[dict]:
    """Die Operationen der Demo-Halterung, bei jedem Aufruf frisch."""
    return json.loads((DATA / "demo_ops.json").read_text("utf-8"))


def plain_ops(*ops: dict) -> list[dict]:
    """Die Ops, wie die Sitzung sie hält: durch die Op-Vereinigung geparst, dann wieder ausgegeben."""
    return [op.model_dump() for op in OPS.validate_python(list(ops))]

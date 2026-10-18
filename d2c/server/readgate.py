"""Lesegates von submit_spec: Wie gründlich wurde die Zeichnung gelesen, bevor die Spezifikation sie erfasst?

Jedes Gate lehnt je Bauteil einmal ab (d2c.quality.gates.Gates.read_gates) und geht beim zweiten Einreichen mit Warnung durch.
"""

import subprocess

from d2c.drawing import pages
from d2c.drawing.text import parse_dimension_text, read_text
from d2c.quality.gates import Finding, Gates
from d2c.spec import PartSpec

SHARP_DPI = 200  # kleinste wirksame Auflösung eines Ausschnitts, der als genaues Lesen einer Ansicht gilt
VALUE_TOL = 0.05  # mm, um die ein gedruckter Wert vom Wert eines Spec-Maßes abweichen darf
# Textebene: Anteil der verschiedenen Zahlenwerte der Seite, die ein Spec-Maß trägt. Unter MIN_COVERAGE lehnt submit_spec
# einmal ab, unter WARN_COVERAGE warnt es nur. Rasterzahlen und Schriftfeld zählen mit, daher die lockeren Schwellen.
MIN_COVERAGE = 0.25
WARN_COVERAGE = 0.60
MIN_TEXT_VALUES = 5  # Mit weniger Zahlen auf der Seite sagt der Anteil nichts über die Vollständigkeit.
LISTED = 15  # so viele nicht erfasste Werte nennt ein Gate höchstens


def _spec_values(spec: PartSpec) -> list[float]:
    """Die Werte, die ein Spec-Maß trägt: sein Wert, bei einem Radius auch der Durchmesser, dazu die Zahl seines gedruckten Textes."""
    values = []
    for d in spec.dimensions:
        parsed = parse_dimension_text(d.text)
        values += [d.value, *([2 * d.value] if d.kind == "radius" else []), *([parsed["nominal"]] if parsed else [])]
    return values


def _listed(values: list[float]) -> str:
    shown = ", ".join(f"{v:g}" for v in values[:LISTED])
    return shown + (f" and {len(values) - LISTED} more" if len(values) > LISTED else "")


def _printed_values(spec: PartSpec) -> list[float]:
    """Die verschiedenen gedruckten Nennwerte der Textebene der Seite; leer ohne Textebene oder Leser."""
    try:
        parsed = (i["parsed"] for i in read_text(pages.resolve_drawing(spec.drawing), spec.page) if i.get("parsed"))
        return sorted({p["nominal"] for p in parsed})
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def read_findings(spec: PartSpec, gates: Gates) -> tuple[list[Finding], list[str]]:
    """Wie gründlich die Zeichnung gelesen wurde: (Befunde zum Ablehnen, Texte zum Warnen).

    Gates: "pages" (jede Seite einer mehrseitigen PDF gesehen), "crop" (ein Ausschnitt-Render ab SHARP_DPI), "printed"
    (jeder gedruckte Wert aus measure_drawing steht in einem Maß) und "coverage" (Anteil der Zahlen der Textebene, die
    ein Maß trägt, gegen MIN_COVERAGE und WARN_COVERAGE).
    """
    path, hard, soft = pages.resolve_drawing(spec.drawing), [], []
    total = pages.pdf_info(spec.drawing, spec.page)["pages"]
    unread = [n for n in range(1, total + 1) if n not in gates.pages_read(path)]
    if total > 1 and unread:
        hard.append(
            Finding(
                "read",
                "pages",
                f"Pages {', '.join(map(str, unread))} of {spec.drawing!r} were not read: call render_drawing and read_drawing_text for each page.",
            )
        )
    if not gates.has_sharp(path):
        hard.append(
            Finding(
                "read",
                "crop",
                f"No render_drawing with crop and at least {SHARP_DPI} dpi was made on {spec.drawing!r}: render each view with crop and dpi=300 "
                f"(if the effective dpi in the answer is below {SHARP_DPI}, the crop is too large).",
            )
        )
    have = _spec_values(spec)
    lost = sorted(v for v in gates.printed_dims(path) if not any(abs(v - h) <= VALUE_TOL for h in have))
    if lost:
        text = f"Printed but not captured (in printed_dims, in no dimension): {_listed(lost)}. Capture them as dimensions."
        hard.append(Finding("read", "printed", text))
    printed = _printed_values(spec)
    if len(printed) >= MIN_TEXT_VALUES:
        left = [v for v in printed if not any(abs(v - h) <= VALUE_TOL for h in have)]
        share = 1 - len(left) / len(printed)
        text = (
            f"The text layer of page {spec.page} lists {len(printed)} distinct values, only {len(printed) - len(left)} of them "
            f"({share:.0%}) appear in a dimension. Not captured: {_listed(left)}. Capture them or explain them in a review item."
        )
        if share < MIN_COVERAGE:
            hard.append(Finding("read", "coverage", text))
        elif share < WARN_COVERAGE:
            soft.append(text)
    return hard, soft

"""Maßstab einer Zeichnung: Schriftfeld-Verhältnis lesen, mit der Vektorgeometrie abgleichen und Hinweise ableiten."""

import re

from d2c.drawing import pages, vector
from d2c.spec import PartSpec

SCALE_TOL = 0.005  # Relative Abweichung, ab der zwei Maßstäbe als verschieden gelten.
MIN_PRINTED_DIMS = 6  # Mit weniger gedruckten Werten lässt sich der echte Maßstab nicht von Zufallstreffern unterscheiden.
CLEAR_SHARE = 0.4  # Anteil der gedruckten Werte, um den ein anderer Maßstab den Schriftfeld-Maßstab übertreffen muss.
PRINTED_KINDS = ("length", "diameter", "radius")  # Maße, die über den Maßstab abstimmen.
RATIO = re.compile(r"(\d+(?:[.,]\d+)?)\s*[:/]\s*(\d+(?:[.,]\d+)?)")  # "2:1", "1:2,5" und "1/2"
TITLE_BLOCK_Y = 0.75  # Anteil der Seitenhöhe (Ursprung oben): Treffer darunter liegen im Schriftfeld.


def title_scale(text: str) -> float | None:
    """Zeichnung:Modell eines Schriftfeld-Maßstabs wie "2:1" oder "1:2,5"; None, wenn der Text kein solches Verhältnis enthält."""
    match = RATIO.search(text)
    if match is None:
        return None
    drawn, model = (float(n.replace(",", ".")) for n in match.groups())
    return drawn / model if drawn > 0 and model > 0 else None


def title_block_ratio(items: list[dict]) -> str | None:
    """Das erste Verhältnis wie "2:1" in den Textelementen einer Seite; Treffer im unteren Seitenteil (Schriftfeld) gehen vor."""
    found = [(item, RATIO.search(item["text"])) for item in items]
    found = [(item, match) for item, match in found if match]
    found.sort(key=lambda hit: hit[0]["y0"] <= TITLE_BLOCK_Y)  # stabil: Schriftfeld zuerst
    return f"{found[0][1][1]}:{found[0][1][2]}" if found else None


def scale_notes(scale: float | None, suggestion: list[dict], printed: int) -> list[str]:
    """Hinweise zum Maßstab: Er fehlt, weicht von der Geometrie ab oder beruht auf zu wenigen Werten."""
    notes = []
    if scale is None:
        notes.append("Scale not given: measured at 1:1. Pass the scale from the title block for real model dimensions.")
    if suggestion:
        best = suggestion[0]
        if scale is not None and abs(best["scale"] - scale) > SCALE_TOL * scale:
            notes.append(
                f"Title block scale differs from the geometry: given {scale:g}, geometry gives "
                f"{best['scale']:g} ({best['votes']} of {printed} printed values)"
            )
        if printed < MIN_PRINTED_DIMS:
            notes.append(f"Only {printed} `printed_dims`: `scale_suggestion` is unreliable; give at least {MIN_PRINTED_DIMS} values.")
    elif printed:
        notes.append("No scale matches `printed_dims`.")
    return notes


def scale_check(spec: PartSpec) -> dict:
    """Vergleicht den Schriftfeld-Maßstab der Spezifikation mit dem Maßstab, zu dem die Geometrie ihrer Seite passt.

    Die gedruckten Längen, Durchmesser und Radien der Spezifikation stimmen über vector.suggest_scale ab. Das
    Ergebnis ist {"title_block", "best", "votes", "conflict", "note"}; conflict bedeutet, dass ein anderer Maßstab
    mindestens CLEAR_SHARE der gedruckten Werte mehr trifft als der Schriftfeld-Maßstab. Weniger als
    MIN_PRINTED_DIMS verschiedene Werte, ein Schriftfeld ohne Verhältnis oder eine Seite ohne Vektorgeometrie
    werden nicht geprüft.
    """
    title = title_scale(spec.scale)
    printed = sorted({d.value for d in spec.dimensions if d.kind in PRINTED_KINDS and not d.assumed and d.value > 0})
    check = {"title_block": title, "best": None, "votes": 0, "conflict": False}
    if title is None:
        return check | {"note": f"Title block scale {spec.scale!r} is not a ratio such as 2:1: scale not checked"}
    if len(printed) < MIN_PRINTED_DIMS:
        return check | {
            "note": f"only {len(printed)} distinct printed lengths, diameters and radii, {MIN_PRINTED_DIMS} are needed: scale not checked"
        }
    ranked = vector.suggest_scale(pages.resolve_drawing(spec.drawing), printed, spec.page, top=None)
    if not ranked:
        return check | {"note": "no vector geometry on the page: scale not checked"}
    on_title = [e for e in ranked if abs(e["scale"] - title) <= SCALE_TOL * title]
    title_votes = max((e["votes"] for e in on_title), default=0)
    best = ranked[0] if ranked[0]["votes"] > title_votes else max(on_title, key=lambda e: e["votes"])
    conflict = best["votes"] - title_votes >= CLEAR_SHARE * len(printed)
    note = (
        f"{title_votes} of {len(printed)} printed values match the title block scale {title:g}, "
        f"{best['votes']} match the scale {best['scale']:g}"
    )
    return check | {"best": best["scale"], "votes": best["votes"], "conflict": conflict, "note": note}

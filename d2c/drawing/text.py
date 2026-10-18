"""Liest die Textebene einer Zeichnungsseite: gedruckte Werte mit Symbolen, Positionen und eingelesenen Maßen.

Manche Zeichnungen enthalten echten Text. Ihre Symbole (Durchmesser, Plusminus, GD&T) stehen in Symbolschriften
ohne Unicode-Zuordnung; eine einfache Textextraktion verliert sie deshalb. In den übrigen Zeichnungen liegt der
Text als Vektorkontur vor. Diese liefern keine Einträge – das ist ein gültiges Ergebnis: Die Werte müssen aus dem
gerenderten Bild abgelesen werden.

pdftohtml von Poppler zerlegt einen gedruckten Text in mehrere Teile (Schriftwechsel, Unterschneidung, gedrehter
Text). Diese Teile werden wieder zu Zeilen verbunden. Ein über oder unter einem Maß gesetztes Abmaß hat eine
andere Grundlinie und bleibt als eigener Eintrag daneben stehen. Text, der weder waagerecht noch um 90 Grad
gedreht ist, wird fragmentiert oder abgeschnitten und muss deshalb im Bild geprüft werden."""

import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from d2c.drawing.pages import unit_box

_NUMBER = r"(?:\d+(?:[.,]\d+)?|[.,]\d+)"  # „6,6“ oder „55“; „,1“ entspricht 0,1, wobei die Null fehlt
_SIGNED = rf"[+-]\s*{_NUMBER}"
_DIMENSION = re.compile(
    rf"""
    \s*
    (?:(?P<count>\d+)\s*[x×]\s*)?                       # 4x
    (?P<prefix>Ø|R|M(?=\s*\d))?\s*                      # Durchmesser, Radius, metrisches Gewinde
    (?P<nominal>{_NUMBER})
    (?:\s*[x×]\s*(?P<pitch>{_NUMBER}))?                 # Gewindesteigung: M10x0,75
    (?:\s*(?:±\s*(?P<symmetric>{_NUMBER})
        |(?P<first>{_SIGNED})(?:\s*(?P<second>{_SIGNED}))?))?
    (?:\s*(?P<fit>[A-Za-z]{{1,2}}\d{{1,2}}))?           # Klasse nach ISO 286: h10, H7
    (?:\s*(?P<envelope>Ⓔ))?
    (?:\s*\(\s*(?P<count_after>\d+)\s*[x×]\s*\))?       # (2x)
    \s*
""",
    re.VERBOSE,
)


def _value(number: str) -> float:
    return float(re.sub(r"\s", "", number).replace(",", "."))


def parse_dimension_text(text: str) -> dict | None:
    """Liest ein gedrucktes Maß ein oder gibt None zurück, wenn der Text keinem der bekannten Formate entspricht.

    Beispiele: "Ø6 h10 Ⓔ", "87,5 -0,2", "32±0,08", "55 ±,1", "R 0,3", "M10x0,75", "4x Ø6,6", "Ø10,5 +1,3".
    Das Ergebnis enthält prefix (Ø, R, M oder None), count, nominal, tol_upper, tol_lower (None ohne Abmaß; bei
    einem einseitigen Abmaß ist die fehlende Seite 0), fit, envelope (Ⓔ) und thread ("M10x0.75" bei einem Gewinde,
    sonst None). Als Dezimaltrennzeichen dient Komma oder Punkt.
    """
    m = _DIMENSION.fullmatch(text)
    if m is None or (m["pitch"] and m["prefix"] != "M") or (m["count"] and m["count_after"]):
        return None
    nominal = _value(m["nominal"])
    tol_upper = tol_lower = None
    if m["symmetric"]:
        tol_upper, tol_lower = _value(m["symmetric"]), -_value(m["symmetric"])
    elif m["first"]:
        deviations = [_value(d) for d in (m["first"], m["second"]) if d]
        if len(deviations) == 1:
            deviations.append(0.0)  # Ein einseitiges Abmaß: die andere Seite ist 0
        tol_upper, tol_lower = max(deviations), min(deviations)
    thread = None
    if m["prefix"] == "M":
        thread = f"M{nominal:g}" + (f"x{_value(m['pitch']):g}" if m["pitch"] else "")
    count = m["count"] or m["count_after"]
    return {
        "prefix": m["prefix"],
        "count": int(count) if count else None,
        "nominal": nominal,
        "tol_upper": tol_upper,
        "tol_lower": tol_lower,
        "fit": m["fit"],
        "envelope": m["envelope"] is not None,
        "thread": thread,
    }


# Symbolzeichen nach Schriftfamilie. Nur diese Schriften werden zugeordnet: „L“ ist ein Schnittbuchstabe in einer normalen Schrift.
_SYMBOLS = {
    "ISO1Symbols": str.maketrans({"O": "Ø", "u": "±", "n": "⊥", "l": "⌖", "i": "∥", "r": "▱", "a": "◎", "d": "⌯", "^": "↗"}),
    "GDTSymbols": str.maketrans({"L": "Ⓔ"}),
}

_ZOOM = 1.5  # pdftohtml-Einheiten in der XML je pt (Standardskalierung)
_TIMEOUT = 60  # Sekunden je pdftohtml-Lauf

# pdftohtml meldet Rahmen ohne Schriftmetriken; diese Werte schätzen die fehlenden Angaben.
_ASCENT = 0.8  # Anteil eines Glyphenrahmens oberhalb der Grundlinie
_ADVANCE = 0.45  # em je Zeichen bei gedrehtem Text; pdftohtml meldet dessen Breite als 0
# Teile gehören zu demselben Text, wenn ihre Grundlinien höchstens _BASELINE em auseinanderliegen und der Abstand höchstens _GAP em beträgt
# und sich die Größen um höchstens _SIZE_RATIO unterscheiden (ein Ansichtskennzeichen neben einem Maß gehört zu einem anderen Text).
# Ein Abstand von mehr als _SPACE em zwischen Teilen derselben Schrift gilt als Wortabstand.
_BASELINE = 0.3
_GAP = 1.0
_SIZE_RATIO = 1.5
_SPACE = 0.3


@dataclass(frozen=True)
class _Piece:
    """Ein <text>-Element von pdftohtml. Einheiten: 1/1,5 pt auf der angezeigten Seite, Ursprung oben links."""

    text: str  # Symbole bereits zugeordnet
    font: str  # Schriftfamilie ohne Präfix der Teilmenge
    size: float
    symbol: bool  # In einer Symbolschrift gesetzt
    vertical: bool  # Um 90 Grad gedreht, von unten nach oben gelesen
    baseline: float  # Position quer zur Leserichtung
    start: float  # Entlang der Leserichtung: links nach rechts oder unten nach oben
    end: float
    box: tuple[float, float, float, float]


def _piece(text: ET.Element, spec: ET.Element) -> _Piece:
    family = re.sub(r"^[A-Z]{6}\+", "", spec.get("family"))
    table = next((t for name, t in _SYMBOLS.items() if name in family), None)
    raw = "".join(text.itertext())  # Ein fett gesetzter Teil verschachtelt seinen Text in <b>
    left, top, width, height = (float(text.get(k)) for k in ("left", "top", "width", "height"))
    if width < 0:  # Text auf dem Kopf wird mit negativer Breite gemeldet
        left, width = left + width, -width
    size = float(spec.get("size"))
    if width:
        box = (left, top, left + width, top + height)
        baseline, start, end = top + _ASCENT * height, left, left + width
    else:
        # pdftohtml meldet gedrehten Text mit Breite 0 an dessen Startpunkt.
        # Grenze: Die Länge ist um etwa 30 % geschätzt; genauer wäre sie aus den Schriftbreiten zu ermitteln.
        bottom = top + _ASCENT * height
        length = len(raw) * _ADVANCE * size
        box = (left - _ASCENT * height, bottom - length, left + (1 - _ASCENT) * height, bottom)
        baseline, start, end = left, -bottom, length - bottom
    return _Piece(raw.translate(table) if table else raw, family, size, table is not None, width == 0, baseline, start, end, box)


def _continues(last: _Piece, piece: _Piece) -> bool:
    """Gibt an, ob ein Teil dort fortgesetzt wird, wo die mit last endende Zeile aufhört; beide haben dieselbe Ausrichtung."""
    em = max(last.size, piece.size)
    return (
        em <= _SIZE_RATIO * min(last.size, piece.size)
        and abs(last.baseline - piece.baseline) <= _BASELINE * em
        and piece.start - last.end <= _GAP * em
    )


def _lines(pieces: list[_Piece]) -> list[list[_Piece]]:
    """Gruppiert die Teile in die Texte, als die sie gedruckt wurden, jeweils in Leserichtung."""
    reach = _GAP * max((p.size for p in pieces), default=0)  # Nach einem größeren Abstand folgt kein Text mehr
    lines: list[list[_Piece]] = []
    # Grenze: Offene Zeilen werden linear durchsucht; eine Rasterseite mit 30.000 Teilen dauert 17 s; nach Grundlinie indiziert ginge es schneller.
    open_lines: list[list[_Piece]] = []  # Die Zeilen, denen sich ein weiter rechts liegendes Teil noch anschließen kann
    for piece in sorted(pieces, key=lambda p: (p.vertical, p.start)):
        open_lines = [line for line in open_lines if line[-1].vertical == piece.vertical and piece.start - line[-1].end <= reach]
        line = next((line for line in open_lines if _continues(line[-1], piece)), None)
        if line is None:
            line = [piece]
            lines.append(line)
            open_lines.append(line)
        else:
            line.append(piece)
    return lines


def _join(line: list[_Piece]) -> str:
    """Verbindet die Teile: Zwischen Schriften und bei großen Abständen steht ein Leerzeichen; wo die Unterschneidung eine Zahl zerlegt hat, nicht.

    Ein Durchmesser- oder Plusminuszeichen bleibt bei seiner Zahl, eine öffnende Klammer bei ihrem Inhalt.
    """
    text = line[0].text
    for before, piece in pairwise(line):
        same_run = (before.font, before.size) == (piece.font, piece.size)
        close = piece.start - before.end <= _SPACE * piece.size
        sticky = before.text.endswith(("Ø", "±", "("))
        text += ("" if sticky or (same_run and close) else " ") + piece.text
    return " ".join(text.split())


def _item(line: list[_Piece], page_width: float, page_height: float) -> dict | None:
    text = _join(line)
    # Rahmen von Symbolzeichen sind aufgebläht: Sie zählen entlang des Textes, nicht quer dazu.
    core = [p for p in line if not p.symbol] or line
    boxes, core_boxes = [p.box for p in line], [p.box for p in core]
    if line[0].vertical:
        x0, x1 = min(b[0] for b in core_boxes), max(b[2] for b in core_boxes)
        y0, y1 = min(b[1] for b in boxes), max(b[3] for b in boxes)
    else:
        x0, x1 = min(b[0] for b in boxes), max(b[2] for b in boxes)
        y0, y1 = min(b[1] for b in core_boxes), max(b[3] for b in core_boxes)
    x0, x1 = (round(min(max(v / page_width, 0), 1), 4) for v in (x0, x1))
    y0, y1 = (round(min(max(v / page_height, 0), 1), 4) for v in (y0, y1))
    if not text or x0 >= x1 or y0 >= y1:  # Leer oder außerhalb der Seite
        return None
    return {
        "text": text,
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "font": core[0].font,
        "size": round(core[0].size / _ZOOM, 1),
        "vertical": line[0].vertical,
        "parsed": parse_dimension_text(text),
    }


def items_from_xml(xml: str | bytes) -> list[dict]:
    """Die Texteinträge einer pdftohtml-xml-Seite: von oben nach unten, dann von links nach rechts.

    Jeder Eintrag enthält text (Symbole zugeordnet), x0, y0, x1, y1 als Anteile der angezeigten Seite mit Ursprung
    oben links, font (Schriftfamilie), size (pt), vertical (gedrehter Text) und parsed (siehe parse_dimension_text).
    pdftohtml meldet die angezeigte Seite; eine Drehung durch /Rotate muss nicht korrigiert werden.
    """
    page = ET.fromstring(xml).find("page")
    if page is None:
        return []
    specs = {spec.get("id"): spec for spec in page.iter("fontspec")}
    pieces = [_piece(text, specs[text.get("font")]) for text in page.iter("text")]
    width, height = float(page.get("width")), float(page.get("height"))
    items = [item for line in _lines(pieces) if (item := _item(line, width, height))]
    return sorted(items, key=lambda i: (i["y0"], i["x0"]))


def read_text(pdf_path: str | Path, page: int = 1, region: Sequence[float] | None = None) -> list[dict]:
    """Liest die Texteinträge einer Seite (1-basiert); siehe items_from_xml.

    region ist (x0, y0, x1, y1) als Anteile der angezeigten Seite, mit Ursprung oben links wie beim Zuschnitt des
    Seitendarstellers. Behalten werden Einträge, deren Rahmen den Bereich überlappt. Eine Seite ohne Textebene ergibt [].
    """
    if region is not None:
        region = unit_box(region, "region")
    if page < 1:
        raise ValueError(f"`page` must be at least 1; received: {page}.")
    run = subprocess.run(
        ["pdftohtml", "-xml", "-i", "-stdout", "-f", str(page), "-l", str(page), str(pdf_path)], capture_output=True, timeout=_TIMEOUT
    )
    if run.returncode:
        raise ValueError(f"Page {page} of {pdf_path} could not be read: {run.stderr.decode(errors='replace').strip()}")
    items = items_from_xml(run.stdout)
    if region is None:
        return items
    rx0, ry0, rx1, ry1 = region
    return [i for i in items if i["x0"] < rx1 and i["x1"] > rx0 and i["y0"] < ry1 and i["y1"] > ry0]

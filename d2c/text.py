"""Read the text layer of a drawing page: printed values with their symbols, positions and parsed dimensions.

Three BCT drawings carry real text. Their symbols (diameter, plus-minus, GD&T) sit in symbol fonts without
Unicode mapping, so a plain text extraction loses them. The other drawings hold their text as vector
outlines; they yield no items, which is a valid result: read those values from the rendered image.

poppler's pdftohtml cuts one printed text into several pieces (font changes, kerning, rotated text). The
pieces are joined again into lines. A tolerance stacked above or below its dimension has another baseline
and stays a separate item next to it. Text that is neither horizontal nor rotated by 90 degrees comes out
in fragments or truncated, so such values must be checked in the image.
"""

import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

_NUMBER = r"(?:\d+(?:[.,]\d+)?|[.,]\d+)"  # "6,6" or "55"; ",1" is 0,1 with the zero missing
_SIGNED = rf"[+-]\s*{_NUMBER}"
_DIMENSION = re.compile(rf"""
    \s*
    (?:(?P<count>\d+)\s*[x×]\s*)?                       # 4x
    (?P<prefix>Ø|R|M(?=\s*\d))?\s*                      # diameter, radius, metric thread
    (?P<nominal>{_NUMBER})
    (?:\s*[x×]\s*(?P<pitch>{_NUMBER}))?                 # thread pitch: M10x0,75
    (?:\s*(?:±\s*(?P<symmetric>{_NUMBER})
        |(?P<first>{_SIGNED})(?:\s*(?P<second>{_SIGNED}))?))?
    (?:\s*(?P<fit>[A-Za-z]{{1,2}}\d{{1,2}}))?           # ISO 286 class: h10, H7
    (?:\s*(?P<envelope>Ⓔ))?
    (?:\s*\(\s*(?P<count_after>\d+)\s*[x×]\s*\))?       # (2x)
    \s*
""", re.VERBOSE)


def _value(number: str) -> float:
    return float(re.sub(r"\s", "", number).replace(",", "."))


def parse_dimension_text(text: str) -> dict | None:
    """Parse a printed dimension, or return None if the text is not one of the known forms.

    Examples: "Ø6 h10 Ⓔ", "87,5 -0,2", "32±0,08", "55 ±,1", "R 0,3", "M10x0,75", "4x Ø6,6", "Ø10,5 +1,3".
    The result has prefix (Ø, R, M or None), count, nominal, tol_upper, tol_lower (None without a
    tolerance; the missing side of a one-sided deviation is 0), fit, envelope (Ⓔ) and thread ("M10x0.75"
    for a thread, else None). Comma or point separates the decimals.
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
            deviations.append(0.0)  # a one-sided deviation: the other side is 0
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


# Symbol glyphs by font family. Only these fonts are mapped: "L" is a section letter in a normal font.
_SYMBOLS = {
    "ISO1Symbols": str.maketrans(
        {"O": "Ø", "u": "±", "n": "⊥", "l": "⌖", "i": "∥", "r": "▱", "a": "◎", "d": "⌯", "^": "↗"}),
    "GDTSymbols": str.maketrans({"L": "Ⓔ"}),
}

_ZOOM = 1.5  # pdftohtml XML units per pt (its default scale)
_TIMEOUT = 60  # seconds for one pdftohtml run

# pdftohtml reports boxes without font metrics; these estimate what is missing.
_ASCENT = 0.8  # share of a glyph box above the baseline
_ADVANCE = 0.45  # em per character of rotated text, whose width pdftohtml reports as 0
# Pieces belong to one text when their baselines differ by at most _BASELINE em, the gap is at most _GAP em
# and the sizes differ by at most _SIZE_RATIO (a view letter next to a dimension is another text).
# A gap of more than _SPACE em between pieces of one font is a word space.
_BASELINE = 0.3
_GAP = 1.0
_SIZE_RATIO = 1.5
_SPACE = 0.3


@dataclass(frozen=True)
class _Piece:
    """One <text> element of pdftohtml. Units: 1/1.5 pt on the displayed page, origin top left."""

    text: str  # symbols already mapped
    font: str  # family without the subset prefix
    size: float
    symbol: bool  # set in a symbol font
    vertical: bool  # rotated by 90 degrees, read bottom to top
    baseline: float  # position across the reading direction
    start: float  # along the reading direction: left to right, or bottom to top
    end: float
    box: tuple[float, float, float, float]


def _piece(text: ET.Element, spec: ET.Element) -> _Piece:
    family = re.sub(r"^[A-Z]{6}\+", "", spec.get("family"))
    table = next((t for name, t in _SYMBOLS.items() if name in family), None)
    raw = "".join(text.itertext())  # a bold piece nests its text in <b>
    left, top, width, height = (float(text.get(k)) for k in ("left", "top", "width", "height"))
    if width < 0:  # upside-down text is reported with a negative width
        left, width = left + width, -width
    size = float(spec.get("size"))
    if width:
        box = (left, top, left + width, top + height)
        baseline, start, end = top + _ASCENT * height, left, left + width
    else:
        # pdftohtml reports rotated text with width 0, at its start point.
        # ponytail: the length is estimated (+-30 %), upgrade: read it from the font widths
        bottom = top + _ASCENT * height
        length = len(raw) * _ADVANCE * size
        box = (left - _ASCENT * height, bottom - length, left + (1 - _ASCENT) * height, bottom)
        baseline, start, end = left, -bottom, length - bottom
    return _Piece(raw.translate(table) if table else raw, family, size, table is not None, width == 0,
                  baseline, start, end, box)


def _continues(last: _Piece, piece: _Piece) -> bool:
    """Whether piece goes on where the line ending in last stops; both have the same orientation."""
    em = max(last.size, piece.size)
    return (em <= _SIZE_RATIO * min(last.size, piece.size)
            and abs(last.baseline - piece.baseline) <= _BASELINE * em
            and piece.start - last.end <= _GAP * em)


def _lines(pieces: list[_Piece]) -> list[list[_Piece]]:
    """Group the pieces into the texts they were printed as, each in reading order."""
    reach = _GAP * max((p.size for p in pieces), default=0)  # no text goes on after a wider gap
    lines: list[list[_Piece]] = []
    # ponytail: open lines are scanned linearly, a 30000-piece grid page takes 17 s, upgrade: index by baseline
    open_lines: list[list[_Piece]] = []  # the lines that a piece further along can still join
    for piece in sorted(pieces, key=lambda p: (p.vertical, p.start)):
        open_lines = [line for line in open_lines
                      if line[-1].vertical == piece.vertical and piece.start - line[-1].end <= reach]
        line = next((line for line in open_lines if _continues(line[-1], piece)), None)
        if line is None:
            line = [piece]
            lines.append(line)
            open_lines.append(line)
        else:
            line.append(piece)
    return lines


def _join(line: list[_Piece]) -> str:
    """Join the pieces: a space between fonts and at wide gaps, none where kerning cut a number in two.

    A diameter or plus-minus sign sticks to its number, an opening bracket to its content.
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
    # Symbol glyph boxes are inflated: they count along the text, not across it.
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
    if not text or x0 >= x1 or y0 >= y1:  # blank, or outside the page
        return None
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "font": core[0].font,
            "size": round(core[0].size / _ZOOM, 1), "vertical": line[0].vertical,
            "parsed": parse_dimension_text(text)}


def items_from_xml(xml: str | bytes) -> list[dict]:
    """The text items of one `pdftohtml -xml` page, top to bottom, then left to right.

    Each item has text (symbols mapped), x0, y0, x1, y1 as fractions of the displayed page with the origin
    top left, font (family), size (pt), vertical (rotated text) and parsed (see parse_dimension_text).
    pdftohtml reports the displayed page, so a page with /Rotate needs no correction.
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
    """Read the text items of one page (1-based), see items_from_xml.

    region is (x0, y0, x1, y1) in fractions of the displayed page, origin top left like the crop of the
    page renderer; it keeps the items whose box overlaps it. A page without a text layer gives [].
    """
    if region is not None and (
            len(region) != 4 or not (0 <= region[0] < region[2] <= 1 and 0 <= region[1] < region[3] <= 1)):
        raise ValueError(f"region must be (x0, y0, x1, y1) with 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, "
                         f"got {region!r}")
    if page < 1:
        raise ValueError(f"page must be 1 or higher, got {page}")
    run = subprocess.run(["pdftohtml", "-xml", "-i", "-stdout", "-f", str(page), "-l", str(page), str(pdf_path)],
                         capture_output=True, timeout=_TIMEOUT)
    if run.returncode:
        raise ValueError(f"cannot read page {page} of {pdf_path}: {run.stderr.decode(errors='replace').strip()}")
    items = items_from_xml(run.stdout)
    if region is None:
        return items
    rx0, ry0, rx1, ry1 = region
    return [i for i in items if i["x0"] < rx1 and i["x1"] > rx0 and i["y0"] < ry1 and i["y1"] > ry0]

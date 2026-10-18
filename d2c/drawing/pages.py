"""Zeichnungs-PDFs aus Sicht des Modells: Pfadprüfungen im Datenordner, Seiteninformationen und PNG-Darstellungen.

Seiten sind 1-basiert. Ein Ausschnitt ist (x0, y0, x1, y1) als Anteile der angezeigten Seite, Ursprung oben
links. Angezeigt wird die CropBox mit angewendeter Seitendrehung; genau das zeigen auch pdfinfo (nach Vertauschen
der Seitenlängen einer gedrehten Seite) und der Renderer."""

import math
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Sequence
from pathlib import Path

import pymupdf

from d2c.config import data_root

MAX_DPI = 600
MAX_PX = 2000  # Längste Seite einer gerenderten Abbildung

Crop = tuple[float, float, float, float]


def resolve_drawing(pdf: str) -> Path:
    """Die vorhandene PDF-Datei, die pdf relativ zum Datenordner bezeichnet.

    ValueError, wenn der Pfad aus dem Datenordner hinausführt (".." und Symlinks zählen nach ihrem Ziel) oder nicht
    auf .pdf endet. Fehlt die Datei, listet die Fehlermeldung die dort verfügbaren PDFs auf.
    """
    root = data_root().resolve()
    path = (root / pdf).resolve()
    if path.suffix.lower() != ".pdf" or not path.is_relative_to(root):
        raise ValueError(f"{pdf!r} is not a .pdf file in the data folder.")
    if not path.is_file():
        # macOS-Einhängepunkte liefern Umlaute in zerlegter Form (NFD); vor der Anzeige werden sie wie bei der Eingabe wieder zusammengesetzt.
        found = sorted(
            unicodedata.normalize("NFC", str(p.relative_to(root))) for p in root.rglob("*") if p.suffix.lower() == ".pdf" and p.is_file()
        )
        raise ValueError(f"Drawing {pdf!r} not found; available: {', '.join(found) or 'none'}.")
    return path


PYMUPDF_ERRORS = (RuntimeError, pymupdf.mupdf.FzErrorBase)  # FileDataError und die Fehler beim Zeichnen


def _poppler(*cmd: str) -> str:
    """Die Standardausgabe eines Poppler-Werkzeugs; bei einem Fehler wird eine ValueError-Ausnahme mit der Fehlermeldung ausgelöst."""
    if shutil.which(cmd[0]) is None:
        raise ValueError(f"{cmd[0]} not found (Poppler is optional: macOS `brew install poppler`, Debian `apt install poppler-utils`)")
    done = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if done.returncode:
        raise ValueError(f"{cmd[0]} failed: {done.stderr.strip()}")
    return done.stdout


def _poppler_info(path: str, page: int) -> tuple[int, tuple[float, float] | None]:
    """Seitenzahl und angezeigte Größe der Seite (None, wenn sie nicht existiert) mit pdfinfo."""
    pages = int(re.search(r"^Pages:\s+(\d+)", _poppler("pdfinfo", path), re.M)[1])
    if not 1 <= page <= pages:
        return pages, None
    out = _poppler("pdfinfo", "-f", str(page), "-l", str(page), path)
    width, height = map(float, re.search(r"^Page\s+\d+\s+size:\s+([\d.]+) x ([\d.]+) pts", out, re.M).groups())
    if int(re.search(r"^Page\s+\d+\s+rot:\s+(\d+)", out, re.M)[1]) % 180:  # Um 90 oder 270 Grad gedreht
        width, height = height, width
    return pages, (width, height)


def pdf_info(pdf: str, page: int = 1) -> dict:
    """Seitenzahl und angezeigte Größe einer Seite: {"pages", "page", "width_pt", "height_pt"}.

    ValueError, wenn page nicht im Bereich 1..pages liegt. Gelesen wird mit pymupdf; nur wenn es die Datei nicht
    öffnen kann, fragt die Funktion Poppler (pdfinfo), sofern installiert.
    """
    path = str(resolve_drawing(pdf))
    try:
        with pymupdf.open(path) as doc:
            pages = doc.page_count
            rect = doc[page - 1].rect if 1 <= page <= pages else None  # CropBox mit angewendeter Drehung
            size = (rect.width, rect.height) if rect is not None else None
    except PYMUPDF_ERRORS:
        pages, size = _poppler_info(path, page)
    if size is None:
        raise ValueError(f"`page` {page} is out of range: {pdf} has {pages} page{'s' if pages != 1 else ''}.")
    return {"pages": pages, "page": page, "width_pt": size[0], "height_pt": size[1]}


def unit_box(box: Sequence[float] | None, name: str) -> Crop:
    """Ein Bereich (x0, y0, x1, y1) als Seitenanteile mit Ursprung oben links; bei None die ganze Seite.

    ValueError (mit dem Parameternamen `name`), wenn er nicht innerhalb der Seite liegt.
    """
    rect = (0.0, 0.0, 1.0, 1.0) if box is None else tuple(box)
    if len(rect) != 4 or not (0 <= rect[0] < rect[2] <= 1 and 0 <= rect[1] < rect[3] <= 1):
        raise ValueError(
            f"`{name}` must be (x0, y0, x1, y1) as page fractions with 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1; received: {box!r}."
        )
    return rect


def effective_dpi(info: dict, dpi: int, crop: Sequence[float] | None = None) -> int:
    """Die Auflösung, die render für eine mit pdf_info beschriebene Seite verwendet.

    Höchstens dpi und 600; zugleich so niedrig, dass die längste Bildseite höchstens 2000 px umfasst. Die Zeichnungen
    sind Vektoren, daher zeigt eine höhere Auflösung weiterhin echte Details.
    """
    if dpi < 1:
        raise ValueError(f"`dpi` must be at least 1; received: {dpi}.")
    x0, y0, x1, y1 = unit_box(crop, "crop")
    longest = max((x1 - x0) * info["width_pt"], (y1 - y0) * info["height_pt"])
    return min(dpi, MAX_DPI, math.floor(MAX_PX * 72 / longest))


def _pixel_box(info: dict, res: int, crop: Crop | None) -> tuple[int, int, int, int] | None:
    """Pixelrahmen (links, oben, Breite, Höhe) des Ausschnitts; gerundet werden seine Kanten, nicht seine Größe, damit
    benachbarte Ausschnitte nahtlos aneinanderliegen. None für die ganze Seite."""
    if crop is None:
        return None
    x0, y0, x1, y1 = unit_box(crop, "crop")
    pw, ph = info["width_pt"] * res / 72, info["height_pt"] * res / 72
    left, top = round(x0 * pw), round(y0 * ph)
    return left, top, max(1, round(x1 * pw) - left), max(1, round(y1 * ph) - top)


def _render_pymupdf(path: Path, page: int, res: int, box: tuple[int, int, int, int] | None) -> bytes:
    with pymupdf.open(path) as doc:
        scale = res / 72
        clip = None
        if box is not None:
            left, top, width, height = box
            eps = 1e-3  # die Ränder um einen Tausendstel Pixel nach innen: sonst rundet irect nach außen auf ein Pixel mehr
            clip = pymupdf.Rect((left + eps) / scale, (top + eps) / scale, (left + width - eps) / scale, (top + height - eps) / scale)
        return doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False).tobytes("png")


def _render_poppler(path: Path, page: int, res: int, box: tuple[int, int, int, int] | None) -> bytes:
    cmd = ["pdftoppm", "-png", "-singlefile", "-cropbox", "-f", str(page), "-l", str(page), "-r", str(res)]
    if box is not None:
        left, top, width, height = box
        cmd += ["-x", str(left), "-y", str(top), "-W", str(width), "-H", str(height)]
    with tempfile.TemporaryDirectory() as tmp:
        _poppler(*cmd, str(path), f"{tmp}/page")
        return Path(tmp, "page.png").read_bytes()


def render(pdf: str, page: int = 1, dpi: int = 100, crop: Crop | None = None) -> bytes:
    """PNG-Darstellung einer Seite oder ihres Ausschnitts mit effective_dpi.

    Gezeichnet wird mit pymupdf (ohne weitere Programme auf dem Rechner); scheitert es an einer Datei, ist Poppler
    (pdftoppm) der optionale Rückfall.
    """
    path = resolve_drawing(pdf)
    info = pdf_info(pdf, page)
    res = effective_dpi(info, dpi, crop)
    box = _pixel_box(info, res, crop)
    try:
        return _render_pymupdf(path, page, res, box)
    except PYMUPDF_ERRORS:
        return _render_poppler(path, page, res, box)

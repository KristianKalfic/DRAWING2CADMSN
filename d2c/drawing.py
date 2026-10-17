"""Zeichnungs-PDFs aus Sicht des Modells: Pfadprüfungen im Datenordner, Seiteninformationen und PNG-Darstellungen.

Seiten sind 1-basiert. Ein Ausschnitt ist (x0, y0, x1, y1) als Anteile der angezeigten Seite, Ursprung oben
links. Angezeigt wird die CropBox mit angewendeter Seitendrehung; genau das zeigen auch pdfinfo (nach Vertauschen
der Seitenlängen einer gedrehten Seite) und der Poppler-Renderer."""

import math
import os
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Sequence
from pathlib import Path

MAX_DPI = 600
MAX_PX = 2000  # Längste Seite einer gerenderten Abbildung

Crop = tuple[float, float, float, float]


def data_root() -> Path:
    """Der Ordner, aus dem Zeichnungen gelesen werden: D2C_DATA, bei jedem Aufruf neu eingelesen."""
    return Path(os.environ.get("D2C_DATA", "/data"))


def resolve_drawing(pdf: str) -> Path:
    """Die vorhandene PDF-Datei, die pdf relativ zum Datenordner bezeichnet.

        ValueError, wenn der Pfad aus dem Datenordner hinausführt (".." und Symlinks zählen nach ihrem Ziel) oder nicht
        auf .pdf endet. Fehlt die Datei, listet die Fehlermeldung die dort verfügbaren PDFs auf.
    """
    root = data_root().resolve()
    path = (root / pdf).resolve()
    if path.suffix.lower() != ".pdf" or not path.is_relative_to(root):
        raise ValueError(f"{pdf!r} ist keine .pdf-Datei im Datenordner.")
    if not path.is_file():
        # macOS-Einhängepunkte liefern Umlaute in zerlegter Form (NFD); vor der Anzeige werden sie wie bei der Eingabe wieder zusammengesetzt.
        found = sorted(
            unicodedata.normalize("NFC", str(p.relative_to(root)))
            for p in root.rglob("*")
            if p.suffix.lower() == ".pdf" and p.is_file()
        )
        raise ValueError(f"Zeichnung {pdf!r} nicht gefunden; verfügbar: {', '.join(found) or 'keine'}.")
    return path


def _poppler(*cmd: str) -> str:
    """Die Standardausgabe eines Poppler-Werkzeugs; bei einem Fehler wird eine ValueError-Ausnahme mit der Fehlermeldung ausgelöst."""
    done = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if done.returncode:
        raise ValueError(f"{cmd[0]} fehlgeschlagen: {done.stderr.strip()}")
    return done.stdout


def pdf_info(pdf: str, page: int = 1) -> dict:
    """Seitenzahl und angezeigte Größe einer Seite: {"pages", "page", "width_pt", "height_pt"}.

        ValueError, wenn page nicht im Bereich 1..pages liegt.
    """
    path = str(resolve_drawing(pdf))
    pages = int(re.search(r"^Pages:\s+(\d+)", _poppler("pdfinfo", path), re.M)[1])
    if not 1 <= page <= pages:
        raise ValueError(f"`page` {page} liegt außerhalb des gültigen Bereichs: {pdf} hat {pages} Seite{'n' if pages != 1 else ''}.")
    out = _poppler("pdfinfo", "-f", str(page), "-l", str(page), path)
    width, height = map(float, re.search(r"^Page\s+\d+\s+size:\s+([\d.]+) x ([\d.]+) pts", out, re.M).groups())
    if int(re.search(r"^Page\s+\d+\s+rot:\s+(\d+)", out, re.M)[1]) % 180:  # Um 90 oder 270 Grad gedreht
        width, height = height, width
    return {"pages": pages, "page": page, "width_pt": width, "height_pt": height}


def _crop_box(crop: Sequence[float] | None) -> Crop:
    """Der Ausschnitt als Rahmen; bei None die ganze Seite. ValueError, wenn er nicht innerhalb der Seite liegt."""
    box = (0.0, 0.0, 1.0, 1.0) if crop is None else tuple(crop)
    if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        raise ValueError(
            f"`crop` muss (x0, y0, x1, y1) als Seitenanteile mit 0 <= x0 < x1 <= 1 und "
            f"0 <= y0 < y1 <= 1 sein; erhalten: {crop!r}."
        )
    return box


def effective_dpi(info: dict, dpi: int, crop: Sequence[float] | None = None) -> int:
    """Die Auflösung, die render für eine mit pdf_info beschriebene Seite verwendet.

        Höchstens dpi und 600; zugleich so niedrig, dass die längste Bildseite höchstens 2000 px umfasst. Die Zeichnungen
        sind Vektoren, daher zeigt eine höhere Auflösung weiterhin echte Details.
    """
    if dpi < 1:
        raise ValueError(f"`dpi` muss mindestens 1 sein; erhalten: {dpi}.")
    x0, y0, x1, y1 = _crop_box(crop)
    longest = max((x1 - x0) * info["width_pt"], (y1 - y0) * info["height_pt"])
    return min(dpi, MAX_DPI, math.floor(MAX_PX * 72 / longest))


def render(pdf: str, page: int = 1, dpi: int = 100, crop: Crop | None = None) -> bytes:
    """PNG-Darstellung einer Seite oder ihres Ausschnitts mit effective_dpi."""
    path = resolve_drawing(pdf)
    info = pdf_info(pdf, page)
    x0, y0, x1, y1 = _crop_box(crop)
    res = effective_dpi(info, dpi, crop)
    cmd = ["pdftoppm", "-png", "-singlefile", "-cropbox", "-f", str(page), "-l", str(page), "-r", str(res)]
    if crop is not None:
        # Pixelrahmen des Ausschnitts; gerundet werden seine Kanten, nicht seine Größe, damit benachbarte Ausschnitte nahtlos aneinanderliegen.
        pw, ph = info["width_pt"] * res / 72, info["height_pt"] * res / 72
        left, top = round(x0 * pw), round(y0 * ph)
        width, height = max(1, round(x1 * pw) - left), max(1, round(y1 * ph) - top)
        cmd += ["-x", str(left), "-y", str(top), "-W", str(width), "-H", str(height)]
    with tempfile.TemporaryDirectory() as tmp:
        _poppler(*cmd, str(path), f"{tmp}/page")
        return Path(tmp, "page.png").read_bytes()

"""Drawing PDFs as the model sees them: path checks inside the data folder, page info, PNG renders.

Pages are 1-based. A crop is (x0, y0, x1, y1) as fractions of the displayed page, origin at the
top left. The displayed page is its CropBox with the page rotation applied, which is what pdfinfo
(after swapping the sides of a turned page) and the poppler renderer both show.
"""

import math
import os
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Sequence
from pathlib import Path

MAX_DPI = 600
MAX_PX = 2000  # longest side of a rendered image

Crop = tuple[float, float, float, float]


def data_root() -> Path:
    """The folder the drawings are read from: D2C_DATA, read on every call."""
    return Path(os.environ.get("D2C_DATA", "/data"))


def resolve_drawing(pdf: str) -> Path:
    """The existing PDF that `pdf` names, relative to the data folder.

    ValueError if the path leads outside the data folder (".." and symlinks count by where they
    end up) or does not end in .pdf; if the file is missing, the message lists the PDFs there are.
    """
    root = data_root().resolve()
    path = (root / pdf).resolve()
    if path.suffix.lower() != ".pdf" or not path.is_relative_to(root):
        raise ValueError(f"{pdf!r} is not a .pdf file inside the data folder")
    if not path.is_file():
        # macOS mounts list umlauts decomposed (NFD); show them composed, as a caller types them.
        found = sorted(
            unicodedata.normalize("NFC", str(p.relative_to(root)))
            for p in root.rglob("*")
            if p.suffix.lower() == ".pdf" and p.is_file()
        )
        raise ValueError(f"no such drawing {pdf!r}; available: {', '.join(found) or 'none'}")
    return path


def _poppler(*cmd: str) -> str:
    """stdout of a poppler tool; a failure becomes a ValueError carrying its message."""
    done = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if done.returncode:
        raise ValueError(f"{cmd[0]} failed: {done.stderr.strip()}")
    return done.stdout


def pdf_info(pdf: str, page: int = 1) -> dict:
    """Page count and displayed size of one page: {"pages", "page", "width_pt", "height_pt"}.

    ValueError if the page is not in 1..pages.
    """
    path = str(resolve_drawing(pdf))
    pages = int(re.search(r"^Pages:\s+(\d+)", _poppler("pdfinfo", path), re.M)[1])
    if not 1 <= page <= pages:
        raise ValueError(f"page {page} is out of range: {pdf} has {pages} page{'s' if pages != 1 else ''}")
    out = _poppler("pdfinfo", "-f", str(page), "-l", str(page), path)
    width, height = map(float, re.search(r"^Page\s+\d+\s+size:\s+([\d.]+) x ([\d.]+) pts", out, re.M).groups())
    if int(re.search(r"^Page\s+\d+\s+rot:\s+(\d+)", out, re.M)[1]) % 180:  # turned by 90 or 270
        width, height = height, width
    return {"pages": pages, "page": page, "width_pt": width, "height_pt": height}


def _crop_box(crop: Sequence[float] | None) -> Crop:
    """The crop as a box, the whole page for None; ValueError unless it lies inside the page."""
    box = (0.0, 0.0, 1.0, 1.0) if crop is None else tuple(crop)
    if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        raise ValueError(
            f"crop must be (x0, y0, x1, y1) as page fractions with 0 <= x0 < x1 <= 1 and "
            f"0 <= y0 < y1 <= 1, got {crop!r}"
        )
    return box


def effective_dpi(info: dict, dpi: int, crop: Sequence[float] | None = None) -> int:
    """The resolution `render` uses for a page described by `pdf_info`.

    At most dpi and 600, and low enough for the longest image side to stay within 2000 px. The
    drawings are vectors, so a higher resolution still shows real detail.
    """
    if dpi < 1:
        raise ValueError(f"dpi must be at least 1, got {dpi}")
    x0, y0, x1, y1 = _crop_box(crop)
    longest = max((x1 - x0) * info["width_pt"], (y1 - y0) * info["height_pt"])
    return min(dpi, MAX_DPI, math.floor(MAX_PX * 72 / longest))


def render(pdf: str, page: int = 1, dpi: int = 100, crop: Crop | None = None) -> bytes:
    """PNG of one page, or of a crop of it, at `effective_dpi`."""
    path = resolve_drawing(pdf)
    info = pdf_info(pdf, page)
    x0, y0, x1, y1 = _crop_box(crop)
    res = effective_dpi(info, dpi, crop)
    cmd = ["pdftoppm", "-png", "-singlefile", "-cropbox", "-f", str(page), "-l", str(page), "-r", str(res)]
    if crop is not None:
        # Pixel box of the crop; its edges are rounded, not its size, so neighbouring crops tile.
        pw, ph = info["width_pt"] * res / 72, info["height_pt"] * res / 72
        left, top = round(x0 * pw), round(y0 * ph)
        width, height = max(1, round(x1 * pw) - left), max(1, round(y1 * ph) - top)
        cmd += ["-x", str(left), "-y", str(top), "-W", str(width), "-H", str(height)]
    with tempfile.TemporaryDirectory() as tmp:
        _poppler(*cmd, str(path), f"{tmp}/page")
        return Path(tmp, "page.png").read_bytes()

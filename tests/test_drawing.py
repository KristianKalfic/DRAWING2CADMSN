import shutil
import struct
import subprocess
import unicodedata

import pytest
from helpers import DEMO_PDF

from d2c.drawing.pages import pdf_info, render, resolve_drawing

NAME = "Demo Teil (A)/demo drawing.pdf"  # Leerzeichen und Klammern müssen jeden Aufruf eines Unterprozesses unverändert überstehen.


@pytest.fixture
def demo_root(tmp_path, monkeypatch):
    """Datenordner mit einer Kopie der Demo-Zeichnung."""
    (tmp_path / "Demo Teil (A)").mkdir()
    shutil.copy(DEMO_PDF, tmp_path / NAME)
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    return tmp_path


def png_size(png: bytes) -> tuple[int, int]:
    """Breite und Höhe aus dem IHDR-Abschnitt."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", png[16:24])


def one_page_pdf(page_entries: str) -> bytes:
    """Ein leeres PDF mit einer Seite, deren Seitendictionary `page_entries` enthält (MediaBox, CropBox, Rotate)."""
    bodies = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R {page_entries} >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(bodies, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = b"xref\n0 4\n0000000000 65535 f \n" + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + xref + f"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{len(out)}\n%%EOF\n".encode()


def test_demo_drawing_has_text_layer():
    text = subprocess.run(["pdftotext", str(DEMO_PDF), "-"], capture_output=True, encoding="utf-8", check=True).stdout
    assert "Ø16 H7" in text
    assert "ISO 2768-m" in text


def test_pdf_info_pages_and_size(demo_root):
    info = pdf_info(NAME)
    assert info["pages"] == 2
    assert info["page"] == 1
    assert info["width_pt"] == pytest.approx(595.28, abs=0.01)
    assert info["height_pt"] == pytest.approx(841.89, abs=0.01)


def test_render_full_page_size(demo_root):
    width, _ = png_size(render(NAME, dpi=100))
    assert width == pytest.approx(827, abs=1)


def test_render_path_with_spaces(demo_root):
    width, height = png_size(render(NAME, page=2))
    assert width > 0 and height > 0


def test_page_out_of_range(demo_root):
    with pytest.raises(ValueError, match="2 pages"):
        render(NAME, page=3)


def test_render_crop_size(demo_root):
    width, _ = png_size(render(NAME, dpi=100, crop=(0, 0, 0.5, 0.5)))
    assert width == pytest.approx(413, abs=1)


def test_render_caps_long_side_2000(demo_root):
    longest = max(png_size(render(NAME, dpi=600)))
    assert 1900 < longest <= 2000  # Die Begrenzung senkt die dpi-Zahl auf den für 2000 Pixel nötigen Wert, aber nicht weiter.


def test_render_rejects_outside_data(demo_root):
    (demo_root / "link.pdf").symlink_to(DEMO_PDF)  # Ein PDF außerhalb des Datenordners
    for name in ("../../etc/passwd", "link.pdf", str(DEMO_PDF)):
        with pytest.raises(ValueError, match="in the data folder"):
            render(name)


def test_missing_lists_available(demo_root):
    composed = "Stützplatte"
    decomposed = unicodedata.normalize("NFD", composed)  # so, wie der macOS-Einhängepunkt ihn auflistet
    (demo_root / decomposed).mkdir()
    shutil.copy(DEMO_PDF, demo_root / decomposed / "plate.pdf")
    with pytest.raises(ValueError) as err:
        resolve_drawing("nope.pdf")
    assert NAME in str(err.value)
    assert f"{composed}/plate.pdf" in str(err.value)


def test_rotated_cropped_page_uses_displayed_size(tmp_path, monkeypatch):
    """`pdfinfo` meldet die nicht gedrehte CropBox; das Bild und damit die Ausschnittsanteile richten sich nach der angezeigten Seite."""
    (tmp_path / "turned.pdf").write_bytes(one_page_pdf("/MediaBox [0 0 400 300] /CropBox [100 50 300 200] /Rotate 90"))
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    info = pdf_info("turned.pdf")
    assert (info["width_pt"], info["height_pt"]) == (150, 200)
    assert png_size(render("turned.pdf", dpi=72)) == (150, 200)
    assert png_size(render("turned.pdf", dpi=72, crop=(0, 0, 0.5, 1))) == (75, 200)


def test_without_poppler_everything_still_works(demo_root, monkeypatch):
    """Ohne pdfinfo und pdftoppm im PATH (frischer Rechner mit start.sh) liefern Seiteninfo und Darstellung dasselbe."""
    with_poppler = png_size(render(NAME, dpi=100, crop=(0, 0, 0.5, 0.5)))
    monkeypatch.setenv("PATH", "/nonexistent")
    assert pdf_info(NAME)["pages"] == 2
    width, height = png_size(render(NAME, dpi=100))
    assert width == 827 and height == pytest.approx(1169, abs=1)  # pymupdf rundet die Höhe von 1169,3 auf
    assert png_size(render(NAME, dpi=100, crop=(0, 0, 0.5, 0.5))) == with_poppler


def test_poppler_is_the_fallback_when_pymupdf_cannot_open(demo_root, monkeypatch):
    """Meldet pymupdf einen Fehler, übernimmt Poppler; fehlt auch das, bleibt ein verständlicher ValueError."""
    from d2c.drawing import pages as drawing

    def broken(*args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(drawing.pymupdf, "open", broken)
    assert pdf_info(NAME)["pages"] == 2
    assert png_size(render(NAME, dpi=100))[0] == 827
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(ValueError, match="Poppler is optional"):
        render(NAME)


def test_pymupdf_and_poppler_agree_on_the_crop_geometry(demo_root):
    from d2c.drawing import pages as drawing

    info = pdf_info(NAME)
    for crop in (None, (0.25, 0.1, 0.75, 0.6), (0, 0.5, 1, 1)):
        res = drawing.effective_dpi(info, 100, crop)
        box = drawing._pixel_box(info, res, crop)
        path = drawing.resolve_drawing(NAME)
        sizes = [png_size(f(path, 1, res, box)) for f in (drawing._render_pymupdf, drawing._render_poppler)]
        assert sizes[0] == sizes[1], crop

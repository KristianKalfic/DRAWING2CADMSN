from pathlib import Path

import pytest

from d2c.text import items_from_xml, parse_dimension_text, read_text

DEMO = Path(__file__).parent / "fixtures" / "demo_drawing.pdf"
TITLE_BLOCK = (0.467, 0.838, 0.962, 0.973)

NO_DIMENSION = dict(prefix=None, count=None, nominal=None, tol_upper=None, tol_lower=None,
                    fit=None, envelope=False, thread=None)


def parsed(**fields) -> dict:
    """The full result of parse_dimension_text with the given fields set."""
    return {**NO_DIMENSION, **fields}


def test_parse_dimension_text_cases():
    cases = {
        "Ø6 h10 Ⓔ": parsed(prefix="Ø", nominal=6.0, fit="h10", envelope=True),
        "87,5 -0,2": parsed(nominal=87.5, tol_upper=0.0, tol_lower=-0.2),
        "37,5 +0,1": parsed(nominal=37.5, tol_upper=0.1, tol_lower=0.0),
        "32±0,08": parsed(nominal=32.0, tol_upper=0.08, tol_lower=-0.08),
        "55 ±,1": parsed(nominal=55.0, tol_upper=0.1, tol_lower=-0.1),  # leading zero missing
        "R 0,3": parsed(prefix="R", nominal=0.3),
        "M10x0,75": parsed(prefix="M", nominal=10.0, thread="M10x0.75"),
        "4x Ø6,6": parsed(prefix="Ø", count=4, nominal=6.6),
        "Ø10,5 +1,3": parsed(prefix="Ø", nominal=10.5, tol_upper=1.3, tol_lower=0.0),
        # joined from several text pieces: spacing varies, the value must not
        "Ø 17 -0,2": parsed(prefix="Ø", nominal=17.0, tol_upper=0.0, tol_lower=-0.2),
        "Ø 10h10 Ⓔ": parsed(prefix="Ø", nominal=10.0, fit="h10", envelope=True),
        "3 H10 Ⓔ": parsed(nominal=3.0, fit="H10", envelope=True),
        "Ø16 H7": parsed(prefix="Ø", nominal=16.0, fit="H7"),
        "M 12 x 1,5": parsed(prefix="M", nominal=12.0, thread="M12x1.5"),
        "M8 (3x)": parsed(prefix="M", count=3, nominal=8.0, thread="M8"),
        "23,216±0,01": parsed(nominal=23.216, tol_upper=0.01, tol_lower=-0.01),
        "10 +0,3 +0,1": parsed(nominal=10.0, tol_upper=0.3, tol_lower=0.1),
        "10 -0,1 +0,2": parsed(nominal=10.0, tol_upper=0.2, tol_lower=-0.1),  # the larger one is the upper
    }
    # text that is not a dimension must not become one
    for text in ("", "Ø", "H10", "A-B", "+0,2 -0,1", "↗ 0,1 A-B", "Schnitt A-A", "0,5 x 45°", "45°",
                 "ISO 13715 +0,4", "DIN 332 - A 2,0 x 4,25", "0,1 CZ A-B", "6 x 10°", "Ø10 ±", "2,0 x 4,25",
                 "2x Ø6 (3x)", "M10x0,75 6H"):
        cases[text] = None
    for text, expected in cases.items():
        assert parse_dimension_text(text) == expected, text


WIDTH, HEIGHT = 1500, 1000  # of the synthetic pdftohtml pages, in its units (1.5 per pt)


def xml_page(texts: list[str], fonts: dict[int, tuple[str, int]]) -> str:
    """A pdftohtml -xml document; fonts maps a font id to (family, size)."""
    specs = "\n".join(f'\t<fontspec id="{i}" size="{size}" family="{family}" color="#000000"/>'
                      for i, (family, size) in fonts.items())
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE pdf2xml SYSTEM "pdf2xml.dtd">\n\n'
            f'<pdf2xml producer="poppler" version="25.03.0">\n'
            f'<page number="1" position="absolute" top="0" left="0" height="{HEIGHT}" width="{WIDTH}">\n'
            f'{specs}\n' + "\n".join(texts) + "\n</page>\n</pdf2xml>\n")


def piece(font: int, top: int, left: int, text: str, width: int = 0, height: int = 21) -> str:
    return f'<text top="{top}" left="{left}" width="{width}" height="{height}" font="{font}">{text}</text>'


FONTS = {0: ("AAAAAA+SolidEdgeISO", 20), 1: ("AAAAAA+SolidEdgeISO", 17),
         2: ("BBBBBB+SolidEdgeISO1Symbols", 20), 3: ("CCCCCC+SolidEdgeISOGDTSymbols", 20),
         4: ("DDDDDD+Helvetica", 20), 5: ("AAAAAA+SolidEdgeISO", 41), 6: ("EEEEEE+SolidEdgeISO,Bold", 17)}


def test_symbol_mapping_only_in_symbol_fonts():
    # the same letters in four fonts, far apart so that no two pieces join into one text
    xml = xml_page([
        piece(4, 100, 100, "O", width=14),   # Helvetica: stays O
        piece(2, 300, 100, "O", width=14),   # ISO1Symbols: diameter sign
        piece(2, 500, 100, "L", width=14),   # ISO1Symbols has no L: stays L
        piece(3, 700, 100, "L", width=14),   # GDTSymbols: envelope requirement
        piece(4, 900, 100, "L", width=14),   # Helvetica: section letter L
        piece(2, 100, 600, "u", width=14),   # ISO1Symbols: plus-minus
        piece(0, 300, 600, "u", width=14),   # normal font: stays u
    ], FONTS)
    got = [(i["text"], i["font"]) for i in sorted(items_from_xml(xml), key=lambda i: (i["x0"], i["y0"]))]
    assert got == [("O", "Helvetica"), ("Ø", "SolidEdgeISO1Symbols"), ("L", "SolidEdgeISO1Symbols"),
                   ("Ⓔ", "SolidEdgeISOGDTSymbols"), ("L", "Helvetica"), ("±", "SolidEdgeISO1Symbols"),
                   ("u", "SolidEdgeISO")]  # font names come without the subset prefix


def test_pieces_merge_into_lines():
    xml = xml_page([
        # vertical fit with envelope, split into four pieces by kerning and font changes
        piece(2, 600, 300, "O", height=20), piece(0, 580, 300, "8h"), piece(0, 560, 300, "10"),
        piece(3, 541, 300, "L", height=29),
        # vertical diameter with its deviation in the smaller tolerance font
        piece(2, 700, 400, "O", height=20), piece(0, 680, 400, "18"), piece(1, 665, 400, "-0", height=18),
        piece(1, 652, 400, ",3", height=18),
        # stacked: the raised deviation is a separate item next to its dimension
        piece(0, 100, 700, "40", width=16), piece(1, 88, 720, "+0,1", width=19, height=18),
        # inline deviation on the baseline joins its dimension
        piece(0, 200, 700, "70", width=16), piece(1, 203, 719, "-0,1", width=19, height=18),
        # table cells far apart stay separate; a bold header nests its text in <b>
        piece(1, 900, 1000, "Ø 6 h10", width=41, height=17), piece(1, 900, 1100, "Ø 5,952", width=41, height=17),
        piece(6, 950, 1000, "<b>Paßmaß</b>", width=44, height=17),
        # one thread note cut at arbitrary characters keeps its spaces and loses none
        piece(0, 400, 500, "M 1"), piece(0, 380, 500, "2 x"), piece(0, 361, 500, " 1"), piece(0, 349, 500, ",5"),
        # a wide gap inside one font is a word space
        piece(0, 800, 100, "3x", width=13), piece(0, 801, 127, "35", width=14),
        # a view letter (much larger) next to a dimension is another text
        piece(5, 600, 1200, "K", width=16, height=42), piece(0, 617, 1220, "27 ±0,1", width=43),
        # blank pieces carry nothing
        piece(0, 50, 50, " ", width=5),
        # rotated text near the page edge: its estimated end would leave the page
        piece(0, 10, 1400, "R 0,3"),
        # upside-down text is reported with a negative width, its box ends at left
        piece(0, 500, 1300, "Scale", width=-74),
        # text running over the right page edge
        piece(0, 300, 1480, "Edge", width=40),
        # a horizontal and a rotated text never join, even when their coordinates line up
        piece(0, 100, -10, "H1", width=30), piece(0, 120, 117, "V1"),
    ], FONTS)
    found = items_from_xml(xml)
    assert found == sorted(found, key=lambda i: (i["y0"], i["x0"]))  # top to bottom, then left to right
    items = {i["text"]: i for i in found}
    assert sorted(items) == sorted(["Ø8h10 Ⓔ", "Ø18 -0,3", "40", "+0,1", "70 -0,1", "Ø 6 h10", "Ø 5,952",
                                    "Paßmaß", "M 12 x 1,5", "3x 35", "K", "27 ±0,1", "R 0,3", "Scale", "Edge", "H1", "V1"])
    assert items["R 0,3"]["y0"] == 0 and items["R 0,3"]["y1"] > 0  # clamped to the page
    assert items["Edge"]["x1"] == 1 and items["Edge"]["x0"] < 1
    assert (items["Scale"]["x0"], items["Scale"]["x1"]) == (round(1226 / WIDTH, 4), round(1300 / WIDTH, 4))
    assert {text for text, item in items.items() if item["vertical"]} == {
        "Ø8h10 Ⓔ", "Ø18 -0,3", "M 12 x 1,5", "R 0,3", "V1"}
    assert items["Ø8h10 Ⓔ"]["parsed"] == parsed(prefix="Ø", nominal=8.0, fit="h10", envelope=True)
    assert items["Ø18 -0,3"]["parsed"]["tol_lower"] == -0.3
    assert items["70 -0,1"]["parsed"]["tol_lower"] == -0.1
    assert items["M 12 x 1,5"]["parsed"]["thread"] == "M12x1.5"
    assert items["3x 35"]["parsed"] == parsed(count=3, nominal=35.0)
    assert items["27 ±0,1"]["parsed"]["tol_upper"] == 0.1
    # the stacked deviation is its own item next to the dimension, which stays without a tolerance
    assert items["+0,1"]["parsed"] is None and items["40"]["parsed"]["tol_upper"] is None
    # the font of an item is its first piece outside a symbol font: here the number, not the diameter sign
    fit = items["Ø8h10 Ⓔ"]
    assert (fit["font"], fit["size"]) == ("SolidEdgeISO", round(20 / 1.5, 1))
    # the inflated symbol box (height 29) does not widen the vertical item beyond its number pieces (height 21)
    assert (fit["x1"] - fit["x0"]) * WIDTH == pytest.approx(21, abs=0.1)
    assert fit["y0"] < fit["y1"]


def write_pdf(path: Path, content: str, rotate: int = 0) -> Path:
    """A 200 x 300 pt page in raw PDF syntax: Helvetica with WinAnsiEncoding as /F1."""
    stream = content.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 300] /Rotate {rotate} /Contents 4 0 R "
         "/Resources << /Font << /F1 5 0 R >> >> >>").encode(),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def test_read_text_minimal_pdf(tmp_path):
    pdf = write_pdf(tmp_path / "minimal.pdf", (
        "BT /F1 12 Tf 20 270 Td (Scale 1:1) Tj ET\n"            # top left
        "BT /F1 12 Tf 100 180 Td (\xd816 H7) Tj ET\n"            # 0xD8 is the diameter sign in WinAnsi
        "BT /F1 12 Tf 0 1 -1 0 150 100 Tm (R 0,3) Tj ET\n"       # rotated, reads bottom to top
        "BT /F1 12 Tf 100 20 Td (4x 6,6) Tj ET\n"                # bottom right
    ))
    items = {i["text"]: i for i in read_text(pdf)}
    assert sorted(items) == ["4x 6,6", "R 0,3", "Scale 1:1", "Ø16 H7"]
    for item in items.values():
        assert set(item) == {"text", "x0", "y0", "x1", "y1", "font", "size", "vertical", "parsed"}
        assert 0 <= item["x0"] < item["x1"] <= 1 and 0 <= item["y0"] < item["y1"] <= 1
        assert item["font"] == "Helvetica" and item["size"] == pytest.approx(12, abs=1)
    # origin top left: the title block text printed at the top of the page has the smallest y
    scale, hole, radius, count = (items[t] for t in ("Scale 1:1", "Ø16 H7", "R 0,3", "4x 6,6"))
    assert scale["y0"] < hole["y0"] < radius["y0"] < count["y0"]
    assert scale["x0"] == pytest.approx(0.10, abs=0.02) and scale["y0"] == pytest.approx(32 / 450, abs=0.02)
    assert count["x0"] == pytest.approx(0.50, abs=0.02) and count["y0"] == pytest.approx(407 / 450, abs=0.02)
    assert [i["vertical"] for i in (scale, hole, radius, count)] == [False, False, True, False]
    # a rotated text starts at the bottom (200 pt from the top) and runs upwards; its baseline is at 150 pt
    assert radius["y1"] == pytest.approx(200 / 300, abs=0.02)
    assert radius["x0"] < 150 / 200 < radius["x1"]
    assert hole["parsed"] == parsed(prefix="Ø", nominal=16.0, fit="H7")
    assert count["parsed"] == parsed(prefix=None, count=4, nominal=6.6)
    assert scale["parsed"] is None

    # a region keeps the items whose box overlaps it
    def texts(region):
        return sorted(i["text"] for i in read_text(pdf, region=region))

    assert texts((0, 0.8, 1, 1)) == ["4x 6,6"]
    assert texts((0, 0, 0.5, 0.2)) == ["Scale 1:1"]
    assert texts((0.6, 0.5, 1, 0.7)) == ["R 0,3"]
    assert texts((0.45, 0.3, 0.8, 0.45)) == ["Ø16 H7"]
    assert texts((0, 0, 1, 1)) == sorted(items)
    assert texts((0.2, 0, 1, 0.2)) == ["Scale 1:1"]  # overlaps the text from x 0.10 to 0.35
    assert texts((0, 0.3, 0.55, 0.45)) == ["Ø16 H7"]
    # an item that only touches the region edge is not inside
    assert texts((0, 0.3, 0.5, 0.45)) == []
    assert texts((0, 0.3, 1, hole["y0"])) == []
    assert texts((0, hole["y1"], 1, 0.5)) == []
    for bad in ((0.5, 0, 0.2, 1), (0, 0.5, 1, 0.5), (-0.1, 0, 1, 1), (0, 0, 1.1, 1), (0, 0, 1)):
        with pytest.raises(ValueError, match="region"):
            read_text(pdf, region=bad)
    for page in (0, 2):
        with pytest.raises(ValueError, match="page"):
            read_text(pdf, page=page)
    with pytest.raises(ValueError, match="missing.pdf"):
        read_text(tmp_path / "missing.pdf")
    # a page without a text layer is a valid result
    assert read_text(write_pdf(tmp_path / "blank.pdf", "0 0 100 100 re S\n")) == []


def test_read_text_rotated_page(tmp_path):
    # a portrait page with /Rotate 90 shows a 300 x 200 pt landscape sheet (like the A3 BCT drawings);
    # the text matrix cancels the rotation, so the text reads horizontally on the displayed page
    pdf = write_pdf(tmp_path / "rotated.pdf", (
        "BT /F1 12 Tf 0 1 -1 0 30 20 Tm (Scale 1:1) Tj ET\n"     # displayed: 20 pt from left, 30 pt from top
        "BT /F1 12 Tf 0 1 -1 0 180 200 Tm (4x 6,6) Tj ET\n"      # displayed: 200 pt from left, 180 pt from top
    ), rotate=90)
    items = {i["text"]: i for i in read_text(pdf)}
    assert sorted(items) == ["4x 6,6", "Scale 1:1"]
    scale, count = items["Scale 1:1"], items["4x 6,6"]
    assert not scale["vertical"] and not count["vertical"]
    assert (scale["x0"], scale["y0"]) == pytest.approx((20 / 300, 21.4 / 200), abs=0.015)
    assert (count["x0"], count["y0"]) == pytest.approx((200 / 300, 171.4 / 200), abs=0.015)
    assert scale["x1"] == pytest.approx((20 + 50) / 300, abs=0.015)  # text width 50 pt along the displayed x axis
    assert [i["text"] for i in read_text(pdf, region=(0.5, 0.5, 1, 1))] == ["4x 6,6"]


def test_demo_text_items():
    by_text = {i["text"]: i for i in read_text(DEMO)}
    assert by_text["Ø16 H7"]["parsed"]["fit"] == "H7"
    holes = by_text["4x Ø6,6"]["parsed"]
    assert (holes["count"], holes["nominal"]) == (4, 6.6)
    assert all(0 <= i[k] <= 1 for i in by_text.values() for k in ("x0", "y0", "x1", "y1"))


def test_region_filter():
    texts = [i["text"] for i in read_text(DEMO, region=TITLE_BLOCK)]
    assert "Maßstab 1:1" in texts
    assert "Ø30" not in texts

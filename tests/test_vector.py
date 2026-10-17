"""Vector measurement on synthetic sheets built with pymupdf."""

import math
from pathlib import Path

import pymupdf
import pytest

from d2c.vector import extract, suggest_scale, verify

DEMO = Path(__file__).parent / "fixtures" / "demo_drawing.pdf"
DEMO_FRONT_VIEW = (0.21, 0.202, 0.781, 0.333)  # regions of the demo drawing, page fractions
DEMO_TOP_VIEW = (0.21, 0.404, 0.781, 0.69)

MM = 72 / 25.4  # PDF points per mm
EDGE = 1.417  # stroke width (pt) of the visible edges in the BCT drawings
THIN = 0.35  # centre lines and the like
SHEET_W, SHEET_H = 420, 297  # A3 landscape, mm


def _sheet(path, draw, rotation=0):
    """Save a blank A3 sheet on which draw(page) has put the geometry."""
    doc = pymupdf.open()
    page = doc.new_page(width=SHEET_W * MM, height=SHEET_H * MM)
    draw(page)
    page.set_rotation(rotation)
    doc.save(path)
    return path


def _plate_pdf(path, scale, rotation=0):
    """80 x 50 mm plate with a dia 30 hole at (30, 25) and a dia 16 hole at (65, 40), drawn at
    `scale`. Its lower-left corner sits 60 mm right of and 80 mm above the sheet's lower-left."""

    def at(x, y):  # plate mm, y up -> PDF point, y down
        return pymupdf.Point((60 + x * scale) * MM, (SHEET_H - 80 - y * scale) * MM)

    def draw(page):
        page.draw_rect(pymupdf.Rect(at(0, 50), at(80, 0)), width=EDGE)
        page.draw_circle(at(30, 25), 15 * scale * MM, width=EDGE)
        page.draw_circle(at(65, 40), 8 * scale * MM, width=EDGE)
        page.draw_line(at(30, -5), at(30, 55), width=THIN)  # centre line, no visible edge

    return _sheet(path, draw, rotation)


def _region(x0, y0, x1, y1):
    """Box in sheet mm from the lower-left corner -> region fractions, origin top-left."""
    return (x0 / SHEET_W, 1 - y1 / SHEET_H, x1 / SHEET_W, 1 - y0 / SHEET_H)


@pytest.mark.parametrize("scale", [1, 2])
def test_synthetic_circles_and_extent(tmp_path, scale):
    res = extract(_plate_pdf(tmp_path / "plate.pdf", scale), scale=scale)
    assert res["scale_used"] == scale
    assert res["extent_mm"] == pytest.approx([80, 50], abs=0.01)
    assert res["extent_basis"] == "largest contour"
    hole30, hole16 = res["circles"]  # largest first
    assert hole30["d_mm"] == pytest.approx(30, abs=0.01)
    assert hole16["d_mm"] == pytest.approx(16, abs=0.01)
    # centres relative to the plate's lower-left corner: x right, y up, model mm
    x0, y0, *_ = res["contours"][0]["bbox_mm"]
    assert (hole30["cx"] - x0, hole30["cy"] - y0) == pytest.approx((30, 25), abs=0.01)
    assert (hole16["cx"] - x0, hole16["cy"] - y0) == pytest.approx((65, 40), abs=0.01)
    # the widest stroke class carries the edges, the thin centre line is no edge
    assert [(s["width"], s["visible"]) for s in res["styles"]] == [(EDGE, True), (THIN, False)]


def test_verify_claims(tmp_path):
    pdf = _plate_pdf(tmp_path / "plate.pdf", 2)
    res = extract(pdf, scale=2)

    verdicts = verify(res, ["d=16", "d=30", "r=8", "L=80", "L=50", "d=16,0"])
    assert [v["status"] for v in verdicts] == ["ok"] * 6
    assert all(abs(v["deviation_mm"]) <= 0.01 for v in verdicts)
    assert verdicts[0]["n_candidates_within_tol"] == 1

    (wrong,) = verify(res, ["d=17"])  # the closest circle is the dia 16 one
    assert wrong["status"] == "MISMATCH"
    assert wrong["measured_mm"] == pytest.approx(16, abs=0.01)
    assert wrong["deviation_mm"] == pytest.approx(-1, abs=0.01)
    assert wrong["n_candidates_within_tol"] == 0
    assert verify(res, ["d=17"], tol=1.5)[0]["status"] == "ok"

    only_an_edge = extract(pdf, region=_region(50, 120, 70, 140), scale=2)
    (none,) = verify(only_an_edge, ["d=16"])
    assert none["status"] == "no_candidates"
    assert none["measured_mm"] is None


@pytest.mark.parametrize("claim", ["x=3", "d19", "d=", "d=-1", ""])
def test_verify_rejects_malformed_claims(tmp_path, claim):
    res = extract(_plate_pdf(tmp_path / "plate.pdf", 1))
    with pytest.raises(ValueError, match="claim"):
        verify(res, [claim])


def test_region_filters(tmp_path):
    pdf = _plate_pdf(tmp_path / "plate.pdf", 2)

    blank = extract(pdf, region=_region(260, 230, 400, 290), scale=2)
    assert blank["circles"] == [] and blank["arcs"] == [] and blank["lines"] == []
    assert blank["contours"] == []
    assert blank["extent_mm"] is None and blank["extent_basis"] is None

    hole = extract(pdf, region=_region(170, 140, 210, 180), scale=2)  # the dia 16 hole only
    (circle,) = hole["circles"]
    assert circle["d_mm"] == pytest.approx(16, abs=0.01)
    assert hole["extent_mm"] == pytest.approx([16, 16], abs=0.01)
    # the origin is the region's lower-left corner: the centre lies 20 sheet mm = 10 model mm in
    assert (circle["cx"], circle["cy"]) == pytest.approx((10, 10), abs=0.01)


def test_rotated_page(tmp_path):
    pdf = _plate_pdf(tmp_path / "plate.pdf", 1, rotation=90)
    assert extract(pdf)["extent_mm"] == pytest.approx([50, 80], abs=0.01)  # as displayed

    # regions are fractions of the displayed page: a box around the dia 16 hole
    page = pymupdf.open(pdf)[0]
    c = pymupdf.Point(125 * MM, (SHEET_H - 120) * MM) * page.rotation_matrix
    w, h, r = page.rect.width, page.rect.height, 12 * MM
    hole = extract(pdf, region=((c.x - r) / w, (c.y - r) / h, (c.x + r) / w, (c.y + r) / h))
    (circle,) = hole["circles"]
    assert circle["d_mm"] == pytest.approx(16, abs=0.01)
    assert (circle["cx"], circle["cy"]) == pytest.approx((12, 12), abs=0.01)


def test_bezier_arc(tmp_path):
    def draw(page):
        centre, start = pymupdf.Point(100 * MM, 100 * MM), pymupdf.Point(112 * MM, 100 * MM)
        page.draw_sector(centre, start, 90, fullSector=False, width=EDGE)

    res = extract(_sheet(tmp_path / "arc.pdf", draw))
    (arc,) = res["arcs"]
    assert res["circles"] == []
    assert arc["r_mm"] == pytest.approx(12, abs=0.01)
    assert arc["sweep"] == pytest.approx(90, abs=0.01)
    assert verify(res, ["r=12"])[0]["status"] == "ok"


def test_tessellated_circle_and_arc(tmp_path):
    """Exports without Bezier curves approximate circles by polylines: a closed polyline is a
    circle, an open one an arc (here 240 degrees across the 0 degree direction)."""

    def ring(cx, cy, r, start, end, steps):  # polyline vertices on a circle, mm -> PDF point
        angles = [math.radians(start + (end - start) * i / steps) for i in range(steps + 1)]
        return [
            pymupdf.Point((cx + r * math.cos(a)) * MM, (cy + r * math.sin(a)) * MM) for a in angles
        ]

    def draw(page):
        page.draw_polyline(ring(100, 100, 12, 0, 360, 24), width=EDGE)
        page.draw_polyline(ring(200, 100, 20, 300, 540, 16), width=EDGE)

    res = extract(_sheet(tmp_path / "poly.pdf", draw))
    (circle,) = res["circles"]
    (arc,) = res["arcs"]
    assert circle["d_mm"] == pytest.approx(24, abs=0.01)
    assert arc["r_mm"] == pytest.approx(20, abs=0.01)
    assert arc["sweep"] == pytest.approx(240, abs=0.05)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"page": 0}, "page"),
        ({"page": 2}, "page"),
        ({"region": (0.6, 0.1, 0.4, 0.9)}, "region"),
        ({"region": (0.1, 0.1, 1.2, 0.9)}, "region"),
        ({"region": (0.1, 0.1, 0.9)}, "region"),
        ({"scale": 0}, "scale"),
        ({"scale": float("nan")}, "scale"),
        ({"edge_width": "thick"}, "edge_width"),
    ],
)
def test_extract_rejects_bad_arguments(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        extract(_plate_pdf(tmp_path / "plate.pdf", 1), **kwargs)


def test_page_without_vector_geometry(tmp_path):
    """A scan or blank page has nothing to measure: empty results, no error."""
    pdf = _sheet(tmp_path / "blank.pdf", lambda page: None)
    res = extract(pdf)
    assert res["styles"] == [] and res["circles"] == [] and res["extent_mm"] is None
    assert verify(res, ["d=5"])[0]["status"] == "no_candidates"
    assert suggest_scale(pdf, [10, 20]) == []


PLATE_DIMS = [80, 50, 30, 16, 35, 15]  # plate size, hole diameters, hole spacing in x and y


def test_suggest_scale_synthetic(tmp_path):
    best = suggest_scale(_plate_pdf(tmp_path / "plate.pdf", 2), PLATE_DIMS)[0]
    assert best["scale"] == pytest.approx(2.0, abs=0.001)
    assert best["standard"] == 2.0
    assert best["votes"] == len(PLATE_DIMS)


def test_suggest_scale_keeps_non_standard_ratio(tmp_path):
    """0.75 is no standard scale. It must still beat chance hits on standard scales."""
    best = suggest_scale(_plate_pdf(tmp_path / "plate.pdf", 0.75), PLATE_DIMS)[0]
    assert best["scale"] == pytest.approx(0.75, abs=0.001)
    assert best["standard"] is None
    assert best["votes"] == len(PLATE_DIMS)


def test_suggest_scale_rejects_non_positive_value(tmp_path):
    with pytest.raises(ValueError, match="printed"):
        suggest_scale(_plate_pdf(tmp_path / "plate.pdf", 1), [80, 0])


def test_demo_front_view_thickness():
    """The plate thickness is not dimensioned on the demo drawing; the front view gives it at 1:1."""
    res = extract(DEMO, region=DEMO_FRONT_VIEW, scale=1)
    assert [v["status"] for v in verify(res, ["L=10", "L=80", "L=25"])] == ["ok"] * 3


def test_demo_top_view_circles():
    diameters = sorted(c["d_mm"] for c in extract(DEMO, region=DEMO_TOP_VIEW, scale=1)["circles"])
    assert diameters == pytest.approx([6.6] * 4 + [16, 30], abs=0.01)

"""Write tests/fixtures/demo_drawing.pdf: the two-page drawing of the demo part "D2C Demo Bracket".

Standard library only; the PDF objects are written by hand. Page 1 shows the part exactly 1:1
(mm to pt with 72/25.4) in first-angle projection: the front view above the top view, with
dimensions, centre lines, hidden edges (dashed) and a title block. Every circle is four cubic
Bezier curves and every R5 corner one, so a vector measurement recovers centres and diameters.
Text is real Helvetica text (WinAnsiEncoding, the diameter sign is byte 0xD8). The plate
thickness (10) is deliberately not dimensioned: it has to be measured from the front view.
Page 2 holds the notes.

Usage: python scripts/make_demo_drawing.py
"""

import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "demo_drawing.pdf"
PT = 72 / 25.4  # pt per mm; the scale is 1:1, so this converts part lengths to sheet lengths
PAGE = (595.28, 841.89)  # A4 portrait, pt
KAPPA = 0.5522847498  # control point distance of a cubic Bezier quarter circle, in radii
EDGE, THIN = 0.5, 0.25  # stroke widths, pt: visible edges; dimension, centre and hidden lines
HIDDEN = (8, 3)  # dash patterns, pt
CENTRE = (14, 2.5, 2, 2.5)
ARROW = (3.0, 1.0)  # arrow head length and width, mm
FONT = 10  # pt

# The part, mm. The plate thickness is not dimensioned on the drawing.
PLATE_W, PLATE_H, PLATE_T, CORNER = 80, 50, 10, 5
BOSS_D, TOTAL_H, BORE_D = 30, 25, 16
HOLE_D, HOLE_DX, HOLE_DY = 6.6, 60, 30  # four through holes on a 60 x 30 pattern

# Sheet position (mm, origin lower left) of the plate centre in the top view and of the middle
# of the plate underside in the front view.
TOP_AT, FRONT_AT = (105, 140), (105, 205)

# Helvetica advance widths (1/1000 em) of the characters the centred dimension texts use.
ADVANCE = {" ": 278, ",": 278, "x": 500, "H": 722, "R": 722, "Ø": 778} | {d: 556 for d in "0123456789"}

Point = tuple[float, float]
Segment = tuple  # ("m", p), ("l", p) or ("c", p1, p2, p3)


def num(mm: float) -> str:
    """A sheet length in mm as a PDF number in pt."""
    s = f"{mm * PT:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def quarter(c: Point, r: float, deg: float) -> tuple[Point, Point, Point, Point]:
    """Cubic Bezier quarter arc about c from angle deg to deg + 90: start, two controls, end."""
    t0, t1 = math.radians(deg), math.radians(deg + 90)
    p0 = (c[0] + r * math.cos(t0), c[1] + r * math.sin(t0))
    p3 = (c[0] + r * math.cos(t1), c[1] + r * math.sin(t1))
    p1 = (p0[0] - KAPPA * r * math.sin(t0), p0[1] + KAPPA * r * math.cos(t0))
    p2 = (p3[0] + KAPPA * r * math.sin(t1), p3[1] - KAPPA * r * math.cos(t1))
    return p0, p1, p2, p3


def radial(c: Point, r: float, deg: float, end_y: float) -> tuple[Point, Point]:
    """Point on the circle at angle deg, and the point at height end_y further out on that radius."""
    ux, uy = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    tip = (c[0] + r * ux, c[1] + r * uy)
    t = (end_y - tip[1]) / uy
    return tip, (tip[0] + t * ux, end_y)


class Sheet:
    """Content stream of one page. Points are sheet mm, origin lower left, y up."""

    def __init__(self) -> None:
        self.ops: list[str] = []

    def path(self, segments: list[Segment], width: float = THIN, dash: tuple = (), close: bool = False,
             fill: bool = False) -> None:
        if not fill:
            self.ops.append(f"{width:g} w [{' '.join(f'{d:g}' for d in dash)}] 0 d")
        self.ops += [" ".join(num(v) for p in pts for v in p) + f" {op}" for op, *pts in segments]
        self.ops.append(("h " if close else "") + ("f" if fill else "S"))

    def line(self, p: Point, q: Point, width: float = THIN, dash: tuple = ()) -> None:
        self.path([("m", p), ("l", q)], width, dash)

    def circle(self, c: Point, r: float, width: float = EDGE) -> None:
        arcs = [quarter(c, r, a) for a in (0, 90, 180, 270)]
        self.path([("m", arcs[0][0])] + [("c", *arc[1:]) for arc in arcs], width, close=True)

    def arrow(self, tip: Point, to: Point) -> None:
        """Filled arrow head with its tip at `tip`, pointing along the unit vector `to`."""
        base = (tip[0] - ARROW[0] * to[0], tip[1] - ARROW[0] * to[1])
        w = (-to[1] * ARROW[1] / 2, to[0] * ARROW[1] / 2)
        left, right = (base[0] + w[0], base[1] + w[1]), (base[0] - w[0], base[1] - w[1])
        self.path([("m", tip), ("l", left), ("l", right)], close=True, fill=True)

    def text(self, x: float, y: float, s: str, size: float = FONT, vertical: bool = False,
             centred: bool = False) -> None:
        """Text with its baseline start at (x, y); centred puts the middle of the string there.
        Vertical text reads from bottom to top, its letters standing left of the baseline."""
        if centred:
            half = sum(ADVANCE[ch] for ch in s) * size / 2000 / PT
            x, y = (x, y - half) if vertical else (x - half, y)
        a, b = (0, 1) if vertical else (1, 0)
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self.ops.append(f"BT /F1 {size:g} Tf {a} {b} {-b} {a} {num(x)} {num(y)} Tm ({s}) Tj ET")

    def stream(self) -> bytes:
        return "\n".join(self.ops).encode("cp1252")  # WinAnsiEncoding: Ø is byte 0xD8


def dim_h(s: Sheet, p: Point, q: Point, label: str) -> None:
    """Horizontal dimension line from p to q with arrows and the text above the middle."""
    s.line(p, q)
    s.arrow(p, (-1, 0))
    s.arrow(q, (1, 0))
    s.text((p[0] + q[0]) / 2, p[1] + 1.0, label, centred=True)


def dim_v(s: Sheet, p: Point, q: Point, label: str) -> None:
    """Vertical dimension line from p up to q with arrows and the text left of the middle."""
    s.line(p, q)
    s.arrow(p, (0, -1))
    s.arrow(q, (0, 1))
    s.text(p[0] - 1.0, (p[1] + q[1]) / 2, label, vertical=True, centred=True)


def callout(s: Sheet, tip: Point, end: Point, label: str) -> None:
    """Leader line from `end` to an arrow head at `tip`, the label centred above the end."""
    d = math.dist(tip, end)
    s.line(end, tip)
    s.arrow(tip, ((tip[0] - end[0]) / d, (tip[1] - end[1]) / d))
    s.text(end[0], end[1] + 1.0, label, centred=True)


def frame(s: Sheet) -> None:
    s.path([("m", (10, 10)), ("l", (200, 10)), ("l", (200, 287)), ("l", (10, 287))], EDGE, close=True)


def title_block(s: Sheet) -> None:
    """Lower right corner; the sheet frame closes its right and lower side."""
    s.path([("m", (200, 46)), ("l", (100, 46)), ("l", (100, 10))], EDGE)
    for p, q in [((100, 34), (200, 34)), ((100, 22), (200, 22)), ((160, 22), (160, 34))]:
        s.line(p, q)
    s.text(103, 38, "D2C Demo Bracket", 14)
    s.text(103, 26.5, "Drawing no. D2C-001", 9)
    s.text(163, 26.5, "Scale 1:1", 9)
    s.text(103, 14.5, "General tolerances ISO 2768-m", 9)


def top_view(s: Sheet) -> None:
    def P(x: float, y: float) -> Point:
        return TOP_AT[0] + x, TOP_AT[1] + y

    a, b, r = PLATE_W / 2, PLATE_H / 2, CORNER
    corners = [quarter(P(a - r, -(b - r)), r, 270), quarter(P(a - r, b - r), r, 0),
               quarter(P(-(a - r), b - r), r, 90), quarter(P(-(a - r), -(b - r)), r, 180)]
    contour: list[Segment] = [("m", corners[-1][3])]
    for p0, p1, p2, p3 in corners:
        contour += [("l", p0), ("c", p1, p2, p3)]
    s.path(contour, EDGE, close=True)
    holes = [(sx * HOLE_DX / 2, sy * HOLE_DY / 2) for sx in (-1, 1) for sy in (-1, 1)]
    for c, d in [((0, 0), BOSS_D), ((0, 0), BORE_D)] + [(h, HOLE_D) for h in holes]:
        s.circle(P(*c), d / 2)

    s.line(P(-44, 0), P(44, 0), dash=CENTRE)
    s.line(P(0, -29), P(0, 29), dash=CENTRE)
    for hx, hy in holes:
        s.line(P(hx - 5.5, hy), P(hx + 5.5, hy), dash=CENTRE)
        s.line(P(hx, hy - 5.5), P(hx, hy + 5.5), dash=CENTRE)

    for x in (-a, a):
        s.line(P(x, -b + 4), P(x, -b - 20))
    for x in (-HOLE_DX / 2, HOLE_DX / 2):
        s.line(P(x, -b + 3.5), P(x, -b - 10))
    for y in (-b, b):
        s.line(P(-a + 4, y), P(-a - 16, y))
    for y in (-HOLE_DY / 2, HOLE_DY / 2):
        s.line(P(a - 3.5, y), P(a + 12, y))
    dim_h(s, P(-a, -b - 18), P(a, -b - 18), "80")
    dim_h(s, P(-HOLE_DX / 2, -b - 8), P(HOLE_DX / 2, -b - 8), "60")
    dim_v(s, P(-a - 14, -b), P(-a - 14, b), "50")
    dim_v(s, P(a + 10, -HOLE_DY / 2), P(a + 10, HOLE_DY / 2), "30")
    callout(s, *radial(P(a - r, b - r), r, 45, TOP_AT[1] + 31.5), "R5")
    callout(s, *radial(P(0, 0), BOSS_D / 2, 60, TOP_AT[1] + 30), "Ø30")
    callout(s, *radial(P(0, 0), BORE_D / 2, 120, TOP_AT[1] + 30), "Ø16 H7")
    callout(s, *radial(P(-HOLE_DX / 2, HOLE_DY / 2), HOLE_D / 2, 105, TOP_AT[1] + 30), "4x Ø6,6")


def front_view(s: Sheet) -> None:
    def P(x: float, y: float) -> Point:
        return FRONT_AT[0] + x, FRONT_AT[1] + y

    a, t, r = PLATE_W / 2, PLATE_T, BOSS_D / 2
    outline = [(-a, 0), (a, 0), (a, t), (r, t), (r, TOTAL_H), (-r, TOTAL_H), (-r, t), (-a, t)]
    s.path([("m", P(*outline[0]))] + [("l", P(*p)) for p in outline[1:]], EDGE, close=True)
    s.line(P(-r, t), P(r, t), EDGE)  # edge where the boss meets the plate
    for x in (-BORE_D / 2, BORE_D / 2):
        s.line(P(x, 0), P(x, TOTAL_H), dash=HIDDEN)
    for hx in (-HOLE_DX / 2, HOLE_DX / 2):
        for x in (hx - HOLE_D / 2, hx + HOLE_D / 2):
            s.line(P(x, 0), P(x, t), dash=HIDDEN)
        s.line(P(hx, -3), P(hx, t + 3), dash=CENTRE)
    s.line(P(0, -4), P(0, TOTAL_H + 4), dash=CENTRE)

    s.line(P(a + 1, 0), P(a + 14, 0))
    s.line(P(r + 1, TOTAL_H), P(a + 14, TOTAL_H))
    dim_v(s, P(a + 12, 0), P(a + 12, TOTAL_H), "25")


def notes_page() -> Sheet:
    s = Sheet()
    frame(s)
    s.text(20, 270, "Notes: 1. Material S235JR. 2. Deburr all edges.")
    return s


def drawing_page() -> Sheet:
    s = Sheet()
    frame(s)
    title_block(s)
    top_view(s)
    front_view(s)
    return s


def pdf(streams: list[bytes]) -> bytes:
    """A PDF with one page per content stream: Helvetica as /F1, uncompressed streams, xref table."""
    n = len(streams)
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(n))
        + b"] /Count %d >>" % n,
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    for i, content in enumerate(streams):
        objs[4 + 2 * i] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %g %g] " % PAGE
            + b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (5 + 2 * i)
        )
        objs[5 + 2 * i] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in objs.items():
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def region(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    """A sheet box in mm (origin lower left) as page fractions (origin top left, like crop)."""
    fx, fy = PT / PAGE[0], PT / PAGE[1]
    return round(x0 * fx, 3), round(1 - y1 * fy, 3), round(x1 * fx, 3), round(1 - y0 * fy, 3)


if __name__ == "__main__":
    OUT.write_bytes(pdf([drawing_page().stream(), notes_page().stream()]))
    print(f"wrote {OUT}")
    print("top view   ", region(44, 92, 164, 177))
    print("front view ", region(44, 198, 164, 237))
    print("title block", region(98, 8, 202, 48))

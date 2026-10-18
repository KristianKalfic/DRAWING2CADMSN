"""Erstellt die Vektor-PDF-Zeichnungen der drei Demo-Bauteile unter d2c/demo.

demo_drawing.pdf zeigt die "D2C Demo Bracket", shaft_drawing.pdf die "D2C Demo Shaft" und flange_drawing.pdf den
"D2C Demo Flange". Nur die Standardbibliothek wird benötigt; die PDF-Objekte werden direkt geschrieben. Seite 1 zeigt
das Bauteil im Maßstab 1:1 (Umrechnung mm zu pt: 72 pt je 25,4 mm) mit Maßen, Mittellinien, verdeckten Kanten
(gestrichelt) und Schriftfeld. Halterung und Flansch stehen in Erstwinkelprojektion: Vorderansicht über der Draufsicht;
die Welle zeigt eine Ansicht mit waagerechter Achse (links liegt z = 0). Jeder Kreis besteht aus vier kubischen
Bézierkurven, jede R5-Ecke aus einer; so lassen sich Mittelpunkte und Durchmesser aus den Vektordaten bestimmen.
Der Text ist echter Helvetica-Text (WinAnsiEncoding, Durchmesserzeichen als Byte 0xD8, Gradzeichen als 0xB0). Die Dicke
von Halterung (10) und Flansch (8) ist absichtlich nicht bemaßt und muss aus der Vorderansicht bestimmt werden.
Seite 2 enthält die Hinweise.

Aufruf: python scripts/make_demo_drawing.py
"""

import math
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "d2c" / "demo"
PT = 72 / 25.4  # pt pro mm; im Maßstab 1:1 wird damit die Bauteillänge in Blattlänge umgerechnet
PAGE = (595.28, 841.89)  # A4-Hochformat, pt
KAPPA = 0.5522847498  # Abstand des Kontrollpunkts eines kubischen Bézier-Viertelkreises, in Radien
EDGE, THIN = 0.5, 0.25  # Strichbreiten in pt: sichtbare Kanten sowie Maß-, Mittel- und verdeckte Linien
HIDDEN = (8, 3)  # Strichmuster in pt
CENTRE = (14, 2.5, 2, 2.5)
ARROW = (3.0, 1.0)  # Länge und Breite der Pfeilspitze in mm
FONT = 10  # pt

# Bauteilmaße in mm. Die Plattendicke ist in der Zeichnung nicht bemaßt.
PLATE_W, PLATE_H, PLATE_T, CORNER = 80, 50, 10, 5
BOSS_D, TOTAL_H, BORE_D = 30, 25, 16
HOLE_D, HOLE_DX, HOLE_DY = 6.6, 60, 30  # vier Durchgangsbohrungen im Muster 60 × 30

# Blattposition (mm, Ursprung links unten) der Plattenmitte in der Draufsicht und der Mitte
# der Plattenunterseite in der Vorderansicht.
TOP_AT, FRONT_AT = (105, 140), (105, 205)

# Welle: Absätze als (Ø, Länge) von links (z = 0) nach rechts, Fase am rechten Ende; Lage des linken Endes auf der Achse.
SHAFT_STEPS = [(20, 35), (30, 45), (24, 30)]
SHAFT_CHAMFER = 2
SHAFT_AT = (50, 170)

# Flansch: Scheibe mit Mittenbohrung und vier Bohrungen auf einem Teilkreis, um 45° gedreht. Die Dicke ist nicht bemaßt.
FLANGE_D, FLANGE_T, FLANGE_BORE_D = 60, 8, 12
FLANGE_PITCH_D, FLANGE_HOLE_D = 44, 5.5

# Zeichenbreiten von Helvetica (1/1000 em) für zentrierte Maßtexte.
ADVANCE = {" ": 278, ",": 278, "x": 500, "H": 722, "R": 722, "Ø": 778, "°": 400} | {d: 556 for d in "0123456789"}

Point = tuple[float, float]
Segment = tuple  # ("m", p), ("l", p) oder ("c", p1, p2, p3)


def num(mm: float) -> str:
    """Ein Blattmaß in mm, dargestellt als PDF-Zahl in pt."""
    s = f"{mm * PT:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def quarter(c: Point, r: float, deg: float) -> tuple[Point, Point, Point, Point]:
    """Kubischer Bézier-Viertelbogen um c vom Winkel deg bis deg + 90: Anfang, zwei Kontrollpunkte, Ende."""
    t0, t1 = math.radians(deg), math.radians(deg + 90)
    p0 = (c[0] + r * math.cos(t0), c[1] + r * math.sin(t0))
    p3 = (c[0] + r * math.cos(t1), c[1] + r * math.sin(t1))
    p1 = (p0[0] - KAPPA * r * math.sin(t0), p0[1] + KAPPA * r * math.cos(t0))
    p2 = (p3[0] + KAPPA * r * math.sin(t1), p3[1] - KAPPA * r * math.cos(t1))
    return p0, p1, p2, p3


def radial(c: Point, r: float, deg: float, end_y: float) -> tuple[Point, Point]:
    """Punkt auf dem Kreis beim Winkel deg und ein weiterer Punkt auf derselben Radiuslinie in Höhe end_y."""
    ux, uy = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    tip = (c[0] + r * ux, c[1] + r * uy)
    t = (end_y - tip[1]) / uy
    return tip, (tip[0] + t * ux, end_y)


class Sheet:
    """Inhaltsstrom einer Seite. Punkte in mm auf dem Blatt, Ursprung links unten, y nach oben."""

    def __init__(self) -> None:
        self.ops: list[str] = []

    def path(self, segments: list[Segment], width: float = THIN, dash: tuple = (), close: bool = False, fill: bool = False) -> None:
        if not fill:
            self.ops.append(f"{width:g} w [{' '.join(f'{d:g}' for d in dash)}] 0 d")
        self.ops += [" ".join(num(v) for p in pts for v in p) + f" {op}" for op, *pts in segments]
        self.ops.append(("h " if close else "") + ("f" if fill else "S"))

    def line(self, p: Point, q: Point, width: float = THIN, dash: tuple = ()) -> None:
        self.path([("m", p), ("l", q)], width, dash)

    def circle(self, c: Point, r: float, width: float = EDGE, dash: tuple = ()) -> None:
        arcs = [quarter(c, r, a) for a in (0, 90, 180, 270)]
        self.path([("m", arcs[0][0])] + [("c", *arc[1:]) for arc in arcs], width, dash, close=True)

    def arrow(self, tip: Point, to: Point) -> None:
        """Gefüllte Pfeilspitze mit der Spitze bei `tip`, ausgerichtet entlang des Einheitsvektors `to`."""
        base = (tip[0] - ARROW[0] * to[0], tip[1] - ARROW[0] * to[1])
        w = (-to[1] * ARROW[1] / 2, to[0] * ARROW[1] / 2)
        left, right = (base[0] + w[0], base[1] + w[1]), (base[0] - w[0], base[1] - w[1])
        self.path([("m", tip), ("l", left), ("l", right)], close=True, fill=True)

    def text(self, x: float, y: float, s: str, size: float = FONT, vertical: bool = False, centred: bool = False) -> None:
        """Text mit Grundlinienanfang bei (x, y); centred setzt dort die Zeichenmitte.
        Bei vertical verläuft der Text von unten nach oben, links neben der Grundlinie."""
        if centred:
            half = sum(ADVANCE[ch] for ch in s) * size / 2000 / PT
            x, y = (x, y - half) if vertical else (x - half, y)
        a, b = (0, 1) if vertical else (1, 0)
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self.ops.append(f"BT /F1 {size:g} Tf {a} {b} {-b} {a} {num(x)} {num(y)} Tm ({s}) Tj ET")

    def stream(self) -> bytes:
        return "\n".join(self.ops).encode("cp1252")  # WinAnsiEncoding: Ø entspricht dem Byte 0xD8


def dim_h(s: Sheet, p: Point, q: Point, label: str) -> None:
    """Waagerechte Maßlinie von p nach q mit Pfeilen und Beschriftung über der Mitte."""
    s.line(p, q)
    s.arrow(p, (-1, 0))
    s.arrow(q, (1, 0))
    s.text((p[0] + q[0]) / 2, p[1] + 1.0, label, centred=True)


def dim_v(s: Sheet, p: Point, q: Point, label: str, at: float = 0.5) -> None:
    """Senkrechte Maßlinie von p nach q mit Pfeilen und Beschriftung links daneben, bei `at` (Anteil von p bis q) zentriert."""
    s.line(p, q)
    s.arrow(p, (0, -1))
    s.arrow(q, (0, 1))
    s.text(p[0] - 1.0, p[1] + at * (q[1] - p[1]), label, vertical=True, centred=True)


def callout(s: Sheet, tip: Point, end: Point, label: str) -> None:
    """Führungslinie von `end` zur Pfeilspitze bei `tip`, Beschriftung mittig über end."""
    d = math.dist(tip, end)
    s.line(end, tip)
    s.arrow(tip, ((tip[0] - end[0]) / d, (tip[1] - end[1]) / d))
    s.text(end[0], end[1] + 1.0, label, centred=True)


def frame(s: Sheet) -> None:
    s.path([("m", (10, 10)), ("l", (200, 10)), ("l", (200, 287)), ("l", (10, 287))], EDGE, close=True)


def title_block(s: Sheet, title: str, number: str) -> None:
    """Schriftfeld unten rechts; der Blattrahmen bildet die rechte und untere Kante."""
    s.path([("m", (200, 46)), ("l", (100, 46)), ("l", (100, 10))], EDGE)
    for p, q in [((100, 34), (200, 34)), ((100, 22), (200, 22)), ((160, 22), (160, 34))]:
        s.line(p, q)
    s.text(103, 38, title, 14)
    s.text(103, 26.5, f"Drawing no. {number}", 9)
    s.text(163, 26.5, "Scale 1:1", 9)
    s.text(103, 14.5, "General tolerances ISO 2768-m", 9)


def top_view(s: Sheet) -> None:
    def P(x: float, y: float) -> Point:
        return TOP_AT[0] + x, TOP_AT[1] + y

    a, b, r = PLATE_W / 2, PLATE_H / 2, CORNER
    corners = [
        quarter(P(a - r, -(b - r)), r, 270),
        quarter(P(a - r, b - r), r, 0),
        quarter(P(-(a - r), b - r), r, 90),
        quarter(P(-(a - r), -(b - r)), r, 180),
    ]
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
    s.line(P(-r, t), P(r, t), EDGE)  # Kante am Übergang vom Bund zur Platte
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


def shaft_view(s: Sheet) -> None:
    def P(x: float, y: float) -> Point:
        return SHAFT_AT[0] + x, SHAFT_AT[1] + y

    radii = [d / 2 for d, _ in SHAFT_STEPS]
    half, x = [(0.0, radii[0])], 0.0  # obere Konturhälfte von links nach rechts
    edges = [0.0]  # x der Wellenenden und Schultern
    for i, (_, length) in enumerate(SHAFT_STEPS):
        x += length
        edges.append(x)
        half.append((x, radii[i]))
        if i + 1 < len(radii):
            half.append((x, radii[i + 1]))
    c, end = SHAFT_CHAMFER, radii[-1]
    half[-1:] = [(x - c, end), (x, end - c)]
    outline = half + [(px, -py) for px, py in reversed(half)]
    s.path([("m", P(*outline[0]))] + [("l", P(*p)) for p in outline[1:]], EDGE, close=True)
    s.line(P(x - c, -end), P(x - c, end), EDGE)  # Kante am Übergang von der Fase zum Mantel
    s.line(P(-5, 0), P(x + 5, 0), dash=CENTRE)

    for (d, length), start in zip(SHAFT_STEPS, edges):
        dim_v(s, P(start + length / 2, -d / 2), P(start + length / 2, d / 2), f"Ø{d}", at=0.75)
    for e, r in zip(edges, [radii[0], *radii[1:], radii[-1] - c]):
        s.line(P(e, -r - 1), P(e, -36))
    for a, b, length in zip(edges, edges[1:], (n for _, n in SHAFT_STEPS)):
        dim_h(s, P(a, -26), P(b, -26), f"{length}")
    dim_h(s, P(0, -34), P(x, -34), f"{x:g}")
    callout(s, P(x - c / 2, end - c / 2), P(x + 8, end + 12), f"{c}x45°")


def flange_views(s: Sheet) -> None:
    def top(px: float, py: float) -> Point:
        return TOP_AT[0] + px, TOP_AT[1] + py

    def front(px: float, py: float) -> Point:
        return FRONT_AT[0] + px, FRONT_AT[1] + py

    r, bore, hole, pitch = FLANGE_D / 2, FLANGE_BORE_D / 2, FLANGE_HOLE_D / 2, FLANGE_PITCH_D / 2
    off = pitch * math.cos(math.radians(45))  # Abstand der Bohrungsmitten von den Achsen
    holes = [(sx * off, sy * off) for sx in (-1, 1) for sy in (-1, 1)]
    for c, rad in [((0, 0), r), ((0, 0), bore)] + [(h, hole) for h in holes]:
        s.circle(top(*c), rad)
    s.circle(top(0, 0), pitch, THIN, CENTRE)
    s.line(top(-r - 5, 0), top(r + 5, 0), dash=CENTRE)
    s.line(top(0, -r - 5), top(0, r + 5), dash=CENTRE)
    for hx, hy in holes:
        s.line(top(hx - 5.5, hy), top(hx + 5.5, hy), dash=CENTRE)
        s.line(top(hx, hy - 5.5), top(hx, hy + 5.5), dash=CENTRE)
    callout(s, *radial(top(0, 0), r, 120, TOP_AT[1] + 36), "Ø60")
    callout(s, *radial(top(0, 0), bore, 60, TOP_AT[1] + 40), "Ø12 H7")
    callout(s, *radial(top(-off, off), hole, 75, TOP_AT[1] + 40), "4x Ø5,5")
    callout(s, top(pitch * math.cos(math.radians(-20)), pitch * math.sin(math.radians(-20))), top(40, -22), "Ø44")

    s.path([("m", front(-r, 0)), ("l", front(r, 0)), ("l", front(r, FLANGE_T)), ("l", front(-r, FLANGE_T))], EDGE, close=True)
    for centre, rad in [(0, bore)] + [(sx * off, hole) for sx in (-1, 1)]:
        for x in (centre - rad, centre + rad):
            s.line(front(x, 0), front(x, FLANGE_T), dash=HIDDEN)
        s.line(front(centre, -3), front(centre, FLANGE_T + 3), dash=CENTRE)


def notes_page(text: str) -> Sheet:
    s = Sheet()
    frame(s)
    s.text(20, 270, text)
    return s


def drawing_page(title: str, number: str, *views) -> Sheet:
    s = Sheet()
    frame(s)
    title_block(s, title, number)
    for view in views:
        view(s)
    return s


def pdf(streams: list[bytes]) -> bytes:
    """PDF mit einer Seite je Inhaltsstrom: Helvetica als /F1, unkomprimierte Ströme und Querverweistabelle."""
    n = len(streams)
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(n)) + b"] /Count %d >>" % n,
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
    """Blattrechteck in mm (Ursprung links unten), angegeben als Seitenanteile (Ursprung oben links, wie bei einem Zuschneidebereich)."""
    fx, fy = PT / PAGE[0], PT / PAGE[1]
    return round(x0 * fx, 3), round(1 - y1 * fy, 3), round(x1 * fx, 3), round(1 - y0 * fy, 3)


if __name__ == "__main__":
    drawings = {
        "demo_drawing.pdf": (
            drawing_page("D2C Demo Bracket", "D2C-001", top_view, front_view),
            "Notes: 1. Material S235JR. 2. Deburr all edges.",
        ),
        "shaft_drawing.pdf": (
            drawing_page("D2C Demo Shaft", "D2C-002", shaft_view),
            "Notes: 1. Material C45. 2. Deburr all edges.",
        ),
        "flange_drawing.pdf": (
            drawing_page("D2C Demo Flange", "D2C-003", flange_views),
            "Notes: 1. Material S235JR. 2. Deburr all edges.",
        ),
    }
    for name, (page, note) in drawings.items():
        (DEMO / name).write_bytes(pdf([page.stream(), notes_page(note).stream()]))
        print(f"Written: {DEMO / name}")
    print("Top view        ", region(44, 92, 164, 177))
    print("Front view      ", region(44, 198, 164, 237))
    print("Title block     ", region(98, 8, 202, 48))

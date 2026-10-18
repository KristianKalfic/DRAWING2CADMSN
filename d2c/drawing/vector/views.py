"""Misst die Vektorgeometrie einer Seite oder eines Bereichs davon."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from os import PathLike

import pymupdf

from d2c.drawing.pages import unit_box
from d2c.drawing.vector.chains import class_chains, collect, grid
from d2c.drawing.vector.contours import (
    Contour,
    find_contours,
    is_frame,
    score,
    visible_widths,
)
from d2c.drawing.vector.fit import class_prims
from d2c.drawing.vector.primitives import Arc, Box, Circle, Line, Prim, union

_PT_MM = 25.4 / 72  # mm je PDF-Punkt
_FIT_TOL = 0.03  # pt: größte Abweichung eines Eckpunkts von einer angepassten Geraden oder einem Bogen
_GRID_FIT_SHARE = 0.75  # Anteil des Koordinatenrasters, den die Anpassung als Rundungsabweichung toleriert


@dataclass(frozen=True)
class _Frame:
    """Abbildung von Blattpunkten (y nach unten) auf Modell-mm (y nach oben), mit Ursprung links unten im Bereich."""

    ox: float
    oy: float
    k: float  # mm je Punkt

    def x(self, v: float) -> float:
        return float(round((v - self.ox) * self.k, 4))

    def y(self, v: float) -> float:
        return float(round((self.oy - v) * self.k, 4))

    def mm(self, v: float) -> float:
        return float(round(v * self.k, 4))


def _read_page(pdf_path: str | PathLike[str], page: int) -> tuple[float, float, list]:
    """Größe (pt) der angezeigten Seite und ihrer umrandeten Elemente."""
    with pymupdf.open(pdf_path) as doc:
        if not 1 <= page <= len(doc):
            raise ValueError(f"`page` must be between 1 and {len(doc)}; received: {page}.")
        pg = doc[page - 1]
        return pg.rect.width, pg.rect.height, collect(pg)


def _inside(p: Prim, box: Box) -> bool:
    x, y = p.mid()
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _line_row(p: Line, f: _Frame, in_contour: bool) -> dict:
    dx, dy = p.p1[0] - p.p0[0], -(p.p1[1] - p.p0[1])  # y nach oben
    angle = math.degrees(math.atan2(dy, dx)) % 180
    return {
        "x0": f.x(p.p0[0]),
        "y0": f.y(p.p0[1]),
        "x1": f.x(p.p1[0]),
        "y1": f.y(p.p1[1]),
        "length_mm": f.mm(math.hypot(dx, dy)),
        "angle_deg": round(angle if angle < 179.995 else 0.0, 3),
        "in_contour": in_contour,
    }


def _contour_row(c: Contour, f: _Frame) -> dict:
    x0, y0, x1, y1 = c.bbox
    return {
        "bbox_mm": [f.x(x0), f.y(y1), f.x(x1), f.y(y0)],
        "w_mm": f.mm(x1 - x0),
        "h_mm": f.mm(y1 - y0),
        "closed_loop": c.area is not None,
        "area_mm2": None if c.area is None else round(float(c.area * f.k * f.k), 3),
    }


def _extent(prims: list[Prim], contours: list[Contour], f: _Frame) -> tuple[list[float] | None, str | None]:
    """Breite und Höhe der größten Kontur oder, falls es keine gibt, aller Grundelemente; None, wenn keine vorhanden sind."""
    if contours:
        box, basis = contours[0].bbox, "largest contour"
    elif prims:
        box, basis = union([p.bbox() for p in prims]), "all visible primitives"
    else:
        return None, None
    return [f.mm(box[2] - box[0]), f.mm(box[3] - box[1])], basis


def _rows(prims: list[Prim], contours: list[Contour], f: _Frame) -> dict:
    """Kreise, Bögen, Linien und Konturen eines Ergebnisses, absteigend nach Größe."""
    in_contour = {id(p) for c in contours for p in c.prims}
    circles = [{"cx": f.x(p.c[0]), "cy": f.y(p.c[1]), "d_mm": f.mm(2 * p.r)} for p in prims if isinstance(p, Circle)]
    arcs = [
        {
            "cx": f.x(p.c[0]),
            "cy": f.y(p.c[1]),
            "r_mm": f.mm(p.r),
            "sweep": round(abs(math.degrees(p.sw)), 2),
        }
        for p in prims
        if isinstance(p, Arc)
    ]
    lines = [_line_row(p, f, id(p) in in_contour) for p in prims if isinstance(p, Line)]
    return {
        "circles": sorted(circles, key=lambda d: (-d["d_mm"], d["cx"], d["cy"])),
        "arcs": sorted(arcs, key=lambda d: (-d["r_mm"], d["cx"], d["cy"])),
        "lines": sorted(lines, key=lambda d: -d["length_mm"]),
        "contours": [_contour_row(c, f) for c in contours],
    }


def extract(
    pdf_path: str | PathLike[str],
    page: int = 1,
    region: Sequence[float] | None = None,
    scale: float = 1.0,
    edge_width: str | float = "auto",
) -> dict:
    """Misst die Vektorgeometrie einer Seite oder eines Bereichs davon.

    Argumente:
        pdf_path: Zeichnung.
        page: Seitennummer, 1-basiert.
        region: (x0, y0, x1, y1) als Anteile der angezeigten Seite, Ursprung oben links. Ein Geometrieelement
            gehört zum Bereich, wenn sein Mittelpunkt darin liegt.
        scale: Zeichnungsmaßstab im Verhältnis Zeichnung:Modell (2 entspricht 2:1, 0.5 entspricht 1:2).
            Modell-mm = Blatt-mm / scale.
        edge_width: Strichstärke sichtbarer Kanten. "auto" wählt die Klasse mit dem größten Anteil an der
            gezeichneten Länge, "all" alle Klassen, eine Zahl die nächstliegende Strichstärke.

    Rückgabe:
        scale_used; styles (Strichklassen: width in pt, score = Bewertung der gezeichneten Länge, visible), zuerst die mit dem
        höchsten Anteil; extent_mm [width, height] mit extent_basis ("largest contour" oder "all visible primitives", None, wenn der Bereich keine Geometrie enthält); circles (cx, cy, d_mm) und arcs (cx,
        cy, r_mm, sweep in Grad), absteigend nach Größe; lines (x0, y0, x1, y1, length_mm, angle_deg,
        in_contour), absteigend nach Länge; contours (bbox_mm [x0, y0, x1, y1], w_mm, h_mm, closed_loop,
        area_mm2), absteigend nach Größe. Koordinaten und Maße sind Modell-mm, y zeigt nach oben, Ursprung
        links unten im Bereich.
    """
    if not scale > 0:
        raise ValueError(f"`scale` must be positive; received: {scale}.")
    rx0, ry0, rx1, ry1 = unit_box(region, "region")
    width, height, flat = _read_page(pdf_path, page)
    classes = {w: [ch for ch in chains if not is_frame(ch, width, height)] for w, chains in class_chains(flat).items()}
    scores = {w: score(chains, width, height) for w, chains in classes.items()}
    visible = visible_widths(scores, edge_width)

    tol = max(_FIT_TOL, _GRID_FIT_SHARE * grid(flat))
    box = (rx0 * width, ry0 * height, rx1 * width, ry1 * height)  # pt, y nach unten
    prims = [p for w in visible for p in class_prims(classes[w], tol) if _inside(p, box)]
    contours = sorted(find_contours(prims), key=lambda c: c.rank, reverse=True)
    f = _Frame(ox=box[0], oy=box[3], k=_PT_MM / scale)
    extent, basis = _extent(prims, contours, f)
    return {
        "scale_used": float(scale),
        "styles": [{"width": w, "score": round(s, 1), "visible": w in visible} for w, s in sorted(scores.items(), reverse=True)],
        "extent_mm": extent,
        "extent_basis": basis,
        **_rows(prims, contours, f),
    }

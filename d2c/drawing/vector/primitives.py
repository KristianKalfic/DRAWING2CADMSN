"""Grundelemente der Vektorauswertung: Typen, Rahmenhilfen sowie Linie, Bogen, Kreis und Freiformkurve.

Winkel sind in den y-nach-unten-Koordinaten der Seite in Radiant angegeben."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]
Box = tuple[float, float, float, float]  # x0, y0, x1, y1
Element = tuple  # ("l", p0, p1) oder ("c", p0, p1, p2, p3): umrandete Gerade oder kubische Bézierkurve
Chain = list[Element]  # Elemente, deren Enden direkt aufeinander folgen


def bounds(pts: Sequence[Point]) -> Box:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def union(boxes: Sequence[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _shoelace(pts: Sequence[Point]) -> float:
    return 0.5 * sum(u[0] * v[1] - v[0] * u[1] for u, v in zip(pts, pts[1:]))


def to_point(a: np.ndarray) -> Point:
    return float(a[0]), float(a[1])


@dataclass(frozen=True, eq=False)
class Line:
    p0: Point
    p1: Point
    single: bool = False  # Eine Quellkante: Nur Folgen daraus können eine Freiformkurve bilden

    def mid(self) -> Point:
        return (self.p0[0] + self.p1[0]) / 2, (self.p0[1] + self.p1[1]) / 2

    def bbox(self) -> Box:
        return bounds([self.p0, self.p1])

    def area_term(self, forward: bool) -> float:
        return _shoelace([self.p0, self.p1] if forward else [self.p1, self.p0])


@dataclass(frozen=True, eq=False)
class Arc:
    c: Point
    r: float
    a0: float  # Anfangswinkel
    sw: float  # Vorzeichenbehafteter überstrichener Winkel
    p0: Point
    p1: Point

    def mid(self) -> Point:
        a = self.a0 + self.sw / 2
        return self.c[0] + self.r * math.cos(a), self.c[1] + self.r * math.sin(a)

    def bbox(self) -> Box:
        lo = min(self.a0, self.a0 + self.sw)
        hi = lo + abs(self.sw)
        pts = [self.p0, self.p1]
        for k in range(-8, 9):  # Extremwerte der Achse innerhalb des überstrichenen Winkels
            a = k * math.pi / 2
            if lo - 1e-9 <= a <= hi + 1e-9:
                pts.append((self.c[0] + self.r * math.cos(a), self.c[1] + self.r * math.sin(a)))
        return bounds(pts)

    def area_term(self, forward: bool) -> float:
        a0, sw = (self.a0, self.sw) if forward else (self.a0 + self.sw, -self.sw)
        a1 = a0 + sw
        (cx, cy), r = self.c, self.r
        dsin, dcos = math.sin(a1) - math.sin(a0), math.cos(a1) - math.cos(a0)
        return 0.5 * (cx * r * dsin - cy * r * dcos + r * r * (a1 - a0))


@dataclass(frozen=True, eq=False)
class Circle:
    c: Point
    r: float

    def mid(self) -> Point:
        return self.c

    def bbox(self) -> Box:
        return self.c[0] - self.r, self.c[1] - self.r, self.c[0] + self.r, self.c[1] + self.r


@dataclass(frozen=True, eq=False)
class Curve:
    """Freiformkurve (Ellipse einer isometrischen Ansicht, Spline) als Polylinie durch ihre Punkte."""

    pts: list[Point]

    @property
    def p0(self) -> Point:
        return self.pts[0]

    @property
    def p1(self) -> Point:
        return self.pts[-1]

    def mid(self) -> Point:
        return self.pts[len(self.pts) // 2]

    def bbox(self) -> Box:
        return bounds(self.pts)

    def area_term(self, forward: bool) -> float:
        return _shoelace(self.pts if forward else self.pts[::-1])


Prim = Line | Arc | Circle | Curve


Prim = Line | Arc | Circle | Curve

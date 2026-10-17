"""Exakte Messungen anhand der Vektorgeometrie technischer PDF-Zeichnungen.

Die meisten BCT-Zeichnungen sind Vektor-PDFs (NX- und CGM-Exporte): Kreise und Bögen liegen als Bézierkurven oder
aus Linien angenäherte Polylinien vor. Durchmesser, Radien und Kantenlängen lassen sich daher aus der Geometrie
ermitteln, ohne gedruckte Zahlen abzulesen. So wird ein in der Zeichnung fehlendes Maß bestimmt, ein aus dem Bild
gelesenes Maß geprüft und ein falscher Maßstab im Schriftfeld erkennbar.

Seiten sind 1-basiert. Ein Bereich ist (x0, y0, x1, y1) als Anteile der angezeigten Seite, Ursprung oben links,
wie beim Zuschnitt des Seitendarstellers. Ergebnisse sind Modell-mm (Blatt-mm geteilt durch den Maßstab),
x zeigt nach rechts, y nach oben. Der Ursprung liegt links unten im Bereich oder, wenn kein Bereich angegeben ist,
links unten auf dem Blatt."""

import collections
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from os import PathLike

import numpy as np
import pymupdf

_PT_MM = 25.4 / 72  # mm je PDF-Punkt
_TAU = 2 * math.pi
_FIT_TOL = 0.03  # pt: größte Abweichung eines Eckpunkts von einer angepassten Geraden oder einem Bogen
_JOIN = 0.02  # pt: Endpunkte der Stream-Elemente gelten bis zu diesem Abstand als zusammenhängend
_SNAP = 0.05  # pt: Endpunkte der Konturteile gelten bis zu diesem Abstand als verbunden
_MIN_ARC_POINTS = 9  # Weniger Eckpunkte lassen die Ellipse der isometrischen Ansicht nicht von einem Kreis unterscheiden
_MIN_ARC_SWEEP = math.radians(60)
# Koordinatenraster (pt) der CGM-basierten Exporte. Die feinere NX-Ausgabe passt zu keinem dieser Raster.
_LATTICES = (1.0, 0.5, 0.25, 0.2, 0.12, 0.1, 0.06, 0.05, 0.025, 0.02, 0.01)
STANDARD_SCALES = (0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 4.0, 5.0, 10.0, 20.0)

Point = tuple[float, float]
Box = tuple[float, float, float, float]  # x0, y0, x1, y1
Element = tuple  # ("l", p0, p1) oder ("c", p0, p1, p2, p3): umrandete Gerade oder kubische Bézierkurve
Chain = list[Element]  # Elemente, deren Enden direkt aufeinander folgen


def _bounds(pts: Sequence[Point]) -> Box:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _union(boxes: Sequence[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _shoelace(pts: Sequence[Point]) -> float:
    return 0.5 * sum(u[0] * v[1] - v[0] * u[1] for u, v in zip(pts, pts[1:]))


def _pt(a: np.ndarray) -> Point:
    return float(a[0]), float(a[1])


# ---------------------------------------------------------------------------------------------
# Grundelemente. Winkel sind in den y-nach-unten-Koordinaten der Seite in Radiant angegeben.
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class _Line:
    p0: Point
    p1: Point
    single: bool = False  # Eine Quellkante: Nur Folgen daraus können eine Freiformkurve bilden

    def mid(self) -> Point:
        return (self.p0[0] + self.p1[0]) / 2, (self.p0[1] + self.p1[1]) / 2

    def bbox(self) -> Box:
        return _bounds([self.p0, self.p1])

    def area_term(self, forward: bool) -> float:
        return _shoelace([self.p0, self.p1] if forward else [self.p1, self.p0])


@dataclass(frozen=True, eq=False)
class _Arc:
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
        return _bounds(pts)

    def area_term(self, forward: bool) -> float:
        a0, sw = (self.a0, self.sw) if forward else (self.a0 + self.sw, -self.sw)
        a1 = a0 + sw
        (cx, cy), r = self.c, self.r
        dsin, dcos = math.sin(a1) - math.sin(a0), math.cos(a1) - math.cos(a0)
        return 0.5 * (cx * r * dsin - cy * r * dcos + r * r * (a1 - a0))


@dataclass(frozen=True, eq=False)
class _Circle:
    c: Point
    r: float

    def mid(self) -> Point:
        return self.c

    def bbox(self) -> Box:
        return self.c[0] - self.r, self.c[1] - self.r, self.c[0] + self.r, self.c[1] + self.r


@dataclass(frozen=True, eq=False)
class _Curve:
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
        return _bounds(self.pts)

    def area_term(self, forward: bool) -> float:
        return _shoelace(self.pts if forward else self.pts[::-1])


_Prim = _Line | _Arc | _Circle | _Curve


# ---------------------------------------------------------------------------------------------
# 1. Umrandete Geometrie der angezeigten Seite
# ---------------------------------------------------------------------------------------------
def _corners(item: tuple) -> list[Point]:
    """Eckpunkte eines umrandeten Rechtecks (re) oder Vierecks (qu), in Pfadreihenfolge."""
    if item[0] == "re":
        r = item[1]
        return [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
    q = item[1]
    return [q.ul, q.ur, q.lr, q.ll]


def _collect(page: pymupdf.Page) -> list[tuple[float, Element]]:
    """Umrandete Linien und kubische Kurven als (Strichstärke, Element) in der Reihenfolge des Datenstroms, angegeben in Punkten der angezeigten Seite (y nach unten, Seitendrehung angewendet)."""
    m = page.rotation_matrix

    def pt(p) -> Point:  # In doppelter Genauigkeit: Point * Matrix rundet auf 32 Bit
        return m.a * p[0] + m.c * p[1] + m.e, m.b * p[0] + m.d * p[1] + m.f

    flat = []
    for path in page.get_drawings():
        if path["type"] not in ("s", "fs"):
            continue
        width = round(path.get("width") or 0.0, 3)
        for item in path["items"]:
            if item[0] == "l":
                flat.append((width, ("l", pt(item[1]), pt(item[2]))))
            elif item[0] == "c":
                flat.append((width, ("c", *map(pt, item[1:5]))))
            elif item[0] in ("re", "qu"):  # Ein umrandetes Rechteck besteht aus vier Linien
                pts = [pt(c) for c in _corners(item)]
                pts.append(pts[0])
                flat += [(width, ("l", pts[i], pts[i + 1])) for i in range(4)]
    return flat


def _grid(flat: list[tuple[float, Element]]) -> float:
    """Abstand (pt) des Koordinatenrasters der Datei; 0, wenn es keines gibt. Die Anpassungen müssen die Rundungsabweichung tolerieren."""
    if not flat:
        return 0.0
    xs = np.array([p[0] for _, el in flat for p in el[1:]])
    for q in _LATTICES:
        if abs(np.mean(np.exp(2j * np.pi * xs / q))) > 0.95:
            return q
    return 0.0


# ---------------------------------------------------------------------------------------------
# 2. Ketten: Elemente, deren Enden direkt aufeinander folgen
# ---------------------------------------------------------------------------------------------
def _key(p: Point) -> tuple[int, int]:
    return round(p[0] * 100), round(p[1] * 100)


def _reversed(el: Element) -> Element:
    return (el[0], *reversed(el[1:]))


def _dedupe_elements(els: list[Element]) -> list[Element]:
    """Entfernt doppelt gezeichnete Elemente (dieselben Endpunkte, in beliebiger Richtung)."""
    seen, out = set(), []
    for el in els:
        key = (el[0], *sorted([_key(el[1]), _key(el[-1])]))
        if el[0] == "c":
            key += tuple(sorted([_key(el[2]), _key(el[3])]))
        if key not in seen:
            seen.add(key)
            out.append(el)
    return out


def _chains(els: list[Element]) -> list[Chain]:
    """Teilt einen Datenstrom dort, wo ein Element nicht am Ende des vorherigen beginnt. NX- und cgm2pdf-Polylinien sind durchgehend (Lücken sind 0 oder mindestens 0,05 pt)."""
    out: list[Chain] = []
    for el in els:
        if out and math.dist(out[-1][-1][-1], el[1]) <= _JOIN:
            out[-1].append(el)
        else:
            out.append([el])
    return out


class _Nodes:
    """Punkte, die höchstens eps voneinander entfernt sind, teilen sich einen Knoten."""

    def __init__(self, eps: float) -> None:
        self.eps = eps
        self.points: list[Point] = []
        self._cells: dict[tuple[int, int], list[int]] = {}

    def add(self, pt: Point) -> int:
        gx, gy = int(round(pt[0] / self.eps)), int(round(pt[1] / self.eps))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self._cells.get((gx + dx, gy + dy), ()):
                    if math.dist(self.points[i], pt) <= self.eps:
                        return i
        self.points.append(pt)
        self._cells.setdefault((gx, gy), []).append(len(self.points) - 1)
        return len(self.points) - 1


def _stitch(chains: list[Chain]) -> list[Chain]:
    """Verbindet Ketten, die sich an einem Knoten mit Grad 2 treffen, unabhängig von ihrer Reihenfolge in der Datei. Verzweigungen (drei oder mehr Enden) bleiben getrennt."""
    nodes = _Nodes(_JOIN)
    ends = [(nodes.add(ch[0][1]), nodes.add(ch[-1][-1])) for ch in chains]
    at: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)  # Knoten -> (chain, end)
    for ci, (n0, n1) in enumerate(ends):
        at[n0].append((ci, 0))
        at[n1].append((ci, 1))
    link = {}
    for pair in at.values():
        if len(pair) == 2 and pair[0][0] != pair[1][0]:
            link[pair[0]], link[pair[1]] = pair[1], pair[0]
    used: set[int] = set()

    def walk(ci: int, end_in: int) -> Chain:  # Kette ci am Ende end_in betreten (0 = Anfang, 1 = Ende)
        els: Chain = []
        while ci not in used:
            used.add(ci)
            els += chains[ci] if end_in == 0 else [_reversed(el) for el in reversed(chains[ci])]
            nxt = link.get((ci, 1 - end_in))
            if nxt is None:
                break
            ci, end_in = nxt
        return els

    out = []
    for ci in range(len(chains)):  # Zuerst offene Pfade: am Ende beginnen, an das nichts anschließt
        free = [e for e in (0, 1) if (ci, e) not in link]
        if ci not in used and free:
            out.append(walk(ci, free[0]))
    out += [walk(ci, 0) for ci in range(len(chains)) if ci not in used]  # Zyklen
    return out


def _class_chains(flat: list[tuple[float, Element]]) -> dict[float, list[Chain]]:
    """Zusammenhängende Ketten je Strichstärke."""
    by_width: dict[float, list[Element]] = collections.defaultdict(list)
    for width, el in flat:
        by_width[width].append(el)
    return {w: _stitch(_chains(_dedupe_elements(els))) for w, els in by_width.items()}


# ---------------------------------------------------------------------------------------------
# 3. Geraden, Bögen und Kreise an Ketten anpassen
# ---------------------------------------------------------------------------------------------
def _fit_circle(pts: np.ndarray) -> tuple[float, float, float, float]:
    """Kreis durch die Punkte: algebraischer Startwert nach Kasa, danach Gauss-Newton anhand des geometrischen Abstands. Gibt Mittelpunkt x, Mittelpunkt y, Radius und den größten Restfehler zurück."""
    P = np.asarray(pts, float)
    m = P.mean(0)
    Q = P - m
    s = max(float(np.abs(Q).max()), 1e-9)
    Q = Q / s
    A = np.c_[2 * Q[:, 0], 2 * Q[:, 1], np.ones(len(Q))]
    sol = np.linalg.lstsq(A, (Q**2).sum(1), rcond=None)[0]
    cx, cy = sol[0], sol[1]
    r = math.sqrt(max(sol[2] + cx * cx + cy * cy, 1e-18))
    for _ in range(6):
        dx, dy = Q[:, 0] - cx, Q[:, 1] - cy
        d = np.maximum(np.hypot(dx, dy), 1e-12)
        J = np.c_[-dx / d, -dy / d, -np.ones(len(d))]
        step = np.linalg.lstsq(J, -(d - r), rcond=None)[0]
        cx, cy, r = cx + step[0], cy + step[1], r + step[2]
    res = np.abs(np.hypot(Q[:, 0] - cx, Q[:, 1] - cy) - r).max() * s
    return float(m[0] + cx * s), float(m[1] + cy * s), float(r * s), float(res)


def _unwrapped_angles(P: np.ndarray, c: Point) -> np.ndarray:
    return np.unwrap(np.arctan2(P[:, 1] - c[1], P[:, 0] - c[0]))


def _on_line(P: np.ndarray, i: int, j: int, tol: float) -> bool:
    """Liegen die Eckpunkte zwischen i und j höchstens tol von der Geraden zwischen P[i] und P[j] entfernt?"""
    p, d = P[i], P[j] - P[i]
    length = math.hypot(*d)
    if length < 1e-9:
        return False
    mid = P[i + 1 : j]
    if len(mid) == 0:
        return True
    dev = np.abs(d[0] * (mid[:, 1] - p[1]) - d[1] * (mid[:, 0] - p[0])) / length
    return bool(dev.max() <= tol)


def _arc_fit(W: np.ndarray, tol: float) -> _Arc | None:
    """Bogen durch aufeinanderfolgende Eckpunkte W, falls sie auf einem Kreis liegen (Restfehler <= tol) und sich gleichmäßig in kleinen Schritten um dessen Mittelpunkt bewegen, wie bei einem aus Linien angenäherten Bogen."""
    d = np.diff(W, axis=0)
    turn = (np.diff(np.arctan2(d[:, 1], d[:, 0])) + math.pi) % _TAU - math.pi
    if np.abs(turn).max() > math.radians(50):
        return None
    cx, cy, r, res = _fit_circle(W)
    if res > tol or r > 1e5:
        return None
    th = _unwrapped_angles(W, (cx, cy))
    sweep = float(th[-1] - th[0])
    if abs(sweep) < _MIN_ARC_SWEEP or abs(sweep) > _TAU + 0.05:
        return None
    if not np.all(np.diff(th) * np.sign(sweep) > 0):
        return None
    return _Arc((cx, cy), r, float(th[0]), sweep, _pt(W[0]), _pt(W[-1]))


def _turn(a: _Line, b: _Line) -> float:
    """Winkel zwischen den Richtungen zweier Linien."""
    v0, v1 = np.subtract(a.p1, a.p0), np.subtract(b.p1, b.p0)
    d = math.atan2(v1[1], v1[0]) - math.atan2(v0[1], v0[0])
    return abs((d + math.pi) % _TAU - math.pi)


def _glue_curves(pieces: list[_Prim]) -> list[_Prim]:
    """Folgen aus mindestens vier kurzen Einzellinien mit gleichmäßigem Richtungswechsel bilden eine Freiformkurve."""
    out: list[_Prim] = []
    run: list[_Line] = []

    def close() -> None:
        nonlocal run
        out.extend([_Curve([run[0].p0] + [q.p1 for q in run])] if len(run) >= 4 else run)
        run = []

    for piece in pieces:
        if isinstance(piece, _Line) and piece.single:
            if run and _turn(run[-1], piece) > math.radians(40):
                close()
            run.append(piece)
        else:
            close()
            out.append(piece)
    close()
    return out


def _segment(P: np.ndarray, tol: float) -> list[_Prim]:
    """Zerlegt die Eckpunkte einer Polylinie jeweils mit möglichst großem Fenster aus Geraden, Bögen und Freiformkurven."""
    n = len(P)
    pieces: list[_Prim] = []
    i = 0
    while i < n - 1:
        jl = i + 1
        while jl + 1 < n and _on_line(P, i, jl + 1, tol):
            jl += 1
        ja, arc = i, None
        if n - i >= _MIN_ARC_POINTS:
            # Bekannte Grenze: Das erste Fenster aus _MIN_ARC_POINTS Eckpunkten muss bereits mindestens
            # _MIN_ARC_SWEEP überstreichen; Bögen mit feinerer Unterteilung als 7,5 Grad je Schritt bleiben Geraden.
            for j in range(i + _MIN_ARC_POINTS - 1, n):
                fit = _arc_fit(P[i : j + 1], tol)
                if fit is None:
                    break
                ja, arc = j, fit
        if arc is not None and ja > jl:
            pieces.append(arc)
            i = ja
        else:
            pieces.append(_Line(_pt(P[i]), _pt(P[jl]), single=(jl == i + 1)))
            i = jl
    return _glue_curves(pieces)


def _closed_circle(Q: np.ndarray, tol: float) -> _Circle | None:
    """Der Kreis, falls die Eckpunkte Q (ohne den wiederholten ersten Punkt) einen aus Linien angenäherten Kreis bilden."""
    cx, cy, r, res = _fit_circle(Q)
    if res > tol or r >= 1e5:
        return None
    th = _unwrapped_angles(np.vstack([Q, Q[:1]]), (cx, cy))
    steps = np.diff(th)
    one_turn = abs(abs(th[-1] - th[0]) - _TAU) < 0.05
    monotonic = np.all(steps > 0) or np.all(steps < 0)
    if monotonic and one_turn and np.abs(steps).max() < math.radians(45):
        return _Circle((cx, cy), r)
    return None


def _poly_prims(pts: list[Point], tol: float) -> list[_Prim]:
    """Grundelemente einer Polylinie: bei geschlossenem Verlauf ein aus Linien angenäherter Kreis, andernfalls Geraden und Bögen."""
    P = [pts[0]]
    for p in pts[1:]:  # Wiederholte Eckpunkte entfernen
        if math.dist(p, P[-1]) > 1e-4:
            P.append(p)
    if len(P) < 2:
        return []
    if len(P) >= 10 and math.dist(P[0], P[-1]) <= _JOIN:
        circle = _closed_circle(np.asarray(P[:-1]), tol)
        if circle:
            return [circle]
    return _segment(np.asarray(P, float), tol)


def _bezier_arc(
    p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, mid: np.ndarray, tol: float
) -> _Arc | None:
    """Der Bogen einer kubischen Bézierkurve: Die Normalen an beiden Enden schneiden sich im Mittelpunkt. Dieser liegt vom Kurvenmittelpunkt genauso weit entfernt wie von den Endpunkten."""
    d0 = p1 - p0 if np.linalg.norm(p1 - p0) > 1e-9 else p2 - p0
    d3 = p3 - p2 if np.linalg.norm(p3 - p2) > 1e-9 else p3 - p1
    n0, n3 = np.array([-d0[1], d0[0]]), np.array([-d3[1], d3[0]])
    M = np.array([[n0[0], -n3[0]], [n0[1], -n3[1]]])
    if abs(np.linalg.det(M)) <= 1e-6 * (np.linalg.norm(n0) * np.linalg.norm(n3)):
        return None  # Parallele Tangenten: kein einzelner Mittelpunkt
    c = p0 + np.linalg.solve(M, p3 - p0)[0] * n0
    r0, r3 = np.linalg.norm(p0 - c), np.linalg.norm(p3 - c)
    if abs(r0 - r3) > tol or abs(np.linalg.norm(mid - c) - r0) > 1e-3 * r0 + tol:
        return None
    a0, a3, am = (math.atan2(*(q - c)[::-1]) for q in (p0, p3, mid))
    sweep = (a3 - a0 + math.pi) % _TAU - math.pi
    to_mid = (am - a0 + math.pi) % _TAU - math.pi
    if abs(to_mid) > abs(sweep) + 1e-6 or np.sign(to_mid) != np.sign(sweep):
        sweep -= math.copysign(_TAU, sweep)  # Langer Bogen: Der Mittelpunkt liegt nicht auf dem kurzen Weg
    return _Arc(_pt(c), float((r0 + r3) / 2), a0, sweep, _pt(p0), _pt(p3))


def _bezier_prim(el: Element, tol: float) -> _Prim:
    """Kubische Bézierkurve als Gerade (kollineare Kontrollpunkte), Bogen oder Freiformkurve."""
    p0, p1, p2, p3 = (np.array(q, float) for q in el[1:5])
    chord = p3 - p0
    length = np.linalg.norm(chord)

    def off(q: np.ndarray) -> float:  # Abstand eines Kontrollpunkts von der Sehne
        if length <= 1e-9:
            return float(np.linalg.norm(q - p0))
        return abs(chord[0] * (q[1] - p0[1]) - chord[1] * (q[0] - p0[0])) / length

    if length > 1e-9 and off(p1) < tol and off(p2) < tol:
        return _Line(_pt(p0), _pt(p3))
    t = np.linspace(0, 1, 9)[:, None]
    pts = ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * t * t * p2 + t**3 * p3
    return _bezier_arc(p0, p1, p2, p3, pts[4], tol) or _Curve([_pt(q) for q in pts])


def _chain_prims(chain: Chain, tol: float) -> list[_Prim]:
    """Grundelemente einer Kette: Eine Folge von Linien wird als eine Polylinie angepasst, jede Bézierkurve einzeln."""
    prims: list[_Prim] = []
    run: list[Point] = []
    for el in chain:
        if el[0] == "l":
            run = run or [el[1]]
            run.append(el[2])
        else:
            if run:
                prims += _poly_prims(run, tol)
                run = []
            prims.append(_bezier_prim(el, tol))
    if run:
        prims += _poly_prims(run, tol)
    return prims


# ---------------------------------------------------------------------------------------------
# 4. Zusammenführen: Bögen eines Kreises ergeben einen Kreis oder längeren Bogen; doppelte Linien entfallen
# ---------------------------------------------------------------------------------------------
def _cluster(rounds: list[_Arc | _Circle]) -> list[list[_Arc | _Circle]]:
    """Gruppiert Bögen und Kreise, deren Mittelpunkt und Radius innerhalb einer Toleranz übereinstimmen."""
    clusters: list[list[_Arc | _Circle]] = []
    for p in rounds:
        tol = 0.05 + 5e-4 * p.r
        for members in clusters:
            head = members[0]
            if math.dist(head.c, p.c) <= tol and abs(head.r - p.r) <= tol:
                members.append(p)
                break
        else:
            clusters.append([p])
    return clusters


def _covered(arcs: list[_Arc]) -> list[list[float]]:
    """Winkelintervalle, die die Bögen gemeinsam abdecken; überlappende oder angrenzende Intervalle werden verbunden."""
    ivs = []
    for a in arcs:
        lo = min(a.a0, a.a0 + a.sw) % _TAU
        hi = lo + abs(a.sw)
        ivs += [(lo, _TAU), (0.0, hi - _TAU)] if hi > _TAU else [(lo, hi)]
    ivs.sort()
    merged: list[list[float]] = []
    for lo, hi in ivs:
        if merged and lo <= merged[-1][1] + 0.012:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    if len(merged) > 1 and merged[0][0] <= 0.012 and merged[-1][1] >= _TAU - 0.012:
        merged[0][0] = merged[-1][0] - _TAU  # Ein Intervall über die Richtung 0 Grad
        merged.pop()
    return merged


def _merge_arcs(rounds: list[_Arc | _Circle]) -> list[_Arc | _Circle]:
    """Führt Bögen und Kreise desselben Kreises zusammen: Sie decken gemeinsam den Vollwinkel (Kreis) ab oder lassen Lücken (längere Bögen). Mittelpunkt und Radius werden nach dem überstrichenen Winkel gewichtet gemittelt."""
    out: list[_Arc | _Circle] = []
    for members in _cluster(rounds):
        w = [max(abs(p.sw), 1e-3) if isinstance(p, _Arc) else _TAU for p in members]
        cx = float(np.average([p.c[0] for p in members], weights=w))
        cy = float(np.average([p.c[1] for p in members], weights=w))
        r = float(np.average([p.r for p in members], weights=w))
        merged = _covered([p for p in members if isinstance(p, _Arc)])
        full = any(isinstance(p, _Circle) for p in members)
        if full or sum(hi - lo for lo, hi in merged) >= _TAU - 0.02:
            out.append(_Circle((cx, cy), r))
            continue
        for lo, hi in merged:
            p0 = (cx + r * math.cos(lo), cy + r * math.sin(lo))
            p1 = (cx + r * math.cos(hi), cy + r * math.sin(hi))
            out.append(_Arc((cx, cy), r, lo, hi - lo, p0, p1))
    return out


def _class_prims(chains: list[Chain], tol: float) -> list[_Prim]:
    """Kreise, Bögen, Linien und Freiformkurven einer Strichklasse. Kollineare Linien werden nicht zusammengefügt:
        Eine Maßhilfslinie kann eine Konturkante fortsetzen und muss getrennt bleiben, sonst schließt sich die Kontur nicht.
    """
    prims = [p for ch in chains for p in _chain_prims(ch, tol)]
    rounds = _merge_arcs([p for p in prims if isinstance(p, (_Arc, _Circle))])
    return [
        *(p for p in rounds if isinstance(p, _Circle)),
        *(p for p in rounds if isinstance(p, _Arc)),
        *(p for p in prims if isinstance(p, _Line)),
        *(p for p in prims if isinstance(p, _Curve)),
    ]


# ---------------------------------------------------------------------------------------------
# 5. Konturen: zusammenhängende Teile der sichtbaren Kanten
# ---------------------------------------------------------------------------------------------
@dataclass
class _Contour:
    """Zusammenhängender Teil der sichtbaren Kanten. Ein Flächeninhalt ist nur gesetzt, wenn genau eine geschlossene Schleife vorliegt."""

    prims: list[_Prim]
    bbox: Box
    area: float | None

    @property
    def rank(self) -> float:
        if self.area is not None:
            return self.area
        return (self.bbox[2] - self.bbox[0]) * (self.bbox[3] - self.bbox[1])


Adjacency = dict[int, list[int]]  # Knoten -> Indizes der dort zusammentreffenden Teile oder Kreise


def _prune_dangling(ends: list[tuple[int, int]], on: Adjacency) -> set[int]:
    """Gibt die Indizes der verbleibenden Teile zurück, nachdem wiederholt alle Teile entfernt wurden, die nur an einem Ende mit dem Rest verbunden sind (Maßhilfslinien, Hinweislinien, Maßlinien). Ein Endpunkt auf einem Kreis gilt als verbunden."""
    alive = set(range(len(ends)))
    while True:
        deg: collections.Counter[int] = collections.Counter()
        for i in alive:
            deg.update(ends[i])
        dead = {
            i
            for i in alive
            if ends[i][0] == ends[i][1] or any(deg[nd] + len(on[nd]) < 2 for nd in ends[i])
        }
        if not dead:
            return alive
        alive -= dead


def _components(
    n: int, n_circles: int, at: Adjacency, on: Adjacency, alive: list[int]
) -> list[list[int]]:
    """Gruppiert die aktiven Teile (0..n-1) und Kreise (n..), die einen Knoten teilen. Union-Find."""
    parent = list(range(n + n_circles))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for node, ids in at.items():
        for j in ids[1:]:
            parent[find(j)] = find(ids[0])
        for ci in on[node]:
            parent[find(n + ci)] = find(ids[0])
    groups: dict[int, list[int]] = collections.defaultdict(list)
    for i in [*alive, *range(n, n + n_circles)]:
        groups[find(i)].append(i)
    return list(groups.values())


def _loop_area(
    idx: list[int], pieces: list[_Prim], ends: list[tuple[int, int]], at: Adjacency
) -> float | None:
    """Von den Teilen idx eingeschlossene Fläche, wenn sie eine geschlossene Schleife bilden, sonst None. Jeder Knoten verbindet genau zwei Teile; der Rundgang vom ersten Teil führt daher wieder zu ihm zurück."""
    order, cur, node = [], idx[0], ends[idx[0]][0]
    while True:
        a, b = ends[cur]
        forward = node == a
        order.append((cur, forward))
        node = b if forward else a
        nxt = [j for j in at[node] if j != cur]
        if not nxt or nxt[0] == idx[0]:
            break
        cur = nxt[0]
    if len(order) != len(idx):
        return None
    return abs(sum(pieces[i].area_term(fwd) for i, fwd in order))


def _contours(prims: list[_Prim]) -> list[_Contour]:
    """Zusammenhängende Teile der sichtbaren Kanten. Ein Linienende auf einem vollständigen Kreis gilt als verbunden (tangentiale Gurtlinien zwischen Laschen)."""
    circles = [p for p in prims if isinstance(p, _Circle)]
    pieces = [p for p in prims if not isinstance(p, _Circle)]
    nodes = _Nodes(_SNAP)
    ends = [(nodes.add(p.p0), nodes.add(p.p1)) for p in pieces]
    on: Adjacency = collections.defaultdict(list)  # Knoten -> Kreise, auf denen er liegt
    for ni, pt in enumerate(nodes.points):
        for ci, c in enumerate(circles):
            if abs(math.dist(pt, c.c) - c.r) <= _SNAP + 1e-4 * c.r:
                on[ni].append(ci)
    alive = sorted(_prune_dangling(ends, on))
    at: Adjacency = collections.defaultdict(list)  # Knoten -> aktive Teile, die an ihm enden
    for i in alive:
        at[ends[i][0]].append(i)
        at[ends[i][1]].append(i)
    n = len(pieces)
    out = []
    for members in _components(n, len(circles), at, on, alive):
        idx = sorted(i for i in members if i < n)
        loops = [circles[i - n] for i in members if i >= n]
        group = [pieces[i] for i in idx] + loops
        area = None
        if not idx and len(loops) == 1:
            area = math.pi * loops[0].r ** 2
        elif idx and not loops and all(len(at[nd]) == 2 for i in idx for nd in ends[i]):
            area = _loop_area(idx, pieces, ends, at)
        out.append(_Contour(group, _union([p.bbox() for p in group]), area))
    return out


# ---------------------------------------------------------------------------------------------
# 6. Strichklassen: Welche Stärke trägt die sichtbaren Kanten?
# ---------------------------------------------------------------------------------------------
def _span(chain: Chain) -> tuple[float, float]:
    """Breite und Höhe (pt) des Rahmens um eine Kette."""
    x0, y0, x1, y1 = _bounds([q for el in chain for q in el[1:]])
    return x1 - x0, y1 - y0


def _is_frame(chain: Chain, width: float, height: float) -> bool:
    """Blattrahmen: ein geschlossenes Rechteck oder eine einzelne gerade Kante, die über das Blatt verläuft."""
    bw, bh = _span(chain)
    return (bw >= 0.8 * width and bh >= 0.8 * height) or (
        len(chain) == 1 and max(bw, bh) >= 0.8 * min(width, height)
    )


def _score(chains: list[Chain], width: float, height: float) -> float:
    """Gezeichnete Länge (pt) der Ketten, die lang genug sind, um Teil einer Ansicht zu sein."""
    diagonal = math.hypot(width, height)
    return sum(
        math.dist(el[1], el[-1])
        for ch in chains
        if max(_span(ch)) >= 0.02 * diagonal
        for el in ch
    )


def _visible_widths(scores: dict[float, float], edge_width: str | float) -> list[float]:
    """Die als sichtbare Kanten berücksichtigten Strichstärken."""
    if edge_width == "all":
        return list(scores)
    if edge_width == "auto":
        best = max(scores.values(), default=0.0)
        return [max(w for w, s in scores.items() if s >= 0.12 * best)] if best > 0 else []
    try:
        target = float(edge_width)
    except ValueError:
        raise ValueError(
            f'`edge_width` muss "auto", "all" oder eine Linienbreite in pt sein; erhalten: {edge_width!r}.'
        ) from None
    return [min(scores, key=lambda w: abs(w - target))] if scores else []


# ---------------------------------------------------------------------------------------------
# 7. Öffentliche API
# ---------------------------------------------------------------------------------------------
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


def _check_region(region: Sequence[float] | None) -> Box:
    box = (0.0, 0.0, 1.0, 1.0) if region is None else tuple(region)
    if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        raise ValueError(
            "`region` muss (x0, y0, x1, y1) als Seitenanteile mit x0 < x1 und "
            f"y0 < y1 sein; erhalten: {region!r}."
        )
    return box


def _read_page(pdf_path: str | PathLike[str], page: int) -> tuple[float, float, list]:
    """Größe (pt) der angezeigten Seite und ihrer umrandeten Elemente."""
    with pymupdf.open(pdf_path) as doc:
        if not 1 <= page <= len(doc):
            raise ValueError(f"`page` muss zwischen 1 und {len(doc)} liegen; erhalten: {page}.")
        pg = doc[page - 1]
        return pg.rect.width, pg.rect.height, _collect(pg)


def _inside(p: _Prim, box: Box) -> bool:
    x, y = p.mid()
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _line_row(p: _Line, f: _Frame, in_contour: bool) -> dict:
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


def _contour_row(c: _Contour, f: _Frame) -> dict:
    x0, y0, x1, y1 = c.bbox
    return {
        "bbox_mm": [f.x(x0), f.y(y1), f.x(x1), f.y(y0)],
        "w_mm": f.mm(x1 - x0),
        "h_mm": f.mm(y1 - y0),
        "closed_loop": c.area is not None,
        "area_mm2": None if c.area is None else round(float(c.area * f.k * f.k), 3),
    }


def _extent(
    prims: list[_Prim], contours: list[_Contour], f: _Frame
) -> tuple[list[float] | None, str | None]:
    """Breite und Höhe der größten Kontur oder, falls es keine gibt, aller Grundelemente; None, wenn keine vorhanden sind."""
    if contours:
        box, basis = contours[0].bbox, "größte Kontur"
    elif prims:
        box, basis = _union([p.bbox() for p in prims]), "alle sichtbaren Grundelemente"
    else:
        return None, None
    return [f.mm(box[2] - box[0]), f.mm(box[3] - box[1])], basis


def _rows(prims: list[_Prim], contours: list[_Contour], f: _Frame) -> dict:
    """Kreise, Bögen, Linien und Konturen eines Ergebnisses, absteigend nach Größe."""
    in_contour = {id(p) for c in contours for p in c.prims}
    circles = [
        {"cx": f.x(p.c[0]), "cy": f.y(p.c[1]), "d_mm": f.mm(2 * p.r)}
        for p in prims
        if isinstance(p, _Circle)
    ]
    arcs = [
        {
            "cx": f.x(p.c[0]),
            "cy": f.y(p.c[1]),
            "r_mm": f.mm(p.r),
            "sweep": round(abs(math.degrees(p.sw)), 2),
        }
        for p in prims
        if isinstance(p, _Arc)
    ]
    lines = [_line_row(p, f, id(p) in in_contour) for p in prims if isinstance(p, _Line)]
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
            höchsten Anteil; extent_mm [width, height] mit extent_basis ("größte Kontur" oder "alle sichtbaren Grundelemente", None, wenn der Bereich keine Geometrie enthält); circles (cx, cy, d_mm) und arcs (cx,
            cy, r_mm, sweep in Grad), absteigend nach Größe; lines (x0, y0, x1, y1, length_mm, angle_deg,
            in_contour), absteigend nach Länge; contours (bbox_mm [x0, y0, x1, y1], w_mm, h_mm, closed_loop,
            area_mm2), absteigend nach Größe. Koordinaten und Maße sind Modell-mm, y zeigt nach oben, Ursprung
            links unten im Bereich.
    """
    if not scale > 0:
        raise ValueError(f"`scale` muss positiv sein; erhalten: {scale}.")
    rx0, ry0, rx1, ry1 = _check_region(region)
    width, height, flat = _read_page(pdf_path, page)
    classes = {
        w: [ch for ch in chains if not _is_frame(ch, width, height)]
        for w, chains in _class_chains(flat).items()
    }
    scores = {w: _score(chains, width, height) for w, chains in classes.items()}
    visible = _visible_widths(scores, edge_width)

    tol = max(_FIT_TOL, 0.75 * _grid(flat))
    box = (rx0 * width, ry0 * height, rx1 * width, ry1 * height)  # pt, y nach unten
    prims = [p for w in visible for p in _class_prims(classes[w], tol) if _inside(p, box)]
    contours = sorted(_contours(prims), key=lambda c: c.rank, reverse=True)
    f = _Frame(ox=box[0], oy=box[3], k=_PT_MM / scale)
    extent, basis = _extent(prims, contours, f)
    return {
        "scale_used": float(scale),
        "styles": [
            {"width": w, "score": round(s, 1), "visible": w in visible}
            for w, s in sorted(scores.items(), reverse=True)
        ],
        "extent_mm": extent,
        "extent_basis": basis,
        **_rows(prims, contours, f),
    }


# ---------------------------------------------------------------------------------------------
# 8. Aus der Zeichnung gelesene Maße und Maßstab mit der Geometrie abgleichen
# ---------------------------------------------------------------------------------------------
Candidates = dict[str, list[tuple[float, str]]]  # Art (d, r, L) -> (Wert in mm, Herkunft)


def _round_candidates(res: dict, pool: Candidates) -> None:
    for c in res["circles"]:
        where = f"Kreis bei ({c['cx']},{c['cy']})"
        pool["d"].append((c["d_mm"], where))
        pool["r"].append((round(c["d_mm"] / 2, 4), where))
    for a in res["arcs"]:
        where = f"Bogen {a['sweep']} Grad bei ({a['cx']},{a['cy']})"
        pool["r"].append((a["r_mm"], where))
        pool["d"].append((round(2 * a["r_mm"], 4), f"{where} (2r)"))


def _edge_candidates(res: dict, pool: Candidates) -> None:
    for ln in res["lines"]:
        pool["L"].append((ln["length_mm"], f"Linie ({ln['x0']},{ln['y0']})-({ln['x1']},{ln['y1']})"))
    for c in res["contours"]:
        x, y = c["bbox_mm"][:2]
        pool["L"].append((c["w_mm"], f"Konturbreite @({x},{y})"))
        pool["L"].append((c["h_mm"], f"Konturhöhe @({x},{y})"))
    if res["extent_mm"]:
        pool["L"].append((res["extent_mm"][0], "Außenbreite"))
        pool["L"].append((res["extent_mm"][1], "Außenhöhe"))


def _centre_candidates(res: dict, pool: Candidates) -> None:
    """Abstände zwischen Mittelpunkten sowie zwischen einem Mittelpunkt und einer mindestens 3 mm langen Linie."""
    centres = sorted({(p["cx"], p["cy"]) for p in res["circles"] + res["arcs"]})
    for i, a in enumerate(centres):
        for b in centres[i + 1 :]:
            dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
            for v, name in ((dx, "dx"), (dy, "dy"), (math.hypot(dx, dy), "dist")):
                if v > 1e-3:
                    pool["L"].append((round(v, 4), f"Mittelpunktabstand {name} {a}-{b}"))
    for cx, cy in centres:
        for ln in res["lines"]:
            if ln["length_mm"] < 3:
                continue
            dx, dy = ln["x1"] - ln["x0"], ln["y1"] - ln["y0"]
            off = abs(dx * (cy - ln["y0"]) - dy * (cx - ln["x0"])) / math.hypot(dx, dy)
            if off > 1e-3:
                line = f"({ln['x0']},{ln['y0']})-({ln['x1']},{ln['y1']})"
                pool["L"].append((round(off, 4), f"Abstand Mittelpunkt ({cx},{cy}) zur Linie {line}"))


def _parallel_candidates(res: dict, pool: Candidates) -> None:
    """Abstand zwischen parallelen Linien, die länger als 1 mm sind."""
    lines = [ln for ln in res["lines"] if ln["length_mm"] > 1]
    for i, a in enumerate(lines):
        for b in lines[i + 1 :]:
            if abs((a["angle_deg"] - b["angle_deg"] + 90) % 180 - 90) < 0.05:
                ang = math.radians(a["angle_deg"])
                dx, dy = b["x0"] - a["x0"], b["y0"] - a["y0"]
                off = abs(-dx * math.sin(ang) + dy * math.cos(ang))
                if off > 1e-3:
                    lengths = f"{a['length_mm']}/{b['length_mm']}"
                    pool["L"].append((round(off, 4), f"Abstand paralleler Linien (Längen {lengths})"))


def _candidates(res: dict) -> Candidates:
    """Alle Durchmesser (d), Radien (r) und Längen (L), die sich aus der Geometrie messen lassen: Kanten, Konturmaße, Mittelpunktabstände sowie Abstände zwischen Mittelpunkten und Linien oder zwischen Linien."""
    pool: Candidates = {"d": [], "r": [], "L": []}
    _round_candidates(res, pool)
    _edge_candidates(res, pool)
    _centre_candidates(res, pool)
    _parallel_candidates(res, pool)
    return pool


_CLAIM = re.compile(r"\s*([drL])\s*=\s*(\d+(?:[.,]\d+)?)\s*")


def _parse_claim(claim: str) -> tuple[str, float]:
    m = _CLAIM.fullmatch(claim)
    if not m:
        raise ValueError(f"`claim` muss das Format 'd=19', 'r=10' oder 'L=49' haben; erhalten: {claim!r}.")
    return m[1], float(m[2].replace(",", "."))


def verify(res: dict, claims: Sequence[str], tol: float = 0.05) -> list[dict]:
    """Prüft aus der Zeichnung gelesene Maße anhand der mit extract gemessenen Geometrie.

        Ein Prüfwert lautet in mm "d=19" (Durchmesser), "r=10" (Radius) oder "L=49" (Länge); ein Dezimalkomma ist
        zulässig. Je Prüfwert werden der nächstliegende Messwert (measured_mm, deviation_mm = Messwert - Sollwert,
        matched = Herkunft), der Status "ok" innerhalb tol, "MISMATCH" außerhalb oder "no_candidates", wenn es
        keinen Messwert dieser Art gibt, sowie n_candidates_within_tol zurückgegeben. Eine dichte Ansicht enthält viele
        Längen; ein L-Maß kann dort zufällig passen. Dafür den Bereich auf die Ansicht begrenzen.
    """
    pool = _candidates(res)
    verdicts = []
    for claim in claims:
        kind, value = _parse_claim(claim)
        found = pool[kind]
        verdict = {
            "claim": claim,
            "status": "no_candidates",
            "measured_mm": None,
            "deviation_mm": None,
            "matched": None,
            "n_candidates_within_tol": 0,
        }
        if found:
            measured, matched = min(found, key=lambda c: abs(c[0] - value))
            verdict.update(
                status="ok" if abs(measured - value) <= tol else "MISMATCH",
                measured_mm=measured,
                deviation_mm=round(measured - value, 4),
                matched=matched,
                n_candidates_within_tol=sum(abs(v - value) <= tol for v, _ in found),
            )
        verdicts.append(verdict)
    return verdicts


def _standard(scale: float) -> float | None:
    """Der Standardmaßstab innerhalb von 0,5 % von scale, falls vorhanden."""
    near = min(STANDARD_SCALES, key=lambda t: abs(t - scale) / t)
    return near if abs(near - scale) / near < 0.005 else None


def suggest_scale(
    pdf_path: str | PathLike[str],
    printed_values: Sequence[float],
    page: int = 1,
    region: Sequence[float] | None = None,
) -> list[dict]:
    """Ermittelt anhand der aufgedruckten Maße den tatsächlichen Maßstab einer Zeichnung und macht einen falschen Maßstab im Schriftfeld sichtbar.

        Jede gemessene Größe der Seite (in Blatt-mm), geteilt durch einen gedruckten Wert, ergibt einen möglichen
        Maßstab. Jeder gedruckte Wert gibt einmal je unterstütztem Maßstab eine Stimme ab. Mindestens sechs Maße aus den
        Ansichten angeben: Bei wenigen Werten lassen sich Zufallstreffer nicht vom tatsächlichen Maßstab unterscheiden.

        Gibt höchstens fünf Wörterbücher {"scale", "votes", "standard"} zurück, sortiert nach den meisten Stimmen,
        danach nach einem Standardmaßstab vor einem nicht standardisierten Wert und schließlich nach dem Verhältnis
        am nächsten zu 1:1 (verwandte Maßreihen können zwei Verhältnisse mit allen Stimmen ergeben, z. B.
        0.3 = 0.4 * 0.75). "standard" ist der Standardmaßstab innerhalb von 0,5 % von "scale", andernfalls None:
        Ein Maßstab von 0.75 muss als nicht standardisierter Wert sichtbar bleiben.
    """
    if any(v <= 0 for v in printed_values):
        raise ValueError(f"`printed_values` müssen positiv sein; erhalten: {list(printed_values)}.")
    pool = _candidates(extract(pdf_path, page, region))
    drawn = [v for kind in ("d", "r", "L") for v, _ in pool[kind]]
    votes: collections.Counter[float] = collections.Counter()
    for printed in printed_values:
        votes.update({round(v / printed, 3) for v in drawn if 0.03 < v / printed < 30})
    ranked = [{"scale": s, "votes": n, "standard": _standard(s)} for s, n in votes.items()]
    ranked.sort(
        key=lambda e: (-e["votes"], e["standard"] is None, abs(math.log(e["scale"])), e["scale"])
    )
    return ranked[:5]

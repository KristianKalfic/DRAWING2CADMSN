"""Umrandete Geometrie der angezeigten Seite einlesen und zu Ketten zusammenfügen."""

import collections
import math

import numpy as np
import pymupdf

from d2c.drawing.vector.primitives import Chain, Element, Point

JOIN = 0.02  # pt: Endpunkte der Stream-Elemente gelten bis zu diesem Abstand als zusammenhängend
# Koordinatenraster (pt) rasterbasierter Exporte. Die feinere Ausgabe anderer Exporte passt zu keinem dieser Raster.
_LATTICES = (1.0, 0.5, 0.25, 0.2, 0.12, 0.1, 0.06, 0.05, 0.025, 0.02, 0.01)


def _corners(item: tuple) -> list[Point]:
    """Eckpunkte eines umrandeten Rechtecks (re) oder Vierecks (qu), in Pfadreihenfolge."""
    if item[0] == "re":
        r = item[1]
        return [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
    q = item[1]
    return [q.ul, q.ur, q.lr, q.ll]


def collect(page: pymupdf.Page) -> list[tuple[float, Element]]:
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


def grid(flat: list[tuple[float, Element]]) -> float:
    """Abstand (pt) des Koordinatenrasters der Datei; 0, wenn es keines gibt. Die Anpassungen müssen die Rundungsabweichung tolerieren."""
    if not flat:
        return 0.0
    xs = np.array([p[0] for _, el in flat for p in el[1:]])
    for q in _LATTICES:
        if abs(np.mean(np.exp(2j * np.pi * xs / q))) > 0.95:
            return q
    return 0.0


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
    """Teilt einen Datenstrom dort, wo ein Element nicht am Ende des vorherigen beginnt. Exportierte Polylinien sind durchgehend (Lücken sind 0 oder mindestens 0,05 pt)."""
    out: list[Chain] = []
    for el in els:
        if out and math.dist(out[-1][-1][-1], el[1]) <= JOIN:
            out[-1].append(el)
        else:
            out.append([el])
    return out


class Nodes:
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
    nodes = Nodes(JOIN)
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


def class_chains(flat: list[tuple[float, Element]]) -> dict[float, list[Chain]]:
    """Zusammenhängende Ketten je Strichstärke."""
    by_width: dict[float, list[Element]] = collections.defaultdict(list)
    for width, el in flat:
        by_width[width].append(el)
    return {w: _stitch(_chains(_dedupe_elements(els))) for w, els in by_width.items()}

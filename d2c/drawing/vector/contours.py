"""Konturen aus den sichtbaren Kanten bilden und die Strichklasse der sichtbaren Kanten bestimmen."""

import collections
import math
from dataclasses import dataclass

from d2c.drawing.vector.chains import Nodes
from d2c.drawing.vector.primitives import Box, Chain, Circle, Prim, bounds, union

_SNAP = 0.05  # pt: Endpunkte der Konturteile gelten bis zu diesem Abstand als verbunden


@dataclass
class Contour:
    """Zusammenhängender Teil der sichtbaren Kanten. Ein Flächeninhalt ist nur gesetzt, wenn genau eine geschlossene Schleife vorliegt."""

    prims: list[Prim]
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
        dead = {i for i in alive if ends[i][0] == ends[i][1] or any(deg[nd] + len(on[nd]) < 2 for nd in ends[i])}
        if not dead:
            return alive
        alive -= dead


def _components(n: int, n_circles: int, at: Adjacency, on: Adjacency, alive: list[int]) -> list[list[int]]:
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


def _loop_area(idx: list[int], pieces: list[Prim], ends: list[tuple[int, int]], at: Adjacency) -> float | None:
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


def find_contours(prims: list[Prim]) -> list[Contour]:
    """Zusammenhängende Teile der sichtbaren Kanten. Ein Linienende auf einem vollständigen Kreis gilt als verbunden (tangentiale Gurtlinien zwischen Laschen)."""
    circles = [p for p in prims if isinstance(p, Circle)]
    pieces = [p for p in prims if not isinstance(p, Circle)]
    nodes = Nodes(_SNAP)
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
        out.append(Contour(group, union([p.bbox() for p in group]), area))
    return out


def _span(chain: Chain) -> tuple[float, float]:
    """Breite und Höhe (pt) des Rahmens um eine Kette."""
    x0, y0, x1, y1 = bounds([q for el in chain for q in el[1:]])
    return x1 - x0, y1 - y0


def is_frame(chain: Chain, width: float, height: float) -> bool:
    """Blattrahmen: ein geschlossenes Rechteck oder eine einzelne gerade Kante, die über das Blatt verläuft."""
    bw, bh = _span(chain)
    return (bw >= 0.8 * width and bh >= 0.8 * height) or (len(chain) == 1 and max(bw, bh) >= 0.8 * min(width, height))


def score(chains: list[Chain], width: float, height: float) -> float:
    """Gezeichnete Länge (pt) der Ketten, die lang genug sind, um Teil einer Ansicht zu sein."""
    diagonal = math.hypot(width, height)
    return sum(math.dist(el[1], el[-1]) for ch in chains if max(_span(ch)) >= 0.02 * diagonal for el in ch)


def visible_widths(scores: dict[float, float], edge_width: str | float) -> list[float]:
    """Die als sichtbare Kanten berücksichtigten Strichstärken."""
    if edge_width == "all":
        return list(scores)
    if edge_width == "auto":
        best = max(scores.values(), default=0.0)
        return [max(w for w, s in scores.items() if s >= 0.12 * best)] if best > 0 else []
    try:
        target = float(edge_width)
    except ValueError:
        raise ValueError(f'`edge_width` must be "auto", "all" or a line width in pt; received: {edge_width!r}.') from None
    return [min(scores, key=lambda w: abs(w - target))] if scores else []

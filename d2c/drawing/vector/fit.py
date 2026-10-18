"""Geraden, Bögen und Kreise an Ketten anpassen und Bögen desselben Kreises zusammenführen."""

import math

import numpy as np

from d2c.drawing.vector.chains import JOIN
from d2c.drawing.vector.primitives import (
    Arc,
    Chain,
    Circle,
    Curve,
    Element,
    Line,
    Point,
    Prim,
    to_point,
)

_TAU = 2 * math.pi
_MIN_ARC_POINTS = 9  # Weniger Eckpunkte lassen die Ellipse der isometrischen Ansicht nicht von einem Kreis unterscheiden
_MIN_ARC_SWEEP = math.radians(60)
_FULL_TURN_TOL = 0.05  # rad: größte Abweichung eines überstrichenen Winkels vom Vollwinkel
_BEZIER_RADIUS_TOL = 1e-3  # relativ: Abweichung des Kurvenmittelpunkts vom Radius der Bézier-Bögen
_MIN_ARC_WEIGHT = 1e-3  # rad: Mindestgewicht eines Bogens beim gewichteten Mittel
_CLUSTER_TOL = 0.05  # pt: Mittelpunkt und Radius gelten bis zu dieser Abweichung als gleich ...
_CLUSTER_TOL_REL = 5e-4  # ... zuzüglich dieses Anteils des Radius


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


def _arc_fit(W: np.ndarray, tol: float) -> Arc | None:
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
    if abs(sweep) < _MIN_ARC_SWEEP or abs(sweep) > _TAU + _FULL_TURN_TOL:
        return None
    if not np.all(np.diff(th) * np.sign(sweep) > 0):
        return None
    return Arc((cx, cy), r, float(th[0]), sweep, to_point(W[0]), to_point(W[-1]))


def _turn(a: Line, b: Line) -> float:
    """Winkel zwischen den Richtungen zweier Linien."""
    v0, v1 = np.subtract(a.p1, a.p0), np.subtract(b.p1, b.p0)
    d = math.atan2(v1[1], v1[0]) - math.atan2(v0[1], v0[0])
    return abs((d + math.pi) % _TAU - math.pi)


def _glue_curves(pieces: list[Prim]) -> list[Prim]:
    """Folgen aus mindestens vier kurzen Einzellinien mit gleichmäßigem Richtungswechsel bilden eine Freiformkurve."""
    out: list[Prim] = []
    run: list[Line] = []

    def close() -> None:
        nonlocal run
        out.extend([Curve([run[0].p0] + [q.p1 for q in run])] if len(run) >= 4 else run)
        run = []

    for piece in pieces:
        if isinstance(piece, Line) and piece.single:
            if run and _turn(run[-1], piece) > math.radians(40):
                close()
            run.append(piece)
        else:
            close()
            out.append(piece)
    close()
    return out


def _segment(P: np.ndarray, tol: float) -> list[Prim]:
    """Zerlegt die Eckpunkte einer Polylinie jeweils mit möglichst großem Fenster aus Geraden, Bögen und Freiformkurven."""
    n = len(P)
    pieces: list[Prim] = []
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
            pieces.append(Line(to_point(P[i]), to_point(P[jl]), single=(jl == i + 1)))
            i = jl
    return _glue_curves(pieces)


def _closed_circle(Q: np.ndarray, tol: float) -> Circle | None:
    """Der Kreis, falls die Eckpunkte Q (ohne den wiederholten ersten Punkt) einen aus Linien angenäherten Kreis bilden."""
    cx, cy, r, res = _fit_circle(Q)
    if res > tol or r >= 1e5:
        return None
    th = _unwrapped_angles(np.vstack([Q, Q[:1]]), (cx, cy))
    steps = np.diff(th)
    one_turn = abs(abs(th[-1] - th[0]) - _TAU) < _FULL_TURN_TOL
    monotonic = np.all(steps > 0) or np.all(steps < 0)
    if monotonic and one_turn and np.abs(steps).max() < math.radians(45):
        return Circle((cx, cy), r)
    return None


def _poly_prims(pts: list[Point], tol: float) -> list[Prim]:
    """Grundelemente einer Polylinie: bei geschlossenem Verlauf ein aus Linien angenäherter Kreis, andernfalls Geraden und Bögen."""
    P = [pts[0]]
    for p in pts[1:]:  # Wiederholte Eckpunkte entfernen
        if math.dist(p, P[-1]) > 1e-4:
            P.append(p)
    if len(P) < 2:
        return []
    if len(P) >= 10 and math.dist(P[0], P[-1]) <= JOIN:
        circle = _closed_circle(np.asarray(P[:-1]), tol)
        if circle:
            return [circle]
    return _segment(np.asarray(P, float), tol)


def _bezier_arc(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, mid: np.ndarray, tol: float) -> Arc | None:
    """Der Bogen einer kubischen Bézierkurve: Die Normalen an beiden Enden schneiden sich im Mittelpunkt. Dieser liegt vom Kurvenmittelpunkt genauso weit entfernt wie von den Endpunkten."""
    d0 = p1 - p0 if np.linalg.norm(p1 - p0) > 1e-9 else p2 - p0
    d3 = p3 - p2 if np.linalg.norm(p3 - p2) > 1e-9 else p3 - p1
    n0, n3 = np.array([-d0[1], d0[0]]), np.array([-d3[1], d3[0]])
    M = np.array([[n0[0], -n3[0]], [n0[1], -n3[1]]])
    if abs(np.linalg.det(M)) <= 1e-6 * (np.linalg.norm(n0) * np.linalg.norm(n3)):
        return None  # Parallele Tangenten: kein einzelner Mittelpunkt
    c = p0 + np.linalg.solve(M, p3 - p0)[0] * n0
    r0, r3 = np.linalg.norm(p0 - c), np.linalg.norm(p3 - c)
    if abs(r0 - r3) > tol or abs(np.linalg.norm(mid - c) - r0) > _BEZIER_RADIUS_TOL * r0 + tol:
        return None
    a0, a3, am = (math.atan2(*(q - c)[::-1]) for q in (p0, p3, mid))
    sweep = (a3 - a0 + math.pi) % _TAU - math.pi
    to_mid = (am - a0 + math.pi) % _TAU - math.pi
    if abs(to_mid) > abs(sweep) + 1e-6 or np.sign(to_mid) != np.sign(sweep):
        sweep -= math.copysign(_TAU, sweep)  # Langer Bogen: Der Mittelpunkt liegt nicht auf dem kurzen Weg
    return Arc(to_point(c), float((r0 + r3) / 2), a0, sweep, to_point(p0), to_point(p3))


def _bezier_prim(el: Element, tol: float) -> Prim:
    """Kubische Bézierkurve als Gerade (kollineare Kontrollpunkte), Bogen oder Freiformkurve."""
    p0, p1, p2, p3 = (np.array(q, float) for q in el[1:5])
    chord = p3 - p0
    length = np.linalg.norm(chord)

    def off(q: np.ndarray) -> float:  # Abstand eines Kontrollpunkts von der Sehne
        if length <= 1e-9:
            return float(np.linalg.norm(q - p0))
        return abs(chord[0] * (q[1] - p0[1]) - chord[1] * (q[0] - p0[0])) / length

    if length > 1e-9 and off(p1) < tol and off(p2) < tol:
        return Line(to_point(p0), to_point(p3))
    t = np.linspace(0, 1, 9)[:, None]
    pts = ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * t * t * p2 + t**3 * p3
    return _bezier_arc(p0, p1, p2, p3, pts[4], tol) or Curve([to_point(q) for q in pts])


def _chain_prims(chain: Chain, tol: float) -> list[Prim]:
    """Grundelemente einer Kette: Eine Folge von Linien wird als eine Polylinie angepasst, jede Bézierkurve einzeln."""
    prims: list[Prim] = []
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


def _cluster(rounds: list[Arc | Circle]) -> list[list[Arc | Circle]]:
    """Gruppiert Bögen und Kreise, deren Mittelpunkt und Radius innerhalb einer Toleranz übereinstimmen."""
    clusters: list[list[Arc | Circle]] = []
    for p in rounds:
        tol = _CLUSTER_TOL + _CLUSTER_TOL_REL * p.r
        for members in clusters:
            head = members[0]
            if math.dist(head.c, p.c) <= tol and abs(head.r - p.r) <= tol:
                members.append(p)
                break
        else:
            clusters.append([p])
    return clusters


def _covered(arcs: list[Arc]) -> list[list[float]]:
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


def _merge_arcs(rounds: list[Arc | Circle]) -> list[Arc | Circle]:
    """Führt Bögen und Kreise desselben Kreises zusammen: Sie decken gemeinsam den Vollwinkel (Kreis) ab oder lassen Lücken (längere Bögen). Mittelpunkt und Radius werden nach dem überstrichenen Winkel gewichtet gemittelt."""
    out: list[Arc | Circle] = []
    for members in _cluster(rounds):
        w = [max(abs(p.sw), _MIN_ARC_WEIGHT) if isinstance(p, Arc) else _TAU for p in members]
        cx = float(np.average([p.c[0] for p in members], weights=w))
        cy = float(np.average([p.c[1] for p in members], weights=w))
        r = float(np.average([p.r for p in members], weights=w))
        merged = _covered([p for p in members if isinstance(p, Arc)])
        full = any(isinstance(p, Circle) for p in members)
        if full or sum(hi - lo for lo, hi in merged) >= _TAU - 0.02:
            out.append(Circle((cx, cy), r))
            continue
        for lo, hi in merged:
            p0 = (cx + r * math.cos(lo), cy + r * math.sin(lo))
            p1 = (cx + r * math.cos(hi), cy + r * math.sin(hi))
            out.append(Arc((cx, cy), r, lo, hi - lo, p0, p1))
    return out


def class_prims(chains: list[Chain], tol: float) -> list[Prim]:
    """Kreise, Bögen, Linien und Freiformkurven einer Strichklasse. Kollineare Linien werden nicht zusammengefügt:
    Eine Maßhilfslinie kann eine Konturkante fortsetzen und muss getrennt bleiben, sonst schließt sich die Kontur nicht.
    """
    prims = [p for ch in chains for p in _chain_prims(ch, tol)]
    rounds = _merge_arcs([p for p in prims if isinstance(p, (Arc, Circle))])
    return [
        *(p for p in rounds if isinstance(p, Circle)),
        *(p for p in rounds if isinstance(p, Arc)),
        *(p for p in prims if isinstance(p, Line)),
        *(p for p in prims if isinstance(p, Curve)),
    ]

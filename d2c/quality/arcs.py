"""Bögen eines path-Profils auf den Radius der Spezifikation legen.

Ein Bogen `arc3` ist durch Start, Zwischenpunkt `via` und Ende bestimmt. Das Modell gibt `via` meist auf 0,01 mm gerundet
an; bei einem kurzen Bogen verschiebt das den Radius um einige Tausendstel (R35 wird R34,995). Liegt der Radius eines
Bogens höchstens SNAP_TOL neben einem Radius der Spezifikation (Radiusmaß oder halber Durchmesser), wird `via` auf den
Kreis mit genau diesem Radius durch Start und Ende gelegt. Start und Ende bleiben unverändert, der Mittelpunkt bleibt auf
derselben Seite der Sehne. Reine Funktion über den serialisierten Vorgang, ohne CadQuery.
"""

import copy
import math

SNAP_TOL = 0.02  # mm: größere Abweichungen sind ein anderes Maß, kein Rundungsfehler

Point = tuple[float, float]


def _center(a: Point, b: Point, c: Point) -> Point | None:
    """Mittelpunkt des Kreises durch drei Punkte; None, wenn sie auf einer Geraden liegen."""
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        return None
    sa, sb, sc = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    return (
        (sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d,
        (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d,
    )


def _snapped_via(start: Point, via: Point, end: Point, radius: float, center: Point) -> Point | None:
    """`via` auf dem Kreis mit `radius` durch start und end, Mittelpunkt nahe `center`; None, wenn die Sehne zu lang ist."""
    half = math.dist(start, end) / 2
    if half < 1e-9 or radius < half:
        return None
    mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    normal = ((start[1] - end[1]) / (2 * half), (end[0] - start[0]) / (2 * half))
    offset = math.sqrt(radius**2 - half**2)
    candidates = [(mid[0] + s * offset * normal[0], mid[1] + s * offset * normal[1]) for s in (1, -1)]
    new = min(candidates, key=lambda c: math.dist(c, center))
    length = math.dist(via, new)
    if length < 1e-9:
        return None
    return (new[0] + (via[0] - new[0]) * radius / length, new[1] + (via[1] - new[1]) * radius / length)


def snap_arcs(op: dict, radii: list[float]) -> tuple[dict, list[str]]:
    """Der Vorgang mit Bögen auf den Radien `radii` und je eine Notiz pro verschobenem Bogen; ohne Treffer unverändert."""
    profile = op.get("profile")
    if not radii or not isinstance(profile, dict) or profile.get("type") != "path":
        return op, []
    try:
        current = (float(profile["start"][0]), float(profile["start"][1]))
        segments = list(profile["segments"])
    except (KeyError, IndexError, TypeError, ValueError):
        return op, []  # die Validierung des Vorgangs meldet das
    snapped, notes = None, []
    for i, segment in enumerate(segments):
        try:
            end = (float(segment["to"][0]), float(segment["to"][1]))
        except (KeyError, IndexError, TypeError, ValueError):
            return op, []
        if segment.get("kind") == "arc3":
            try:
                via = (float(segment["via"][0]), float(segment["via"][1]))
            except (KeyError, IndexError, TypeError, ValueError):
                return op, []
            center = _center(current, via, end)
            if center is not None:
                r = math.dist(center, current)
                target = min(radii, key=lambda x: abs(x - r))
                if 1e-6 < abs(target - r) <= SNAP_TOL and (new := _snapped_via(current, via, end, target, center)):
                    snapped = snapped or copy.deepcopy(op)
                    snapped["profile"]["segments"][i]["via"] = [round(new[0], 6), round(new[1], 6)]
                    notes.append(f"profile.segments.{i}: arc radius {r:.4f} → {target:g} (radius of the spec)")
        current = end
    return (snapped, notes) if snapped else (op, [])

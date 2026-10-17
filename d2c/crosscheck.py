"""Prüfungen allein an der PartSpec, bevor es Geometrie gibt: Plausibilitätsregeln und der Vergleich zweier Lesungen.

Jeder Befund trägt den Entwurf eines Prüfpunkts, damit eine Stelle, die nicht stimmen kann, oder über die zwei
unabhängige Lesungen einer Zeichnungsseite streiten, unverändert auf die Prüfliste wandern kann.
"""

import math
from collections.abc import Iterable
from itertools import combinations

from d2c.checks import AXIS_COS
from d2c.spec import Dimension, ExpectedHole, PartSpec

TOL = 0.05  # mm (Grad bei Winkeln); kleinere Abweichungen zwischen zwei Lesungen sind kein Befund

Vector = tuple[float, float, float]


def _finding(code: str, message: str, where: str, assumption: str, refs: Iterable[str] = (),
             issue: str = "ambiguous") -> dict:
    """Ein Befund samt Entwurf des Prüfpunkts, der ihn beantwortet; die Message ist der Grund."""
    review = {"issue": issue, "refs": list(dict.fromkeys(refs)), "assumption": assumption, "reason": message}
    return {"code": code, "message": message, "where": where, "suggested_review": review}


def _xyz(v: Vector) -> str:
    return "(" + ", ".join(f"{c:g}" for c in v) + ")"


def _unit(v: Vector) -> tuple[float, ...]:
    length = math.hypot(*v)
    return tuple(c / length for c in v)  # die Spezifikation lehnt eine Nullachse ab


def _line_distance(point: Vector, hole: ExpectedHole) -> float:
    """Abstand eines Punkts von der Achsengeraden einer Bohrung."""
    axis = _unit(hole.axis)
    rel = [p - q for p, q in zip(point, hole.at)]
    along = sum(r * a for r, a in zip(rel, axis))
    return math.hypot(*(r - along * a for r, a in zip(rel, axis)))


def _offset(h: ExpectedHole, g: ExpectedHole) -> float:
    """Abstand der Achsen zweier Bohrungen: der Abstand der Geraden, wenn sie parallel sind.

    `at` ist ein beliebiger Punkt der Achse, also dürfen zwei Lesungen verschiedene Punkte derselben Geraden nennen.
    """
    return max(_line_distance(h.at, g), _line_distance(g.at, h))


def _parallel(h: ExpectedHole, g: ExpectedHole) -> bool:
    return abs(sum(a * b for a, b in zip(_unit(h.axis), _unit(g.axis)))) >= AXIS_COS


def _along(hole: ExpectedHole) -> int | None:
    """Index der Koordinatenachse, der eine Bohrung folgt, None bei einer schrägen."""
    return next((k for k, c in enumerate(_unit(hole.axis)) if abs(c) >= AXIS_COS), None)


# ---------------------------------------------------------------- zwei Lesungen einer Seite

def _label(d: Dimension) -> str:
    return f"{d.kind} {d.value:g}" + (f" ({d.text!r})" if d.text else "")


def _text_key(d: Dimension) -> str:
    return "".join(d.text.split()).casefold()


def _same(p: float | None, q: float | None, tol: float) -> bool:
    return (p is None and q is None) or (p is not None and q is not None and abs(p - q) <= tol)


def _deviation(d: Dimension) -> str:
    return "none" if d.tol_upper is None or d.tol_lower is None else f"{d.tol_upper:+g}/{d.tol_lower:+g}"


def _pair_dimensions(a: list[Dimension], b: list[Dimension], tol: float) -> tuple[list, list, list]:
    """Paare von Maßen gleicher Art, deren Werte um höchstens tol abweichen, eins zu eins.

    Gibt die Paare und die Maße von a und von b zurück, die ohne Partner bleiben.
    """
    pairs, left_a, left_b = [], [], []
    for kind in sorted({d.kind for d in (*a, *b)}):
        xs = sorted((d for d in a if d.kind == kind), key=lambda d: d.value)
        ys = sorted((d for d in b if d.kind == kind), key=lambda d: d.value)
        i = j = 0
        while i < len(xs) and j < len(ys):
            if abs(xs[i].value - ys[j].value) <= tol:
                pairs.append((xs[i], ys[j]))
                i, j = i + 1, j + 1
            elif xs[i].value < ys[j].value:
                left_a.append(xs[i])
                i += 1
            else:
                left_b.append(ys[j])
                j += 1
        left_a += xs[i:]
        left_b += ys[j:]
    return pairs, left_a, left_b


def _compare_dimensions(a: PartSpec, b: PartSpec, tol: float) -> list[dict]:
    pairs, left_a, left_b = _pair_dimensions(a.dimensions, b.dimensions, tol)
    findings = []
    for x, y in pairs:
        if x.fit != y.fit:
            findings.append(_finding(
                "EXTRACTION_MISMATCH", f"{_label(x)}: fit {x.fit or 'none'} in A vs {y.fit or 'none'} in B",
                f"dimensions.{x.id}.fit", f"fit {x.fit or 'none'} for {_label(x)}", [x.id]))
        if not (_same(x.tol_upper, y.tol_upper, tol) and _same(x.tol_lower, y.tol_lower, tol)):
            findings.append(_finding(
                "EXTRACTION_MISMATCH", f"{_label(x)}: deviations {_deviation(x)} in A vs {_deviation(y)} in B",
                f"dimensions.{x.id}.tol", f"deviations {_deviation(x)} for {_label(x)}", [x.id]))
    # was keinen Partner nach Wert hat, kann trotzdem ein gedruckter Text sein, den die Lesungen anders trennen
    for x in list(left_a):
        y = next((y for y in left_b if x.text.strip() and _text_key(x) == _text_key(y)), None)
        if y is not None:
            left_a.remove(x)
            left_b.remove(y)
            findings.append(_finding(
                "EXTRACTION_MISMATCH", f"printed {x.text!r}: {x.kind} {x.value:g} in A vs {y.kind} {y.value:g} in B",
                f"dimensions.{x.id}", f"{x.kind} {x.value:g} for {x.text!r}", [x.id]))
    findings += [
        _finding("ONLY_IN_A", f"{_label(x)} only in A: the second reading has no such dimension",
                 f"dimensions.{x.id}", f"{_label(x)} is on the drawing", [x.id])
        for x in left_a]
    findings += [
        _finding("ONLY_IN_B", f"{_label(y)} only in B: the first reading has no such dimension",
                 f"second.dimensions.{y.id}", f"there is no {_label(y)}")  # die Kennung von B fehlt in der Spezifikation
        for y in left_b]
    return findings


def _compare_bbox(a: PartSpec, b: PartSpec, tol: float) -> list[dict]:
    return [
        _finding("EXTRACTION_MISMATCH", f"expected bbox {axis}: A={x:g} vs B={y:g}", f"expected.bbox.{axis}",
                 f"bbox {axis} = {x:g} mm")
        for axis, x, y in zip("xyz", a.expected.bbox, b.expected.bbox) if abs(x - y) > tol]


def _pair_holes(a: list[ExpectedHole], b: list[ExpectedHole]) -> list[tuple[int, int]]:
    """Bohrungen eins zu eins paaren, zuerst die Paare mit den nächsten Achsen, dann die mit den nächsten Durchmessern.

    Der Abstand wird gerundet, damit das Rauschen von 1e-15 zwischen gleichen Achsen den Durchmesser nicht überstimmen kann.
    """
    candidates = sorted((round(_offset(h, g), 6), abs(h.d - g.d), i, j)
                        for i, h in enumerate(a) for j, g in enumerate(b))
    pairs, used_a, used_b = [], set(), set()
    for _, _, i, j in candidates:
        if i not in used_a and j not in used_b:
            pairs.append((i, j))
            used_a.add(i)
            used_b.add(j)
    return sorted(pairs)


def _compare_holes(a: PartSpec, b: PartSpec, tol: float) -> list[dict]:
    holes_a, holes_b = a.expected.holes, b.expected.holes
    pairs = _pair_holes(holes_a, holes_b)
    findings = []
    for i, j in pairs:
        h, g = holes_a[i], holes_b[j]
        where = f"expected.holes[{i}]"
        if abs(h.d - g.d) > tol:
            findings.append(_finding(
                "EXTRACTION_MISMATCH", f"hole {i} diameter: A={h.d:g} vs B={g.d:g}", f"{where}.d",
                f"hole {i} diameter {h.d:g} mm", h.dim_ids))
        if not _parallel(h, g):  # die Lage einer schrägen Bohrung ist nicht aussagekräftig
            findings.append(_finding(
                "EXTRACTION_MISMATCH", f"hole {i} axis: A={_xyz(h.axis)} vs B={_xyz(g.axis)}", f"{where}.axis",
                f"hole {i} axis {_xyz(h.axis)}", h.dim_ids))
        elif (offset := _offset(h, g)) > tol:
            findings.append(_finding(
                "EXTRACTION_MISMATCH",
                f"hole {i} position: A={_xyz(h.at)} vs B={_xyz(g.at)}, the axes are {offset:g} mm apart",
                f"{where}.at", f"hole {i} at {_xyz(h.at)}", h.dim_ids))
    if len(holes_a) != len(holes_b):
        paired_a, paired_b = {i for i, _ in pairs}, {j for _, j in pairs}
        alone = [f"A[{i}] d={h.d:g} at {_xyz(h.at)}" for i, h in enumerate(holes_a) if i not in paired_a]
        alone += [f"B[{j}] d={g.d:g} at {_xyz(g.at)}" for j, g in enumerate(holes_b) if j not in paired_b]
        findings.append(_finding(
            "HOLE_COUNT", f"hole count: A={len(holes_a)} vs B={len(holes_b)}; without a partner: {', '.join(alone)}",
            "expected.holes", f"{len(holes_a)} holes"))
    return findings


def compare_specs(a: PartSpec, b: PartSpec, tol: float = TOL) -> list[dict]:
    """Die Unterschiede zweier unabhängiger Lesungen einer Seite; a ist die Spezifikation, die übermittelt wird.

    Maße werden nach Art und Wert gepaart, das Überzählige zu gedruckten Texten. Bohrungen werden nach dem Abstand
    ihrer Achsen gepaart, also zählen Kennungen, Reihenfolge und der gewählte Punkt auf einer Achse nicht. Jeder
    Unterschied über tol (mm) ist ein Befund mit dem Code EXTRACTION_MISMATCH, ONLY_IN_A, ONLY_IN_B oder HOLE_COUNT.
    """
    return _compare_dimensions(a, b, tol) + _compare_bbox(a, b, tol) + _compare_holes(a, b, tol)


# ---------------------------------------------------------------- Regeln ohne Referenz

def _holes_outside_bbox(spec: PartSpec) -> list[dict]:
    """Bohrungen, die in keiner Lage in die erwartete bbox passen.

    expected.bbox enthält nur Größen, keine Lage, also müssen die über eine Achse gebohrten Bohrungen zusammen
    in die Größe entlang dieser Achse passen. Eine einzelne Bohrung fällt nur auf, wenn sie größer als das Teil ist.
    """
    findings = []
    for k, axis in enumerate("xyz"):
        across = [h for h in spec.expected.holes if _along(h) not in (None, k)]
        if not across:
            continue
        low = min(across, key=lambda h: h.at[k] - h.d / 2)
        high = max(across, key=lambda h: h.at[k] + h.d / 2)
        span = high.at[k] + high.d / 2 - (low.at[k] - low.d / 2)
        size = spec.expected.bbox[k]
        if span > size + TOL:
            findings.append(_finding(
                "HOLE_OUTSIDE_BBOX",
                f"the holes span {span:g} mm along {axis} (positions and diameters), more than the expected bbox "
                f"of {size:g} mm",
                f"expected.bbox.{axis}", "hole positions and diameters as read", [*low.dim_ids, *high.dim_ids],
                "conflict"))
    return findings


def _holes_overlap(spec: PartSpec) -> list[dict]:
    """Parallele Bohrungen, deren Querschnitte überlappen. Bohrungen auf einer Achse bilden ein Stufenloch, kreuzende ein Kreuzloch."""
    findings = []
    for (i, h), (j, g) in combinations(enumerate(spec.expected.holes), 2):
        reach = (h.d + g.d) / 2
        if _parallel(h, g) and TOL < (offset := _offset(h, g)) < reach - TOL:
            findings.append(_finding(
                "HOLES_OVERLAP",
                f"holes {i} and {j} overlap: their axes are {offset:g} mm apart, the radii add up to {reach:g} mm",
                "expected.holes", f"holes {i} and {j} as read", [*h.dim_ids, *g.dim_ids], "conflict"))
    return findings


def _fillets_too_big(spec: PartSpec) -> list[dict]:
    """Rundungsradien über der halben mittleren Kante der bbox.

    Die mittlere Kante, nicht die kleinste: die Stärke einer Platte sagt nichts über den Radius ihrer Ecken.
    """
    limit = sorted(spec.expected.bbox)[1] / 2
    dims = {d.id: d for d in spec.dimensions}
    findings = []
    for feature in spec.features:
        if feature.type != "fillet":
            continue
        for d in (dims[i] for i in feature.dim_ids):
            if d.kind == "radius" and d.value > limit + TOL:
                findings.append(_finding(
                    "FILLET_TOO_BIG", f"fillet radius {d.value:g} is above half the middle edge of the bbox ({limit:g})",
                    f"features.{feature.id}", f"radius {d.value:g} as read", [d.id], "conflict"))
    return findings


def plausibility(spec: PartSpec) -> list[dict]:
    """Befunde der Regeln, die keine Referenz brauchen: Bohrungen, die nicht passen, überlappende Bohrungen, zu große Rundungen."""
    return _holes_outside_bbox(spec) + _holes_overlap(spec) + _fillets_too_big(spec)

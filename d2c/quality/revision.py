"""Änderungsliste zwischen zwei Revisionen derselben Spezifikation: reine Funktion über zwei PartSpec.

Ein Eintrag ist {"area", "change", "feature", "before", "after"}. `area` ist dimension, tolerance, general_tolerance, geometric,
hole oder bbox, `change` ist added, removed oder changed; feature nennt Art und ID (leer bei hole, bbox und general_tolerance),
before und after sind Anzeigetexte ("-" heißt fehlt).

Zuordnungsregel: Die IDs wechseln zwischen Revisionen (ein neues Lesen nummeriert neu), deshalb gilt die ID nur als letzte Spur. Maße (und ebenso Form- und
Lagetoleranzen) werden in dieser Reihenfolge einander eins zu eins zugeordnet, jede Stufe nur unter den noch freien:
1. gleiche Art und gleicher gedruckter Text (`text`, leer zählt nicht; Leerzeichen, "=", "mm" und Dezimalkomma zählen nicht),
2. gleiche Art und gleicher Wert (nur Maße),
3. gleiche Art und gleiche ID.
Bohrungen: 1. gleicher Durchmesser, gleiche Lage und Achse; 2. gleiche Lage und Achse (Durchmesser geändert);
3. gleicher Durchmesser und gleiche Achse (verschoben). Lage ist die Bohrachse: Punkte auf derselben Achse sind dieselbe Lage.
Was keinen Partner findet, ist neu (nur in der neuen Revision) oder entfallen (nur in der alten).
"""

from collections.abc import Callable, Sequence

from d2c.spec import Dimension, ExpectedHole, GeometricTolerance, PartSpec

DECIMALS = 6  # Stellen, auf die Werte für den Vergleich gerundet werden (1e-6 mm)


def _g(x: float) -> str:
    return f"{x:g}"


def _r(values: Sequence[float]) -> tuple[float, ...]:
    return tuple(round(v, DECIMALS) for v in values)


def _text(d: Dimension) -> str:
    """Der gedruckte Text ohne Leerzeichen, "=" und "mm", mit Dezimalpunkt: "4x M 5" und "4x M5", "6,6" und "6.6",
    "R = 20 mm" und "R20" sind derselbe Text."""
    return "".join(d.text.split()).replace("=", "").removesuffix("mm").replace(",", ".")


def _line(h: ExpectedHole) -> tuple[float, ...]:
    """Die Bohrachse als Lotfußpunkt vom Ursprung: unabhängig davon, welcher Punkt der Achse in `at` steht."""
    length = sum(a * a for a in h.axis) ** 0.5
    unit = [a / length for a in h.axis]
    along = sum(p * u for p, u in zip(h.at, unit, strict=True))
    return _r([p - along * u for p, u in zip(h.at, unit, strict=True)]) + _r(unit)


def _pair(old: list, new: list, keys: Sequence[Callable]) -> tuple[list[tuple], list, list]:
    """Ordnet alte und neue Einträge stufenweise über `keys` zu; liefert (Paare, nur alt, nur neu). Ein Schlüssel None zählt nicht."""
    old, new, pairs = list(old), list(new), []
    for key in keys:
        free: dict[object, list] = {}
        for item in old:
            if key(item) is not None:
                free.setdefault(key(item), []).append(item)
        for item in list(new):
            bucket = free.get(key(item)) if key(item) is not None else None
            if bucket:
                partner = bucket.pop(0)
                old.remove(partner)
                new.remove(item)
                pairs.append((partner, item))
    return pairs, old, new


def _row(area: str, change: str, feature: str, before: str, after: str) -> dict:
    return {"area": area, "change": change, "feature": feature, "before": before, "after": after}


def _tolerance(d: Dimension) -> str:
    """Die Toleranz eines Maßes als Anzeigetext: Passung oder Abmaße wie +0.2/0, sonst "none"."""
    parts = [d.fit] if d.fit else []
    if d.tol_upper is not None or d.tol_lower is not None:
        parts.append("/".join("-" if x is None else ("0" if x == 0 else f"{x:+g}") for x in (d.tol_upper, d.tol_lower)))
    return ", ".join(parts) or "none"


def _name(item: Dimension | GeometricTolerance) -> str:
    return f"{item.kind} {item.id}"


def _shown(d: Dimension) -> str:
    """Der gedruckte Text; ein angenommenes Maß hat keinen und erscheint als (Wert)."""
    return d.text or f"({_g(d.value)})"


def _dimensions(old: PartSpec, new: PartSpec) -> list[dict]:
    keys = (
        lambda d: (d.kind, _text(d)) if d.text else None,
        lambda d: (d.kind, _r([d.value])),
        lambda d: (d.kind, d.id),
    )
    pairs, removed, added = _pair(old.dimensions, new.dimensions, keys)
    rows = []
    for a, b in pairs:
        same_tol = _tolerance(a) == _tolerance(b)
        if _r([a.value]) != _r([b.value]) or (_text(a) != _text(b) and same_tol):
            rows.append(_row("dimension", "changed", _name(b), _shown(a), _shown(b)))
        if not same_tol:
            rows.append(_row("tolerance", "changed", _name(b), _tolerance(a), _tolerance(b)))
    rows += [_row("dimension", "removed", _name(d), _shown(d), "-") for d in removed]
    rows += [_row("dimension", "added", _name(d), "-", _shown(d)) for d in added]
    return rows


def _frame(t: GeometricTolerance) -> str:
    return f"{_g(t.value)} mm" + (f", datums {', '.join(t.datums)}" if t.datums else "")


def _geometric(old: PartSpec, new: PartSpec) -> list[dict]:
    keys = (lambda t: (t.kind, t.text), lambda t: (t.kind, t.id))
    pairs, removed, added = _pair(old.geometric_tolerances, new.geometric_tolerances, keys)
    rows = [
        _row("geometric", "changed", _name(b), _frame(a), _frame(b))
        for a, b in pairs
        if _r([a.value]) != _r([b.value]) or a.datums != b.datums
    ]
    rows += [_row("geometric", "removed", _name(t), _frame(t), "-") for t in removed]
    rows += [_row("geometric", "added", _name(t), "-", _frame(t)) for t in added]
    return rows


def _hole(h: ExpectedHole) -> str:
    return f"Ø{_g(h.d)} at ({', '.join(map(_g, h.at))}), axis ({', '.join(map(_g, h.axis))})"


def _holes(old: PartSpec, new: PartSpec) -> list[dict]:
    keys = (
        lambda h: (_r([h.d]), _line(h)),
        lambda h: _line(h),
        lambda h: (_r([h.d]), _r(h.axis)),
    )
    pairs, removed, added = _pair(old.expected.holes, new.expected.holes, keys)
    rows = [_row("hole", "changed", "", _hole(a), _hole(b)) for a, b in pairs if (_r([a.d]), _line(a)) != (_r([b.d]), _line(b))]
    rows += [_row("hole", "removed", "", _hole(h), "-") for h in removed]
    rows += [_row("hole", "added", "", "-", _hole(h)) for h in added]
    return rows


def _size(spec: PartSpec) -> str:
    return " × ".join(map(_g, spec.expected.bbox))


def diff(old: PartSpec, new: PartSpec) -> list[dict]:
    """Die Unterschiede der neuen gegenüber der alten Spezifikation; leer, wenn sich nichts Erfasstes geändert hat."""
    rows = _dimensions(old, new) + _geometric(old, new) + _holes(old, new)
    if _r(old.expected.bbox) != _r(new.expected.bbox):
        rows.append(_row("bbox", "changed", "", _size(old), _size(new)))
    if old.general_tolerance != new.general_tolerance:
        rows.append(_row("general_tolerance", "changed", "", old.general_tolerance or "-", new.general_tolerance or "-"))
    return rows

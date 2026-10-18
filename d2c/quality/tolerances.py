"""Toleranzen: Grenzabweichungen, Toleranzmitte und Einhaltung.

Die Abweichungen eines Maßes stammen in dieser Reihenfolge aus den eingetragenen Abmaßen (explicit), seiner
Passungsklasse nach ISO 286 oder der Allgemeintoleranz nach ISO 2768 im Schriftfeld (general). Die Tabellen
liegen als XML-Dateien im Ordner D2C_TABLES und werden bei jedem Aufruf eingelesen; fehlt eine der beiden Dateien
(`*isolim*.xml`, `*toltabs*.xml`), gelten dafür die Standardtabellen aus d2c.quality.iso_tables. Alle Abweichungen sind in mm
angegeben."""

import copy
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterator
from typing import Literal

from d2c.config import tables_dir
from d2c.oppath import get_path, set_path
from d2c.quality import iso_tables
from d2c.quality.iso_tables import GENERAL_ENTRIES
from d2c.spec import Dimension, PartSpec

Variant = Literal["nominal", "mid"]

EPS = 1e-6  # mm: Spielraum des Toleranzbands und kleinste Änderung eines Vorgangswerts, die als Änderung zählt
GENERAL_KINDS = ("length", "diameter", "radius")  # Allgemeintoleranzen nach ISO 2768 gelten nur für lineare Maße
GEOMETRY_TOL = 0.05  # mm: so weit darf ein gemessener Zylinderdurchmesser oder Abstand (Ebene, Achse) vom Maß abweichen
REFERENCE = re.compile(r"\(.*\)")  # Hilfsmaß in runden Klammern (ISO 129-1): nur zur Information, nicht toleriert


class ToleranceError(ValueError):
    """Eine Abweichung kann nicht ermittelt werden: Die Klasse ist unbekannt oder das Maß liegt außerhalb der Tabelle."""


def _table(kind: str) -> ET.Element:
    """Wurzelelement der ersten Datei `*<kind>*.xml` in D2C_TABLES (Standard /tables), sonst der Standardtabelle."""
    folder = tables_dir()
    files = sorted(folder.glob(f"*{kind}*.xml")) if folder.is_dir() else []
    if not files:
        return getattr(iso_tables, kind)()
    return ET.parse(files[0]).getroot()  # Der Parser liest die Zeichenkodierung (ISO-8859-1) aus dem XML-Kopf


def _covers(row: ET.Element, nominal: float) -> bool:
    """Gibt an, ob der Nennwert in den Bereich einer Tabellenzeile fällt.

    Untere Grenze: over=x bedeutet x < Nennwert, from=x bedeutet x <= Nennwert. Obere Grenze: to=x bedeutet
    Nennwert <= x, up_to=x bedeutet Nennwert < x, weil die nächste Zeile mit from=x beginnt.
    """
    attrs = row.attrib
    above = nominal > float(attrs["over"]) if "over" in attrs else nominal >= float(attrs["from"])
    below = nominal <= float(attrs["to"]) if "to" in attrs else nominal < float(attrs["up_to"])
    return above and below


def _deviations(rows: ET.Element | None, nominal: float, what: str) -> tuple[float, float]:
    """(lower, upper) der ersten Tabellenzeile, die den Nennwert abdeckt."""
    if rows is None:
        raise ToleranceError(f"{what} is not contained in the tolerance tables.")
    for row in rows:
        if _covers(row, nominal):
            # + 0.0: In den Tabellen steht die Nullabweichung der Klassen H und h als "-0.0"
            return float(row.get("lower")) + 0.0, float(row.get("upper")) + 0.0
    raise ToleranceError(f"{what}: there is no row for {nominal} mm in the tolerance tables.")


def _fit_deviations(fit: str, nominal: float) -> tuple[float, float]:
    return _deviations(_table("isolim").find(fit), nominal, f"fit {fit}")


def _general_deviations(general: str, nominal: float) -> tuple[float, float]:
    entry = GENERAL_ENTRIES[general]
    rows = _table("toltabs").find(f"toltable[@entry='{entry}']/LIN_TOLTAB")
    return _deviations(rows, nominal, general)


def limits(dim: Dimension, general: str | None) -> tuple[float, float, str]:
    """(lower, upper, source): Abweichungen eines Maßes in mm und ihre Herkunft.

    source ist "explicit" (am Maß eingetragen), "fit" (Passungsklasse nach ISO 286), "general" (Allgemeintoleranz
    nach ISO 2768 im Schriftfeld, nur für Länge, Durchmesser und Radius) oder "none" (0.0, 0.0); geprüft wird
    in dieser Reihenfolge. Ein Hilfsmaß, dessen Text in runden Klammern steht, etwa "(65)", hat ohne eingetragene
    Abmaße "none": Die Allgemeintoleranz gilt dafür nicht. ToleranceError wird ausgelöst, wenn in einer benötigten Tabelle die Klasse oder das Maß fehlt.
    """
    if dim.tol_upper is not None and dim.tol_lower is not None:
        return dim.tol_lower, dim.tol_upper, "explicit"
    if REFERENCE.fullmatch(dim.text.strip()):
        return 0.0, 0.0, "none"
    if dim.fit is not None:
        return *_fit_deviations(dim.fit, dim.value), "fit"
    if general is not None and dim.kind in GENERAL_KINDS:
        return *_general_deviations(general, dim.value), "general"
    return 0.0, 0.0, "none"


def _mid(nominal: float, lower: float, upper: float) -> float:
    return round(nominal + (upper + lower) / 2, 6)  # Gerundet: 12.7 + (0 - 0.1) / 2 ergäbe 12.649999999999999


def mid_value(dim: Dimension, general: str | None) -> float:
    """Toleranzmitte: nominal + (upper + lower) / 2, auf 6 Dezimalstellen gerundet."""
    lower, upper, _ = limits(dim, general)
    return _mid(dim.value, lower, upper)


def _bindings(ops: list[dict], spec: PartSpec) -> Iterator[tuple[int, str, Dimension]]:
    """Jeder refs-Eintrag der ops als (Vorgangsindex, Parameterpfad, Maß)."""
    dimensions = {d.id: d for d in spec.dimensions}
    for index, op in enumerate(ops):
        for path, dim_id in op.get("refs", {}).items():
            yield index, path, dimensions[dim_id]


SCALES = (1.0, 0.5, 2.0)  # Wert = Maß, Halbmaß (Radius, Versatz zur Mitte) oder Doppeltes (Durchmesser statt Radius)
NEAR_MM, NEAR_RATIO = 0.5, 0.05  # "nahe am Nennmaß": höchstens max(0,5 mm; 5 % des Nennmaßes) entfernt


def _factor(value: float, dim: Dimension, lower: float, upper: float) -> float | None:
    """Faktor k mit Wert = k · Maß (negativ bei umgekehrtem Vorzeichen), oder None, wenn der Wert nicht zum Maß gehört.

    Reihenfolge: (1) bis (3) der Betrag liegt im Toleranzband des Maßes, danach seines Halb- und Doppelmaßes;
    (4) bis (6) er liegt nahe am Nennmaß (unskaliert, dann skaliert) und damit ausserhalb des Bands: ein Verstoß, der
    sichtbar bleiben muss. Sonst ist der Wert kein Maßwert (None).
    """
    size, sign = abs(value), -1.0 if value < 0 else 1.0
    for scale in SCALES:
        if (dim.value + lower) * scale - EPS <= size <= (dim.value + upper) * scale + EPS:
            return sign * scale
    near = max(NEAR_MM, NEAR_RATIO * dim.value)
    for scale in SCALES:
        if abs(size / scale - dim.value) <= near:
            return sign * scale
    return None


def apply_mode(ops: list[dict], spec: PartSpec, mode: Variant) -> tuple[list[dict], list[dict], list[dict]]:
    """(ops, changes, skipped): Eine Kopie der ops. Bei "mid" erhält jeder über refs an ein toleriertes Maß gebundene Parameter dessen Mitte.

    Ein Parameter, der das Maß, sein Halb- oder Doppelmaß ist (Zuordnung wie in _factor, auch knapp ausserhalb des
    Bands), erhält k · Mitte mit demselben Faktor k und Vorzeichen. Passt der Wert zu keinem Faktor oder fehlt die
    Toleranztabelle für das Maß, bleibt er unverändert und steht in `skipped` als
    {op_index, path, dim_id, nominal, value, reason}.
    `changes` enthält {op_index, path, dim_id, nominal, mid, factor, value}. Ist ein Wert bereits k · Mitte
    (zum Beispiel bei symmetrischer Toleranz), gilt er als unverändert.
    """
    result = copy.deepcopy(ops)
    changes, skipped = [], []
    if mode == "mid":
        for index, path, dim in _bindings(ops, spec):
            current = get_path(ops[index], path)
            skip = {"op_index": index, "path": path, "dim_id": dim.id, "nominal": dim.value, "value": current}
            try:
                lower, upper, source = limits(dim, spec.general_tolerance)
            except ToleranceError as e:
                skipped.append(skip | {"reason": str(e)})
                continue
            if source == "none":
                continue
            k = _factor(current, dim, lower, upper)
            if k is None:
                skipped.append(skip | {"reason": f"value {current:g} is neither the dimension {dim.value:g} nor its half or double"})
                continue
            mid = _mid(dim.value, lower, upper)
            value = round(k * mid, 6)
            if abs(value - current) > EPS:
                set_path(result[index], path, value)
                changes.append(
                    {"op_index": index, "path": path, "dim_id": dim.id, "nominal": dim.value, "mid": mid, "factor": k, "value": value}
                )
    return result, changes, skipped


def unbound(spec: PartSpec, ops: list[dict], skipped: list[dict]) -> list[str]:
    """IDs der tolerierten Maße, deren Toleranzmitte vom Nennmaß abweicht und die keine brauchbare refs-Bindung haben (fehlend oder übersprungen)."""
    usable = {dim.id for _, _, dim in _bindings(ops, spec)} - {s["dim_id"] for s in skipped}
    found = []
    for dim in spec.dimensions:
        try:
            lower, upper, source = limits(dim, spec.general_tolerance)
        except ToleranceError:
            continue
        if source != "none" and dim.id not in usable and abs(_mid(dim.value, lower, upper) - dim.value) > EPS:
            found.append(dim.id)
    return found


def _measured_diameters(report: dict, dim: Dimension) -> list[float]:
    """Durchmesser der Zylinder, die der Bericht für Bohrungen dieses Durchmessermaßes ermittelt hat.

    Eine Bohrung gehört dazu, wenn sie das Maß in dim_ids nennt oder ihr erwarteter Durchmesser dem Nennmaß entspricht.
    """
    if dim.kind != "diameter":  # Eine Bohrung kann auch die Maße ihrer Lage benennen
        return []
    holes = (
        c
        for c in report.get("dims") or []
        if c["check"] == "hole" and (dim.id in c["dim_ids"] or abs(c["expected"]["d"] - dim.value) <= EPS)
    )
    return [c["actual"]["d"] for c in holes if c["actual"]]


def _from_geometry(dim: Dimension, lower: float, upper: float, geometry: dict | None) -> float | None:
    """Der gemessene Zylinderdurchmesser oder Abstand des Körpers, der zum Maß passt (±GEOMETRY_TOL um Nennmaß und Toleranzband), sonst None.

    Längenmaße vergleichen die Abstände paralleler Ebenen sowie die Lagemaße der Zylinderachsen (Achse zu Ebene, Achse zu Achse).
    """
    pool = {
        "diameter": (geometry or {}).get("diameters", []),
        "length": (geometry or {}).get("distances", []),
        "radius": [d / 2 for d in (geometry or {}).get("diameters", [])],
    }.get(dim.kind, [])
    low, high = min(dim.value, dim.value + lower), max(dim.value, dim.value + upper)
    hits = [m for m in pool if low - GEOMETRY_TOL <= m <= high + GEOMETRY_TOL]
    return min(hits, key=lambda m: abs(m - dim.value), default=None)


def compliance(spec: PartSpec, ops: list[dict], report: dict, mode: Variant, geometry: dict | None = None) -> list[dict]:
    """Gibt für jedes tolerierte Maß einen Eintrag dazu zurück, ob das Modell die Toleranz einhält.

    Die ops werden so gelesen, wie apply_mode(ops, spec, mode) sie aufbaut; report ist der Worker-Bericht dieses Aufbaus.
    values sind die über refs an das Maß gebundenen Vorgangswerte (durch ihren Faktor auf das Maß umgerechnet) und,
    bei einem Durchmesser, die an den Bohrungen gemessenen Durchmesser, die es nennen. Hat ein Maß keine solchen Werte,
    wird es mit `geometry` ({"diameters": [...], "distances": [...]} vom fertigen Körper, siehe
    export.measure_geometry) verglichen: ein Fund ergibt values=[gemessener Wert] und geometry=True. Zu den distances
    gehören Ebenenabstände und Lagemaße (Bohrungsachse zu Kante, Achse zu Achse); nur so haben Lagemaße ein Urteil.
    Auch mit mode="mid" hängt das von `geometry` ab: Ohne sie hat ein Maß ohne gebundenen Wert kein Urteil.
    in_tolerance: Jeder Wert liegt zwischen nominal + lower und nominal + upper.
    Ein gebundener Wert gilt als das Maß, seine Hälfte oder sein Doppeltes (siehe _factor; nahe am Nennmaß, aber
    außerhalb des Bands zählt er und fällt durch). Jeder andere Wert ist kein Maß: Er entfällt und steht in "note".
    Ein Maß ohne Wert hat in_tolerance None, also nicht geprüft.
    """
    built, _, _ = apply_mode(ops, spec, mode)
    bound, skipped = defaultdict(list), defaultdict(list)
    for index, path, dim in _bindings(built, spec):
        value = get_path(built[index], path)
        try:
            lower, upper, _ = limits(dim, spec.general_tolerance)
        except ToleranceError:
            continue  # die Tabelle kennt das Maß nicht: Es wird unten ausgelassen
        k = _factor(value, dim, lower, upper)
        if k is None:
            skipped[dim.id].append(f"op {index} {path} = {value:g}")
        else:
            bound[dim.id].append(round(value / k, 6))
    entries = []
    for dim in spec.dimensions:
        try:
            lower, upper, source = limits(dim, spec.general_tolerance)
        except ToleranceError:
            continue  # die Tabelle kennt das Maß nicht: Der Aufrufer meldet es, geprüft werden kann es nicht
        if source == "none":
            continue
        values = bound[dim.id] + _measured_diameters(report, dim)
        by_geometry = False
        if not values and (found := _from_geometry(dim, lower, upper, geometry)) is not None:
            values, by_geometry = [found], True
        within = all(dim.value + lower - EPS <= v <= dim.value + upper + EPS for v in values)
        entry = {
            "dim_id": dim.id,
            "text": dim.text,
            "nominal": dim.value,
            "lower": lower,
            "upper": upper,
            "source": source,
            "values": values,
            "in_tolerance": within if values else None,
            "geometry": by_geometry,
        }
        if skipped[dim.id]:
            entry["note"] = (
                f"{dim.id} cannot be checked at: {'; '.join(skipped[dim.id])} (neither the dimension nor half or double of it, nor close to the nominal)"
            )
        entries.append(entry)
    return entries

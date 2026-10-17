"""Toleranzen (Erweiterung X1): Grenzabweichungen, Toleranzmitte und Einhaltung.

Die Abweichungen eines Maßes stammen in dieser Reihenfolge aus den eingetragenen Abmaßen (explicit), seiner
Passungsklasse nach ISO 286 oder der Allgemeintoleranz nach ISO 2768 im Schriftfeld (general). Die Tabellen
liegen als BCT-XML-Dateien im Ordner D2C_TABLES und werden bei jedem Aufruf eingelesen. Alle Abweichungen sind in mm angegeben."""

import copy
import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from d2c.ops import get_path, set_path
from d2c.spec import Dimension, PartSpec

Variant = Literal["nominal", "mid"]

EPS = 1e-6  # mm: Spielraum des Toleranzbands und kleinste Änderung eines Vorgangswerts, die als Änderung zählt
GENERAL_KINDS = ("length", "diameter", "radius")  # Allgemeintoleranzen nach ISO 2768 gelten nur für lineare Maße
# Die LIN_TOLTAB-Einträge in bct_toltabs.xml entsprechen genau den BORE_TOLTAB-Einträgen und gelten daher auch für Durchmesser.
GENERAL_ENTRIES = {
    "ISO 2768-f": "DIN ISO 2768 (fine/fein)",
    "ISO 2768-m": "DIN ISO 2768 (medium/mittel)",
    "ISO 2768-c": "DIN ISO 2768 (coarse/grob)",
    "ISO 2768-v": "DIN ISO 2768 (very coarse/sehr grob)",
}
NO_TABLES = "Toleranztabellen nicht gefunden (D2C_TABLES)"  # what a caller says when tables_found() is False


class ToleranceError(ValueError):
    """Eine Abweichung kann nicht ermittelt werden: Es fehlt eine Tabellendatei, die Klasse ist unbekannt oder das Maß liegt außerhalb der Tabelle."""


def _table(pattern: str) -> ET.Element:
    """Wurzelelement der ersten Datei in D2C_TABLES (Standard /tables), die zum Muster passt."""
    folder = Path(os.environ.get("D2C_TABLES", "/tables"))
    files = sorted(folder.glob(pattern))
    if not files:
        raise ToleranceError(f"Keine Datei für {pattern} in {folder} gefunden: Setze D2C_TABLES auf den Ordner mit den BCT-Tabellen.")
    return ET.parse(files[0]).getroot()  # Der Parser liest die Zeichenkodierung (ISO-8859-1) aus dem XML-Kopf


def tables_found() -> bool:
    """Whether D2C_TABLES holds both table files, the ISO 286 and the ISO 2768 one."""
    try:
        _table("*isolim*.xml"), _table("*toltabs*.xml")
    except ToleranceError:
        return False
    return True


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
        raise ToleranceError(f"{what} ist nicht in den Toleranztabellen enthalten.")
    for row in rows:
        if _covers(row, nominal):
            # + 0.0: In den Tabellen steht die Nullabweichung der Klassen H und h als "-0.0"
            return float(row.get("lower")) + 0.0, float(row.get("upper")) + 0.0
    raise ToleranceError(f"{what}: Für {nominal} mm gibt es keine Zeile in den Toleranztabellen.")


def _fit_deviations(fit: str, nominal: float) -> tuple[float, float]:
    return _deviations(_table("*isolim*.xml").find(fit), nominal, f"fit {fit}")


def _general_deviations(general: str, nominal: float) -> tuple[float, float]:
    entry = GENERAL_ENTRIES[general]
    rows = _table("*toltabs*.xml").find(f"toltable[@entry='{entry}']/LIN_TOLTAB")
    return _deviations(rows, nominal, general)


def limits(dim: Dimension, general: str | None) -> tuple[float, float, str]:
    """(lower, upper, source): Abweichungen eines Maßes in mm und ihre Herkunft.

        source ist "explicit" (am Maß eingetragen), "fit" (Passungsklasse nach ISO 286), "general" (Allgemeintoleranz
        nach ISO 2768 im Schriftfeld, nur für Länge, Durchmesser und Radius) oder "none" (0.0, 0.0); geprüft wird
        in dieser Reihenfolge. ToleranceError wird ausgelöst, wenn in einer benötigten Tabelle die Klasse oder das Maß fehlt.
    """
    if dim.tol_upper is not None and dim.tol_lower is not None:
        return dim.tol_lower, dim.tol_upper, "explicit"
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


def apply_mode(ops: list[dict], spec: PartSpec, mode: Variant) -> tuple[list[dict], list[dict]]:
    """Eine Kopie der ops. Bei "mid" erhält jeder über refs an ein toleriertes Maß gebundene Parameter dessen Mitte.

        Zusätzlich werden die Änderungen als {op_index, path, dim_id, nominal, mid} zurückgegeben. Ist ein Wert bereits
        die Mitte (zum Beispiel bei symmetrischer Toleranz), gilt er als unverändert.
    """
    result = copy.deepcopy(ops)
    changes = []
    if mode == "mid":
        for index, path, dim in _bindings(ops, spec):
            lower, upper, source = limits(dim, spec.general_tolerance)
            if source == "none":
                continue
            mid = _mid(dim.value, lower, upper)
            if abs(mid - get_path(ops[index], path)) > EPS:
                set_path(result[index], path, mid)
                changes.append({"op_index": index, "path": path, "dim_id": dim.id, "nominal": dim.value, "mid": mid})
    return result, changes


def _measured_diameters(report: dict, dim: Dimension) -> list[float]:
    """Durchmesser der Zylinder, die der Bericht für Bohrungen mit zugeordnetem Durchmessermaß ermittelt hat."""
    if dim.kind != "diameter":  # Eine Bohrung kann auch die Maße ihrer Lage benennen
        return []
    holes = (c for c in report.get("dims") or [] if c["check"] == "hole" and dim.id in c["dim_ids"])
    return [c["actual"]["d"] for c in holes if c["actual"]]


def compliance(spec: PartSpec, ops: list[dict], report: dict, mode: Variant) -> list[dict]:
    """Gibt für jedes tolerierte Maß einen Eintrag dazu zurück, ob das Modell die Toleranz einhält.

    The ops are read as apply_mode(ops, spec, mode) builds them; report is the worker report of that build.
    values are the op values bound to the dimension by refs and, for a diameter, the diameters measured at
    the holes that name it. in_tolerance: every value lies between nominal + lower and nominal + upper.
    A dimension that nothing is bound to has no values: in_tolerance is None there, not checked.
    """
    built, _ = apply_mode(ops, spec, mode)
    bound = defaultdict(list)
    for index, path, dim in _bindings(built, spec):
        bound[dim.id].append(get_path(built[index], path))
    entries = []
    for dim in spec.dimensions:
        lower, upper, source = limits(dim, spec.general_tolerance)
        if source == "none":
            continue
        values = bound[dim.id] + _measured_diameters(report, dim)
        within = all(dim.value + lower - EPS <= v <= dim.value + upper + EPS for v in values)
        entries.append({
            "dim_id": dim.id, "text": dim.text, "nominal": dim.value, "lower": lower, "upper": upper,
            "source": source, "values": values, "in_tolerance": within if values else None,
        })
    return entries

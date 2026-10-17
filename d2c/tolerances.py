"""Tolerances (extension X1): limit deviations, tolerance mid and compliance.

The deviations of a dimension come from, in this order: the ones printed with it (explicit), its ISO 286
fit class, the ISO 2768 class of the title block (general). The tables are the BCT XML files in the folder
D2C_TABLES, read on every call. All deviations are in mm.
"""

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

EPS = 1e-6  # mm: slack of a tolerance band, and the smallest shift of an op value that counts as a change
GENERAL_KINDS = ("length", "diameter", "radius")  # ISO 2768 general tolerances cover linear sizes only
# toltable entries of bct_toltabs.xml. Their LIN_TOLTAB is value for value the BORE_TOLTAB, so it serves diameters too.
GENERAL_ENTRIES = {
    "ISO 2768-f": "DIN ISO 2768 (fine/fein)",
    "ISO 2768-m": "DIN ISO 2768 (medium/mittel)",
    "ISO 2768-c": "DIN ISO 2768 (coarse/grob)",
    "ISO 2768-v": "DIN ISO 2768 (very coarse/sehr grob)",
}


class ToleranceError(ValueError):
    """A deviation cannot be looked up: no table file, unknown class, or a size outside the table."""


def _table(pattern: str) -> ET.Element:
    """Root of the first file in D2C_TABLES (default /tables) that matches pattern."""
    folder = Path(os.environ.get("D2C_TABLES", "/tables"))
    files = sorted(folder.glob(pattern))
    if not files:
        raise ToleranceError(f"no {pattern} in {folder}: set D2C_TABLES to the folder with the BCT tables")
    return ET.parse(files[0]).getroot()  # the parser reads the encoding (ISO-8859-1) from the XML header


def _covers(row: ET.Element, nominal: float) -> bool:
    """Whether the nominal size lies in the range of a table row.

    Lower bound: over=x means x < nominal, from=x means x <= nominal. Upper bound: to=x means nominal <= x,
    up_to=x means nominal < x, because the next row starts with from=x.
    """
    attrs = row.attrib
    above = nominal > float(attrs["over"]) if "over" in attrs else nominal >= float(attrs["from"])
    below = nominal <= float(attrs["to"]) if "to" in attrs else nominal < float(attrs["up_to"])
    return above and below


def _deviations(rows: ET.Element | None, nominal: float, what: str) -> tuple[float, float]:
    """(lower, upper) of the first row of rows that covers the nominal size."""
    if rows is None:
        raise ToleranceError(f"{what} is not in the tolerance tables")
    for row in rows:
        if _covers(row, nominal):
            # + 0.0: the tables write the zero deviation of the H and h classes as "-0.0"
            return float(row.get("lower")) + 0.0, float(row.get("upper")) + 0.0
    raise ToleranceError(f"{what} has no row for {nominal} mm in the tolerance tables")


def _fit_deviations(fit: str, nominal: float) -> tuple[float, float]:
    return _deviations(_table("*isolim*.xml").find(fit), nominal, f"fit {fit}")


def _general_deviations(general: str, nominal: float) -> tuple[float, float]:
    entry = GENERAL_ENTRIES[general]
    rows = _table("*toltabs*.xml").find(f"toltable[@entry='{entry}']/LIN_TOLTAB")
    return _deviations(rows, nominal, general)


def limits(dim: Dimension, general: str | None) -> tuple[float, float, str]:
    """(lower, upper, source): the deviations of a dimension in mm and where they come from.

    source is "explicit" (printed with the dimension), "fit" (its ISO 286 class), "general" (the ISO 2768
    class `general` of the title block, for length, diameter and radius only) or "none" (0.0, 0.0), checked
    in this order. Raises ToleranceError if a needed table lacks the class or the size.
    """
    if dim.tol_upper is not None and dim.tol_lower is not None:
        return dim.tol_lower, dim.tol_upper, "explicit"
    if dim.fit is not None:
        return *_fit_deviations(dim.fit, dim.value), "fit"
    if general is not None and dim.kind in GENERAL_KINDS:
        return *_general_deviations(general, dim.value), "general"
    return 0.0, 0.0, "none"


def _mid(nominal: float, lower: float, upper: float) -> float:
    return round(nominal + (upper + lower) / 2, 6)  # rounded: 12.7 + (0 - 0.1) / 2 would be 12.649999999999999


def mid_value(dim: Dimension, general: str | None) -> float:
    """The tolerance mid, nominal + (upper + lower) / 2, rounded to 6 decimals."""
    lower, upper, _ = limits(dim, general)
    return _mid(dim.value, lower, upper)


def _bindings(ops: list[dict], spec: PartSpec) -> Iterator[tuple[int, str, Dimension]]:
    """Every refs entry of the ops as (op index, parameter path, dimension)."""
    dimensions = {d.id: d for d in spec.dimensions}
    for index, op in enumerate(ops):
        for path, dim_id in op.get("refs", {}).items():
            yield index, path, dimensions[dim_id]


def apply_mode(ops: list[dict], spec: PartSpec, mode: Variant) -> tuple[list[dict], list[dict]]:
    """A copy of the ops. For "mid", every parameter bound by refs to a tolerated dimension takes its mid.

    Also returns the changes as {op_index, path, dim_id, nominal, mid}. A value that already is the mid,
    as under a symmetric tolerance, is no change.
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
    """Diameters of the cylinders that the report found for the holes naming a diameter dimension."""
    if dim.kind != "diameter":  # a hole may name the dimensions that locate it, too
        return []
    holes = (c for c in report.get("dims") or [] if c["check"] == "hole" and dim.id in c["dim_ids"])
    return [c["actual"]["d"] for c in holes if c["actual"]]


def compliance(spec: PartSpec, ops: list[dict], report: dict, mode: Variant) -> list[dict]:
    """Whether the model keeps the tolerance of every tolerated dimension, one entry per dimension.

    The ops are read as apply_mode(ops, spec, mode) builds them; report is the worker report of that build.
    values are the op values bound to the dimension by refs and, for a diameter, the diameters measured at
    the holes that name it. in_tolerance: every value lies between nominal + lower and nominal + upper.
    A dimension that nothing is bound to has no values and so counts as in tolerance.
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
        entries.append({
            "dim_id": dim.id, "text": dim.text, "nominal": dim.value, "lower": lower, "upper": upper,
            "source": source, "values": values,
            "in_tolerance": all(dim.value + lower - EPS <= v <= dim.value + upper + EPS for v in values),
        })
    return entries

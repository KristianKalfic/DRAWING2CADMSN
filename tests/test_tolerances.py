"""Toleranzen (X1): Passungen nach ISO 286, Allgemeintoleranzen nach ISO 2768, Toleranzmitte und Einhaltung.

Unter `tests/fixtures/tables` liegen kleine Tabellen im BCT-Format mit öffentlichen ISO-Werten."""

from pathlib import Path

import pytest
from pydantic import TypeAdapter

from d2c.ops import Op
from d2c.spec import Dimension, PartSpec
from d2c.tolerances import ToleranceError, apply_mode, compliance, limits, mid_value, tables_found

FIXTURE_TABLES = Path(__file__).parent / "fixtures" / "tables"
OPS = TypeAdapter(list[Op])
# ISO 2768-1: lineare Maße über 6 bis 30 mm
GENERAL_25MM = [("ISO 2768-f", 0.1), ("ISO 2768-m", 0.2), ("ISO 2768-c", 0.5), ("ISO 2768-v", 1.0)]


@pytest.fixture(autouse=True)
def _fixture_tables(monkeypatch):
    monkeypatch.setenv("D2C_TABLES", str(FIXTURE_TABLES))


def dim(value, kind="length", id="d", **fields) -> Dimension:
    """Ein aus einer Zeichnung ausgelesenes Maß; wie in der Spezifikation verlangt, enthält der Text den Maßwert."""
    return Dimension(id=id, kind=kind, value=value, text=f"{value:g}", source=None, confidence=0.95, **fields)


def make_spec(*dimensions, general=None) -> PartSpec:
    return PartSpec(
        name="part", drawing="part/part.pdf", title="part", scale="1:1", general_tolerance=general,
        views=[{"id": "v1", "kind": "front", "scale": "1:1"}], dimensions=list(dimensions), features=[],
        expected={"bbox": (1, 1, 1)}, review=[])


def extrude(distance=10, refs=None) -> dict:
    return {"op": "extrude", "profile": {"type": "rect", "center": (0, 0), "w": 40, "h": 40},
            "plane": {"name": "XY"}, "distance": distance, "refs": refs or {}}


def bore(d, positions=((0, 0),), refs=None) -> dict:
    return {"op": "holes", "plane": {"name": "XY", "offset": 10}, "positions": list(positions), "d": d,
            "refs": refs or {}}


def plain_ops(*ops) -> list[dict]:
    """Operationen wie in der Sitzung gespeichert: über die `Op`-Union eingelesen und anschließend ausgegeben."""
    return [op.model_dump() for op in OPS.validate_python(list(ops))]


def hole_report(measured, dim_ids=("d16",)) -> dict:
    """Worker-Bericht mit Begrenzungsmaß und einer Bohrungsprüfung im Format von `checks.dimension_check`."""
    actual = None if measured is None else {"d": measured, "dist": 0.0}
    hole = {"check": "hole", "expected": {"d": 16.0, "at": (0.0, 0.0, 0.0), "axis": (0.0, 0.0, 1.0)},
            "actual": actual, "ok": actual is not None, "dim_ids": list(dim_ids)}
    bbox = {"check": "bbox_x", "expected": 40.0, "actual": 40.0, "ok": True, "dim_ids": []}
    return {"ok": True, "dims": [bbox, hole]}


def test_fit_h10_6():
    assert limits(dim(6, "diameter", fit="h10"), None) == (-0.048, 0.0, "fit")


def test_fit_H10_4():
    assert limits(dim(4, "diameter", fit="H10"), None) == (0.0, 0.048, "fit")


def test_zero_deviation_has_no_sign():
    """Die Tabellen speichern für die H-Klassen die Nullabweichung als „-0.0“; Berichte und Prüflisten zeigen 0,0."""
    lower, upper, _ = limits(dim(16, "diameter", fit="H7"), None)
    assert (repr(lower), repr(upper)) == ("0.0", "0.018")


def test_iso2768_m_25():
    assert limits(dim(25), "ISO 2768-m") == (-0.2, 0.2, "general")


@pytest.mark.parametrize("general, deviation", GENERAL_25MM)
def test_general_tolerance_classes(general, deviation):
    assert limits(dim(25), general) == (-deviation, deviation, "general")


@pytest.mark.parametrize("nominal", [3.001, 6.0])
def test_range_boundary_inclusive(nominal):
    """Der Bereich ist (über, bis]: h10 über 3 bis 6 schließt den oberen Grenzwert 6,0 ein."""
    assert limits(dim(nominal, "diameter", fit="h10"), None) == (-0.048, 0.0, "fit")


@pytest.mark.parametrize("nominal", [3.0, 6.001])
def test_range_boundary_exclusive(nominal):
    with pytest.raises(ToleranceError, match="keine Zeile"):
        limits(dim(nominal, "diameter", fit="h10"), None)


@pytest.mark.parametrize("nominal, deviation", [
    (0.0, 0.0), (0.4, 0.0),  # from=0,0 up_to=0,5: endet vor 0,5
    (0.5, 0.1), (3.0, 0.1),  # from=0,5 to=3,0: beginnt bei 0,5 und schließt 3,0 ein
    (3.5, 0.1), (6.0, 0.1), (6.5, 0.2),  # Zeilen mit „über“ und „bis“
])
def test_range_attribute_styles(nominal, deviation):
    assert limits(dim(nominal), "ISO 2768-m") == (-deviation, deviation, "general")


def test_explicit_beats_fit():
    printed = dim(6, "diameter", fit="h10", tol_upper=0.1, tol_lower=-0.1)
    assert limits(printed, None) == (-0.1, 0.1, "explicit")
    assert limits(printed, "ISO 2768-m") == (-0.1, 0.1, "explicit")


def test_fit_beats_general():
    assert limits(dim(6, "diameter", fit="h10"), "ISO 2768-m") == (-0.048, 0.0, "fit")


@pytest.mark.parametrize("kind", ["length", "diameter", "radius"])
def test_general_tolerance_for_linear_kinds(kind):
    assert limits(dim(25, kind), "ISO 2768-m")[2] == "general"


@pytest.mark.parametrize("kind", ["angle", "chamfer", "thread", "count"])
def test_no_general_tolerance_for_other_kinds(kind):
    assert limits(dim(25, kind), "ISO 2768-m") == (0.0, 0.0, "none")


def test_no_tolerance_at_all():
    assert limits(dim(25), None) == (0.0, 0.0, "none")


def test_unknown_fit_class():
    with pytest.raises(ToleranceError, match="fit X9"):
        limits(dim(6, "diameter", fit="X9"), None)


def test_missing_tables_name_the_folder(monkeypatch, tmp_path):
    """Der Ordner wird bei jedem Aufruf neu gelesen. Für ausdrücklich angegebene Abweichungen ist keine Tabelle nötig."""
    monkeypatch.setenv("D2C_TABLES", str(tmp_path))
    with pytest.raises(ToleranceError, match=str(tmp_path)):
        limits(dim(6, "diameter", fit="h10"), None)
    with pytest.raises(ToleranceError, match=str(tmp_path)):
        limits(dim(25), "ISO 2768-m")
    assert limits(dim(25, tol_upper=0.1, tol_lower=0.0), "ISO 2768-m") == (0.0, 0.1, "explicit")


def test_first_matching_file_is_read_in_its_declared_encoding(monkeypatch, tmp_path):
    table = ('<?xml version="1.0" encoding="ISO-8859-1"?>\n<!-- Passung für Wellen -->\n'
             '<ISOLIM><h10><value over="3" to="6" upper="0.0" lower="{}"/></h10></ISOLIM>\n')
    (tmp_path / "b_isolim.xml").write_bytes(table.format("-0.5").encode("iso-8859-1"))
    (tmp_path / "a_isolim.xml").write_bytes(table.format("-0.048").encode("iso-8859-1"))
    monkeypatch.setenv("D2C_TABLES", str(tmp_path))
    assert limits(dim(6, "diameter", fit="h10"), None) == (-0.048, 0.0, "fit")


def test_mid_asymmetric():
    """„31 -0,1“ bedeutet 31 mit den Abweichungen 0 und -0,1; die Toleranzmitte ist 30,95."""
    assert mid_value(dim(31, tol_upper=0.0, tol_lower=-0.1), None) == 30.95


def test_mid_value_cases():
    assert mid_value(dim(16, "diameter", fit="H7"), None) == 16.009
    assert mid_value(dim(25), "ISO 2768-m") == 25  # symmetrisch: das Nennmaß selbst
    assert mid_value(dim(25), None) == 25  # keine Toleranz
    # 12,7 + (0 − 0,1) / 2 ergibt als Gleitkommazahl 12,649999999999999
    assert mid_value(dim(12.7, tol_upper=0.0, tol_lower=-0.1), None) == 12.65


def test_apply_mode_substitutes_refs():
    spec = make_spec(
        dim(16, "diameter", id="d16", fit="H7"), dim(30, id="p30", tol_upper=0.2, tol_lower=0.0),
        dim(10, id="t10"), general="ISO 2768-m")
    ops = plain_ops(
        extrude(10, {"distance": "t10"}), extrude(), extrude(),
        bore(16, ((0, 30),), {"d": "d16", "positions.0.1": "p30"}))

    mid_ops, changes = apply_mode(ops, spec, "mid")

    assert mid_ops[3]["d"] == 16.009
    assert mid_ops[3]["positions"] == [(0, 30.1)]  # ein Pfad durch eine Liste und ein Tupel
    assert mid_ops[:3] == ops[:3]  # t10 ist symmetrisch (ISO 2768-m): keine Verschiebung nötig
    assert changes == [
        {"op_index": 3, "path": "d", "dim_id": "d16", "nominal": 16, "mid": 16.009},
        {"op_index": 3, "path": "positions.0.1", "dim_id": "p30", "nominal": 30, "mid": 30.1},
    ]
    assert (ops[3]["d"], ops[3]["positions"]) == (16, [(0, 30)])  # die Eingabe bleibt unverändert


def test_apply_mode_leaves_untoleranced_dimensions_alone():
    """Ohne Toleranz gibt es keine Toleranzmitte: Ein aus Maßen abgeleiteter Wert (15 = 25 - 10) bleibt unverändert."""
    spec = make_spec(dim(25, id="h25"))
    ops = plain_ops(extrude(15, {"distance": "h25"}))
    assert apply_mode(ops, spec, "mid") == (ops, [])


def test_apply_mode_nominal_is_a_plain_copy():
    spec = make_spec(dim(16, "diameter", id="d16", fit="H7"))
    ops = plain_ops(extrude(), bore(16, refs={"d": "d16"}))
    copied, changes = apply_mode(ops, spec, "nominal")
    assert (copied, changes) == (ops, [])
    assert copied is not ops and copied[1] is not ops[1]


def test_compliance_flags_nominal_outside():
    spec = make_spec(dim(31, id="l31", tol_upper=-0.1, tol_lower=-0.3))
    ops = plain_ops(extrude(31, {"distance": "l31"}))
    report = {"ok": True, "dims": []}

    assert compliance(spec, ops, report, "nominal") == [{
        "dim_id": "l31", "text": "31", "nominal": 31, "lower": -0.3, "upper": -0.1,
        "source": "explicit", "values": [31], "in_tolerance": False}]
    mid = compliance(spec, ops, report, "mid")[0]
    assert (mid["values"], mid["in_tolerance"]) == ([30.8], True)
    # Bereits auf die Toleranzmitte gesetzte Operationen liefern dasselbe Ergebnis.
    moved, _ = apply_mode(ops, spec, "mid")
    assert compliance(spec, moved, report, "mid") == compliance(spec, ops, report, "mid")


def test_compliance_adds_measured_hole_diameter():
    spec = make_spec(dim(16, "diameter", id="d16", fit="H7"))
    ops = plain_ops(extrude(), bore(16, refs={"d": "d16"}))

    nominal = compliance(spec, ops, hole_report(16.0), "nominal")[0]
    assert (nominal["values"], nominal["in_tolerance"]) == ([16, 16.0], True)  # Der untere Grenzwert zählt noch als eingehalten.
    mid = compliance(spec, ops, hole_report(16.009), "mid")[0]
    assert (mid["values"], mid["in_tolerance"]) == ([16.009, 16.009], True)
    too_big = compliance(spec, ops, hole_report(16.03), "mid")[0]
    assert (too_big["values"], too_big["in_tolerance"]) == ([16.009, 16.03], False)
    no_cylinder = compliance(spec, ops, hole_report(None), "mid")[0]
    assert no_cylinder["values"] == [16.009]


def test_compliance_band_has_a_slack_of_1e_6():
    spec = make_spec(dim(16, "diameter", id="d16", fit="H7"))
    inside = compliance(spec, [], hole_report(15.9999995), "nominal")[0]
    outside = compliance(spec, [], hole_report(15.999995), "nominal")[0]
    assert (inside["in_tolerance"], outside["in_tolerance"]) == (True, False)


def test_compliance_without_measurements():
    """Der Bericht eines fehlgeschlagenen Aufbaus enthält keine Maßeinträge."""
    spec = make_spec(dim(16, "diameter", id="d16", fit="H7"))
    ops = plain_ops(extrude(), bore(16, refs={"d": "d16"}))
    failed = {"ok": False, "error": "BuildError: op 1 (holes): ...", "dims": None}
    assert compliance(spec, ops, failed, "nominal")[0]["values"] == [16]


def test_measured_diameter_only_for_diameter_dimensions():
    """Für eine erwartete Bohrung können neben dem Durchmesser auch die Maße angegeben werden, die ihre Lage bestimmen."""
    spec = make_spec(dim(16, "diameter", id="d16", fit="H7"), dim(30, id="p30", tol_upper=0.1, tol_lower=-0.1))
    by_id = {c["dim_id"]: c for c in compliance(spec, [], hole_report(16.0, ("d16", "p30")), "nominal")}
    assert (by_id["d16"]["values"], by_id["p30"]["values"]) == ([16.0], [])


def test_measured_diameter_only_from_hole_checks():
    """Die Maßprüfung eines Drehteils kann auf sein Durchmessermaß verweisen; der Istwert ist kein Zylinder."""
    spec = make_spec(dim(40, "diameter", id="d40", tol_upper=0.0, tol_lower=-0.1))
    size_check = {"check": "bbox_y", "expected": 40.0, "actual": 39.95, "ok": True, "dim_ids": ["d40"]}
    assert compliance(spec, [], {"ok": True, "dims": [size_check]}, "nominal")[0]["values"] == []


def test_compliance_lists_tolerated_dimensions_only():
    spec = make_spec(
        dim(31, id="a", tol_upper=0.0, tol_lower=-0.1), dim(25, id="b"), dim(45, "angle", id="c"),
        general="ISO 2768-m")
    result = compliance(spec, [], {"ok": True, "dims": []}, "nominal")
    assert [(c["dim_id"], c["source"], c["values"]) for c in result] == [("a", "explicit", []), ("b", "general", [])]


def test_compliance_unbound_dim_is_not_checked():
    """A tolerated dimension without an op value or a measured hole has nothing to check: None, not True."""
    spec = make_spec(dim(16, "diameter", id="d16", fit="H7"), dim(31, id="l31", tol_upper=0.0, tol_lower=-0.1))
    ops = plain_ops(extrude(31, {"distance": "l31"}))
    by_id = {c["dim_id"]: c for c in compliance(spec, ops, {"ok": True, "dims": []}, "nominal")}
    assert (by_id["d16"]["values"], by_id["d16"]["in_tolerance"]) == ([], None)
    assert (by_id["l31"]["values"], by_id["l31"]["in_tolerance"]) == ([31], True)
    # a hole the build found counts as a value, so the diameter is checked after all
    measured = compliance(spec, ops, hole_report(16.0), "nominal")[0]
    assert (measured["values"], measured["in_tolerance"]) == ([16.0], True)


def test_tables_found(monkeypatch, tmp_path):
    assert tables_found()  # conftest points D2C_TABLES at the test tables
    monkeypatch.setenv("D2C_TABLES", str(tmp_path))
    assert not tables_found()
    (tmp_path / "x_isolim.xml").write_text("<ISOLIM/>")
    assert not tables_found()  # ISO 286 alone is not enough
    (tmp_path / "x_toltabs.xml").write_text("<TOLTABS/>")
    assert tables_found()
    monkeypatch.setenv("D2C_TABLES", str(tmp_path / "missing"))
    assert not tables_found()

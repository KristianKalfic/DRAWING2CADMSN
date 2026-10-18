"""Toleranzen: Passungen nach ISO 286, Allgemeintoleranzen nach ISO 2768, Toleranzmitte und Einhaltung.

Unter `d2c/demo/tables` liegen kleine Tabellen im Format der Toleranztabellen mit öffentlichen ISO-Werten."""

import pytest
from helpers import plain_ops

from d2c.quality.tolerances import ToleranceError, apply_mode, compliance, limits, mid_value, unbound
from d2c.spec import Dimension, PartSpec

# ISO 2768-1: lineare Maße über 6 bis 30 mm
GENERAL_25MM = [("ISO 2768-f", 0.1), ("ISO 2768-m", 0.2), ("ISO 2768-c", 0.5), ("ISO 2768-v", 1.0)]


def dim(value, kind="length", id="d", **fields) -> Dimension:
    """Ein aus einer Zeichnung ausgelesenes Maß; wie in der Spezifikation verlangt, enthält der Text den Maßwert."""
    return Dimension(id=id, kind=kind, value=value, text=f"{value:g}", source=None, confidence=0.95, **fields)


def make_spec(*dimensions, general=None) -> PartSpec:
    return PartSpec(
        name="part",
        drawing="part/part.pdf",
        title="part",
        scale="1:1",
        general_tolerance=general,
        views=[{"id": "v1", "kind": "front", "scale": "1:1"}],
        dimensions=list(dimensions),
        features=[],
        expected={"bbox": (1, 1, 1)},
        review=[],
    )


def extrude(distance=10, refs=None) -> dict:
    return {
        "op": "extrude",
        "profile": {"type": "rect", "center": (0, 0), "w": 40, "h": 40},
        "plane": {"name": "XY"},
        "distance": distance,
        "refs": refs or {},
    }


def bore(d, positions=((0, 0),), refs=None) -> dict:
    return {"op": "holes", "plane": {"name": "XY", "offset": 10}, "positions": list(positions), "d": d, "refs": refs or {}}


def hole_report(measured, dim_ids=("d16",)) -> dict:
    """Worker-Bericht mit Begrenzungsmaß und einer Bohrungsprüfung im Format von `checks.dimension_check`."""
    actual = None if measured is None else {"d": measured, "dist": 0.0}
    hole = {
        "check": "hole",
        "expected": {"d": 16.0, "at": (0.0, 0.0, 0.0), "axis": (0.0, 0.0, 1.0)},
        "actual": actual,
        "ok": actual is not None,
        "dim_ids": list(dim_ids),
    }
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
    with pytest.raises(ToleranceError, match="no row for"):
        limits(dim(nominal, "diameter", fit="h10"), None)


@pytest.mark.parametrize(
    "nominal, deviation",
    [
        (0.0, 0.0),
        (0.4, 0.0),  # from=0,0 up_to=0,5: endet vor 0,5
        (0.5, 0.1),
        (3.0, 0.1),  # from=0,5 to=3,0: beginnt bei 0,5 und schließt 3,0 ein
        (3.5, 0.1),
        (6.0, 0.1),
        (6.5, 0.2),  # Zeilen mit „über“ und „bis“
    ],
)
def test_range_attribute_styles(nominal, deviation):
    assert limits(dim(nominal), "ISO 2768-m") == (-deviation, deviation, "general")


def test_explicit_beats_fit():
    printed = dim(6, "diameter", fit="h10", tol_upper=0.1, tol_lower=-0.1)
    assert limits(printed, None) == (-0.1, 0.1, "explicit")
    assert limits(printed, "ISO 2768-m") == (-0.1, 0.1, "explicit")


def test_reference_dimension_in_brackets_has_no_general_tolerance():
    """Ein Hilfsmaß "(65)" ist nicht toleriert; mit eingetragenen Abmaßen gelten diese weiter."""
    reference = Dimension(id="d", kind="length", value=65, text="(65)", source=None, confidence=0.95)
    assert limits(reference, "ISO 2768-m") == (0.0, 0.0, "none")
    assert limits(reference.model_copy(update={"text": "65"}), "ISO 2768-m") == (-0.3, 0.3, "general")
    assert limits(reference.model_copy(update={"tol_upper": 0.1, "tol_lower": 0.0}), "ISO 2768-m") == (0.0, 0.1, "explicit")
    assert compliance(make_spec(reference, general="ISO 2768-m"), [], {}, "nominal") == []


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


def test_missing_tables_fall_back_to_the_standard_tables(monkeypatch, tmp_path):
    """Der Ordner wird bei jedem Aufruf neu gelesen; ohne Datei gelten die Standardtabellen (siehe test_standard_tables)."""
    monkeypatch.setenv("D2C_TABLES", str(tmp_path))
    assert limits(dim(6, "diameter", fit="h10"), None) == (-0.048, 0.0, "fit")
    assert limits(dim(25), "ISO 2768-m") == (-0.2, 0.2, "general")
    monkeypatch.setenv("D2C_TABLES", str(tmp_path / "gibt es nicht"))
    assert limits(dim(25), "ISO 2768-m") == (-0.2, 0.2, "general")
    assert limits(dim(25, tol_upper=0.1, tol_lower=0.0), "ISO 2768-m") == (0.0, 0.1, "explicit")


def test_external_table_file_overrides_only_its_own_standard(monkeypatch, tmp_path):
    """Eine ISO-286-Datei in D2C_TABLES ersetzt die Standard-Passungen, die ISO-2768-Tabelle bleibt Standard."""
    (tmp_path / "x_isolim.xml").write_text('<ISOLIM><h10><value over="3" to="6" upper="0.0" lower="-0.5"/></h10></ISOLIM>')
    monkeypatch.setenv("D2C_TABLES", str(tmp_path))
    assert limits(dim(6, "diameter", fit="h10"), None) == (-0.5, 0.0, "fit")
    with pytest.raises(ToleranceError, match="fit H7"):  # die Datei kennt H7 nicht, die Standardtabelle wird nicht beigemischt
        limits(dim(16, "diameter", fit="H7"), None)
    assert limits(dim(25), "ISO 2768-m") == (-0.2, 0.2, "general")


def test_first_matching_file_is_read_in_its_declared_encoding(monkeypatch, tmp_path):
    table = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>\n<!-- Passung für Außenmaße -->\n'
        '<ISOLIM><h10><value over="3" to="6" upper="0.0" lower="{}"/></h10></ISOLIM>\n'
    )
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
        dim(16, "diameter", id="d16", fit="H7"), dim(30, id="p30", tol_upper=0.2, tol_lower=0.0), dim(10, id="t10"), general="ISO 2768-m"
    )
    ops = plain_ops(extrude(10, {"distance": "t10"}), extrude(), extrude(), bore(16, ((0, 30),), {"d": "d16", "positions.0.1": "p30"}))

    mid_ops, changes, skipped = apply_mode(ops, spec, "mid")

    assert mid_ops[3]["d"] == 16.009
    assert mid_ops[3]["positions"] == [(0, 30.1)]  # ein Pfad durch eine Liste und ein Tupel
    assert mid_ops[:3] == ops[:3]  # t10 ist symmetrisch (ISO 2768-m): keine Verschiebung nötig
    assert changes == [
        {"op_index": 3, "path": "d", "dim_id": "d16", "nominal": 16, "mid": 16.009, "factor": 1.0, "value": 16.009},
        {"op_index": 3, "path": "positions.0.1", "dim_id": "p30", "nominal": 30, "mid": 30.1, "factor": 1.0, "value": 30.1},
    ]
    assert skipped == []
    assert (ops[3]["d"], ops[3]["positions"]) == (16, [(0, 30)])  # die Eingabe bleibt unverändert


def test_apply_mode_leaves_untoleranced_dimensions_alone():
    """Ohne Toleranz gibt es keine Toleranzmitte: Ein aus Maßen abgeleiteter Wert (15 = 25 - 10) bleibt unverändert."""
    spec = make_spec(dim(25, id="h25"))
    ops = plain_ops(extrude(15, {"distance": "h25"}))
    assert apply_mode(ops, spec, "mid") == (ops, [], [])


def test_apply_mode_nominal_is_a_plain_copy():
    spec = make_spec(dim(16, "diameter", id="d16", fit="H7"))
    ops = plain_ops(extrude(), bore(16, refs={"d": "d16"}))
    copied, changes, skipped = apply_mode(ops, spec, "nominal")
    assert (copied, changes, skipped) == (ops, [], [])
    assert copied is not ops and copied[1] is not ops[1]


def test_compliance_flags_nominal_outside():
    spec = make_spec(dim(31, id="l31", tol_upper=-0.1, tol_lower=-0.3))
    ops = plain_ops(extrude(31, {"distance": "l31"}))
    report = {"ok": True, "dims": []}

    assert compliance(spec, ops, report, "nominal") == [
        {
            "dim_id": "l31",
            "text": "31",
            "nominal": 31,
            "lower": -0.3,
            "upper": -0.1,
            "source": "explicit",
            "values": [31],
            "in_tolerance": False,
            "geometry": False,
        }
    ]
    mid = compliance(spec, ops, report, "mid")[0]
    assert (mid["values"], mid["in_tolerance"]) == ([30.8], True)
    # Bereits auf die Toleranzmitte gesetzte Operationen liefern dasselbe Ergebnis.
    moved, _, _ = apply_mode(ops, spec, "mid")
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
    spec = make_spec(dim(31, id="a", tol_upper=0.0, tol_lower=-0.1), dim(25, id="b"), dim(45, "angle", id="c"), general="ISO 2768-m")
    result = compliance(spec, [], {"ok": True, "dims": []}, "nominal")
    assert [(c["dim_id"], c["source"], c["values"]) for c in result] == [("a", "explicit", []), ("b", "general", [])]


def test_compliance_unbound_dim_is_not_checked():
    """Ein toleriertes Maß ohne Op-Wert oder gemessene Bohrung hat nichts zu prüfen: None, nicht True."""
    spec = make_spec(dim(16, "diameter", id="d16", fit="H7"), dim(31, id="l31", tol_upper=0.0, tol_lower=-0.1))
    ops = plain_ops(extrude(31, {"distance": "l31"}))
    by_id = {c["dim_id"]: c for c in compliance(spec, ops, {"ok": True, "dims": []}, "nominal")}
    assert (by_id["d16"]["values"], by_id["d16"]["in_tolerance"]) == ([], None)
    assert (by_id["l31"]["values"], by_id["l31"]["in_tolerance"]) == ([31], True)
    # eine Bohrung, die der Aufbau gefunden hat, zählt als Wert, also wird der Durchmesser doch geprüft
    measured = compliance(spec, ops, hole_report(16.0), "nominal")[0]
    assert (measured["values"], measured["in_tolerance"]) == ([16.0], True)


def offset_cut(offset, refs=None) -> dict:
    """Ein Ausschnitt, dessen Ebenenversatz ein Maß (oder sein Halbmaß) trägt."""
    return {
        "op": "extrude",
        "profile": {"type": "rect", "center": (0, 0), "w": 4, "h": 4},
        "plane": {"name": "XZ", "offset": offset},
        "distance": -6,
        "mode": "cut",
        "refs": refs or {},
    }


D12 = dim(6.1, id="d12", tol_upper=0.0, tol_lower=-0.05)


@pytest.mark.parametrize("value, shown", [(6.1, 6.1), (-3.05, 6.1), (3.05, 6.1), (12.2, 6.1), (-12.2, 6.1)])
def test_compliance_scaled_binding_is_checked(value, shown):
    """Ein gebundener Wert kann das Maß selbst, sein Halbmaß (Versatz) oder sein Doppeltes sein; geprüft wird das Maß."""
    ops = plain_ops(offset_cut(value, {"plane.offset": "d12"}))
    entry = compliance(make_spec(D12), ops, {"ok": True, "dims": []}, "nominal")[0]
    assert (entry["values"], entry["in_tolerance"]) == ([shown], True)
    assert "note" not in entry


def test_compliance_unrelated_binding_is_not_a_violation():
    ops = plain_ops(offset_cut(4.2, {"plane.offset": "d12"}))
    entry = compliance(make_spec(D12), ops, {"ok": True, "dims": []}, "nominal")[0]
    assert (entry["values"], entry["in_tolerance"]) == ([], None)
    assert "4.2" in entry["note"] and "d12" in entry["note"]


@pytest.mark.parametrize("value, shown", [(6.3, 6.3), (-6.3, 6.3), (-3.15, 6.3), (3.15, 6.3), (12.6, 6.3), (5.9, 5.9)])
def test_compliance_binding_near_nominal_outside_band_is_a_violation(value, shown):
    """Ein Wert nahe am Nennmaß (höchstens max(0,5 mm; 5 %)), aber ausserhalb des Bands, ist ein echter Verstoß."""
    ops = plain_ops(offset_cut(value, {"plane.offset": "d12"}))
    entry = compliance(make_spec(D12), ops, {"ok": True, "dims": []}, "nominal")[0]
    assert (entry["values"], entry["in_tolerance"]) == ([shown], False)
    assert "note" not in entry


def test_apply_mode_moves_near_values_too():
    ops = plain_ops(offset_cut(-3.15, {"plane.offset": "d12"}), offset_cut(6.3, {"plane.offset": "d12"}))
    mid_ops, _, _ = apply_mode(ops, make_spec(D12), "mid")
    assert [op["plane"]["offset"] for op in mid_ops] == [-3.0375, 6.075]


def test_compliance_scaled_binding_still_flags_real_violation():
    spec = make_spec(dim(31, id="l31", tol_upper=-0.1, tol_lower=-0.3))
    ops = plain_ops(offset_cut(-15.5, {"plane.offset": "l31"}))  # Halbmaß 15,5 = 31/2, ausserhalb der Grenzen
    entry = compliance(spec, ops, {"ok": True, "dims": []}, "nominal")[0]
    assert (entry["values"], entry["in_tolerance"]) == ([31], False)


def test_compliance_mid_with_half_binding():
    ops = plain_ops(offset_cut(-3.05, {"plane.offset": "d12"}))
    entry = compliance(make_spec(D12), ops, {"ok": True, "dims": []}, "mid")[0]
    assert (entry["values"], entry["in_tolerance"]) == ([6.075], True)


def test_apply_mode_scales_half_and_double_and_skips_unrelated():
    spec = make_spec(D12)
    ops = plain_ops(
        offset_cut(-3.05, {"plane.offset": "d12"}),
        offset_cut(3.05, {"plane.offset": "d12"}),
        offset_cut(12.2, {"plane.offset": "d12"}),
        offset_cut(6.1, {"plane.offset": "d12"}),
        offset_cut(4.2, {"plane.offset": "d12"}),
    )
    mid_ops, changes, _ = apply_mode(ops, spec, "mid")
    assert [op["plane"]["offset"] for op in mid_ops] == [-3.0375, 3.0375, 12.15, 6.075, 4.2]
    assert [c["op_index"] for c in changes] == [0, 1, 2, 3]


def test_compliance_measured_hole_without_dim_ids_uses_nominal():
    """Hat der Agent die Bohrung nicht mit dem Maß verknüpft, genügt der gleiche Nenndurchmesser."""
    spec = make_spec(dim(16, "diameter", id="d16", fit="H7"))
    entry = compliance(spec, [], hole_report(16.004, dim_ids=()), "nominal")[0]
    assert (entry["values"], entry["in_tolerance"]) == ([16.004], True)
    other = compliance(
        spec,
        [],
        hole_report(9.0, dim_ids=())
        | {"dims": [{"check": "hole", "expected": {"d": 9.0}, "actual": {"d": 9.0}, "ok": True, "dim_ids": []}]},
        "nominal",
    )[0]
    assert (other["values"], other["in_tolerance"]) == ([], None)


# Standardtabellen (ohne D2C_TABLES): bekannte Werte aus ISO 2768-1 und ISO 286-1


@pytest.fixture
def standard(monkeypatch, tmp_path):
    monkeypatch.setenv("D2C_TABLES", str(tmp_path))


@pytest.mark.parametrize(
    "general, nominal, deviation",
    [
        ("ISO 2768-f", 0.5, 0.05),
        ("ISO 2768-f", 3, 0.05),
        ("ISO 2768-f", 3.1, 0.05),
        ("ISO 2768-f", 100, 0.15),
        ("ISO 2768-f", 1000, 0.3),
        ("ISO 2768-m", 2, 0.1),
        ("ISO 2768-m", 6, 0.1),
        ("ISO 2768-m", 6.1, 0.2),
        ("ISO 2768-m", 120, 0.3),
        ("ISO 2768-m", 400, 0.5),
        ("ISO 2768-m", 1000, 0.8),
        ("ISO 2768-m", 2000, 1.2),
        ("ISO 2768-m", 4000, 2),
        ("ISO 2768-c", 3, 0.2),
        ("ISO 2768-c", 6, 0.3),
        ("ISO 2768-c", 30, 0.5),
        ("ISO 2768-c", 400, 1.2),
        ("ISO 2768-c", 4000, 4),
        ("ISO 2768-v", 6, 0.5),
        ("ISO 2768-v", 30, 1),
        ("ISO 2768-v", 120, 1.5),
        ("ISO 2768-v", 400, 2.5),
        ("ISO 2768-v", 4000, 8),
    ],
)
def test_standard_iso_2768(standard, general, nominal, deviation):
    assert limits(dim(nominal), general) == (-deviation, deviation, "general")


def test_standard_iso_2768_gaps_follow_the_norm(standard):
    with pytest.raises(ToleranceError, match="no row for"):
        limits(dim(2500), "ISO 2768-f")  # f ist über 2000 mm nicht festgelegt
    with pytest.raises(ToleranceError, match="no row for"):
        limits(dim(1), "ISO 2768-v")  # v beginnt bei 3 mm
    with pytest.raises(ToleranceError, match="no row for"):
        limits(dim(5000), "ISO 2768-m")


@pytest.mark.parametrize(
    "fit, nominal, lower, upper",
    [
        ("H6", 25, 0, 0.013),
        ("H7", 16, 0, 0.018),
        ("H7", 25, 0, 0.021),
        ("H7", 40, 0, 0.025),
        ("H7", 500, 0, 0.063),
        ("H8", 25, 0, 0.033),
        ("H9", 40, 0, 0.062),
        ("H11", 25, 0, 0.13),
        ("H11", 2, 0, 0.06),
        ("h6", 25, -0.013, 0),
        ("h7", 40, -0.025, 0),
        ("h9", 40, -0.062, 0),
        ("h6", 3, -0.006, 0),
        ("g6", 25, -0.02, -0.007),
        ("g6", 10, -0.014, -0.005),
        ("g6", 100, -0.034, -0.012),
        ("js6", 25, -0.0065, 0.0065),
        ("js6", 40, -0.008, 0.008),
        ("k6", 25, 0.002, 0.015),
        ("k6", 3, 0, 0.006),
        ("m6", 25, 0.008, 0.021),
        ("m6", 100, 0.013, 0.035),
        ("n6", 25, 0.015, 0.028),
        ("n6", 10, 0.01, 0.019),
        ("p6", 25, 0.022, 0.035),
        ("p6", 300, 0.056, 0.088),
        ("r6", 25, 0.028, 0.041),
        ("r6", 60, 0.041, 0.060),
        ("r6", 70, 0.043, 0.062),
        ("r6", 500, 0.132, 0.172),
    ],
)
def test_standard_iso_286(standard, fit, nominal, lower, upper):
    got = limits(dim(nominal, "diameter", fit=fit), None)
    assert got == (pytest.approx(lower), pytest.approx(upper), "fit")


@pytest.mark.parametrize("fit", "H6 H7 H8 H9 H11 g6 h6 h7 h9 js6 k6 m6 n6 p6 r6".split())
def test_standard_iso_286_covers_every_range_up_to_500(standard, fit):
    for nominal in (0.5, 3, 3.1, 6, 10, 18, 30, 50, 80, 120, 180, 250, 315, 400, 500):
        lower, upper, _ = limits(dim(nominal, "diameter", fit=fit), None)
        assert lower <= upper
    with pytest.raises(ToleranceError, match="no row for"):
        limits(dim(501, "diameter", fit=fit), None)


def test_standard_tables_agree_with_the_fixture_tables(monkeypatch, tmp_path):
    """Die Testtabellen sind Auszüge derselben Normwerte: Standard und Auszug liefern dasselbe."""
    cases = [
        (dim(16, "diameter", fit="H7"), None),
        (dim(6, "diameter", fit="h10"), None),
        (dim(6, "diameter", fit="H10"), None),
        *((dim(v), g) for g, _ in GENERAL_25MM for v in (25,)),
    ]
    cases.append((dim(100), "ISO 2768-m"))
    fixture = [limits(d, g) for d, g in cases]  # autouse-Fixture: D2C_TABLES zeigt auf die Testtabellen
    monkeypatch.setenv("D2C_TABLES", str(tmp_path))
    assert [limits(d, g) for d, g in cases] == fixture


def test_standard_it_grades_follow_the_formula(standard):
    """Gegenprobe der Tabellen mit i = 0,45 ∛D + 0,001 D (D = geometrisches Mittel des Bereichs), IT7 = 16 i, IT9 = 40 i."""
    for lo, hi in [(18, 30), (50, 80), (120, 180), (315, 400)]:
        i = 0.45 * (lo * hi) ** (1 / 6) + 0.001 * (lo * hi) ** 0.5
        for fit, factor in (("H7", 16), ("H9", 40)):
            upper = limits(dim(hi, "diameter", fit=fit), None)[1] * 1000
            assert upper == pytest.approx(factor * i, rel=0.05)


def test_mid_works_without_any_table_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("D2C_TABLES", str(tmp_path / "fehlt"))
    assert mid_value(dim(16, "diameter", fit="H7"), None) == 16.009
    assert mid_value(dim(40, "diameter", fit="k6"), None) == 40.01


# Bindung mit Faktor (F07)


def test_half_and_negated_binding_get_the_scaled_mid():
    """Die Nut bei y = -3,05 ist an das Maß 6,1 (0/-0,05) gebunden: Toleranzmitte 6,075, der Parameter wird -3,0375 und nicht +6,075."""
    spec = make_spec(dim(6.1, id="d12", tol_upper=0.0, tol_lower=-0.05))
    ops = plain_ops(extrude(refs={"plane.offset": "d12"}) | {"plane": {"name": "XY", "offset": -3.05}})
    mid_ops, changes, skipped = apply_mode(ops, spec, "mid")
    assert mid_ops[0]["plane"]["offset"] == -3.0375
    assert changes == [
        {"op_index": 0, "path": "plane.offset", "dim_id": "d12", "nominal": 6.1, "mid": 6.075, "factor": -0.5, "value": -3.0375}
    ]
    assert skipped == []
    assert ops[0]["plane"]["offset"] == -3.05  # Eingabe unverändert
    # die Prüfung rechnet den Faktor zurück: -3,0375 gehört zum Maß 6,075
    entry = compliance(spec, ops, {"ok": True, "dims": []}, "mid")[0]
    assert (entry["values"], entry["in_tolerance"]) == ([6.075], True)
    nominal = compliance(spec, ops, {"ok": True, "dims": []}, "nominal")[0]
    assert (nominal["values"], nominal["in_tolerance"]) == ([6.1], True)  # 6,1 ist das Höchstmaß


def test_half_width_binding_gets_half_of_the_mid():
    """Ein Rechteck mit w = 20 ist die halbe Breite 40 (0/-0,2): Mitte 39,9, der Parameter wird 19,95."""
    spec = make_spec(dim(40, id="b", tol_upper=0.0, tol_lower=-0.2))
    ops = plain_ops(extrude(refs={"profile.w": "b"}) | {"profile": {"type": "rect", "center": (0, 0), "w": 20, "h": 40}})
    mid_ops, changes, skipped = apply_mode(ops, spec, "mid")
    assert (mid_ops[0]["profile"]["w"], changes[0]["factor"], skipped) == (19.95, 0.5, [])
    # bereits verschoben: nichts ändert sich mehr und nichts wird übersprungen
    assert apply_mode(mid_ops, spec, "mid") == (mid_ops, [], [])


def test_binding_with_an_offset_is_skipped_with_a_reason():
    """Ein Wert, der weder das Maß noch seine Hälfte oder sein Gegenwert ist (Versatz), bleibt stehen und wird gemeldet."""
    spec = make_spec(dim(30, id="p30", tol_upper=0.2, tol_lower=0.0))
    ops = plain_ops(bore(16, ((0, 42),), {"positions.0.1": "p30"}))
    mid_ops, changes, skipped = apply_mode(ops, spec, "mid")
    assert mid_ops == ops and changes == []
    assert len(skipped) == 1 and (skipped[0]["op_index"], skipped[0]["path"], skipped[0]["dim_id"]) == (1 - 1, "positions.0.1", "p30")
    assert skipped[0]["value"] == 42 and "30" in skipped[0]["reason"]
    assert unbound(spec, ops, skipped) == ["p30"]  # eine übersprungene Bindung zählt als nicht gebunden


def test_unknown_class_is_skipped_instead_of_aborting():
    spec = make_spec(dim(16, "diameter", id="d16", fit="E8"))
    ops = plain_ops(bore(16, refs={"d": "d16"}))
    mid_ops, changes, skipped = apply_mode(ops, spec, "mid")
    assert mid_ops == ops and changes == [] and "fit E8" in skipped[0]["reason"]
    assert compliance(spec, ops, {"ok": True, "dims": []}, "nominal") == []


def test_unbound_lists_toleranced_dimensions_without_refs():
    spec = make_spec(
        dim(16, "diameter", id="d16", fit="H7"),
        dim(31, id="l31", tol_upper=0.0, tol_lower=-0.1),
        dim(25, id="sym"),
        dim(7, id="plain"),
        general="ISO 2768-m",
    )
    ops = plain_ops(bore(16, refs={"d": "d16"}))
    assert unbound(spec, ops, []) == ["l31"]  # sym: symmetrisch, nichts zu verschieben; plain: 7 mm hat ±0,2, auch symmetrisch


# Prüfung gegen die Geometrie (F19)

GEOMETRY = {"diameters": [6.6, 16.0, 68.0], "distances": [10.0, 25.0, 40.5]}


def test_unbound_dimension_is_checked_against_the_body():
    spec = make_spec(
        dim(68, "diameter", id="d68", tol_upper=0.2, tol_lower=0.0),
        dim(40.6, id="l", tol_upper=0.0, tol_lower=-0.3),
        dim(33, id="gone", tol_upper=0.1, tol_lower=-0.1),
        dim(8, "radius", id="r8", tol_upper=0.1, tol_lower=0.0),
    )
    by_id = {c["dim_id"]: c for c in compliance(spec, [], {"ok": True, "dims": []}, "nominal", GEOMETRY)}
    assert (by_id["d68"]["values"], by_id["d68"]["in_tolerance"], by_id["d68"]["geometry"]) == ([68.0], True, True)
    # 40,5 liegt im Band 40,3 bis 40,6, obwohl das Nennmaß 40,6 ist
    assert (by_id["l"]["values"], by_id["l"]["in_tolerance"], by_id["l"]["geometry"]) == ([40.5], True, True)
    assert (by_id["gone"]["values"], by_id["gone"]["in_tolerance"], by_id["gone"]["geometry"]) == ([], None, False)
    assert (by_id["r8"]["values"], by_id["r8"]["in_tolerance"], by_id["r8"]["geometry"]) == ([8.0], True, True)  # Ø16 ist R8


def test_geometry_finds_out_of_tolerance_values():
    spec = make_spec(dim(31, id="l31", tol_upper=-0.1, tol_lower=-0.3))
    found = compliance(spec, [], {"ok": True, "dims": []}, "nominal", {"diameters": [], "distances": [31.0]})[0]
    assert (found["values"], found["in_tolerance"], found["geometry"]) == ([31.0], False, True)  # Nennmaß statt Mitte 30,8


def test_mid_mode_judges_unbound_dimensions_by_the_geometry():
    """Ein nicht gebundenes Maß hat auch auf der Mitte ein Urteil, sobald der Körper gemessen ist."""
    spec = make_spec(dim(31, id="l31", tol_upper=-0.1, tol_lower=-0.3))
    report = {"ok": True, "dims": []}
    entry = compliance(spec, [], report, "mid", {"diameters": [], "distances": [30.8]})[0]
    assert (entry["values"], entry["in_tolerance"], entry["geometry"]) == ([30.8], True, True)
    assert compliance(spec, [], report, "mid")[0]["in_tolerance"] is None


def test_bound_values_beat_the_geometry():
    spec = make_spec(dim(25, id="h", tol_upper=0.0, tol_lower=-0.1))
    ops = plain_ops(extrude(25, {"distance": "h"}))
    entry = compliance(spec, ops, {"ok": True, "dims": []}, "nominal", {"diameters": [], "distances": [24.95]})[0]
    assert (entry["values"], entry["geometry"]) == ([25], False)

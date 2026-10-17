"""Prüfungen von Volumenkörpern und Zeichnungsmaßen, STEP-Rückimport und Referenzwerten."""

import math

import cadquery as cq
import pytest
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.Geom import Geom_CylindricalSurface, Geom_RectangularTrimmedSurface
from OCP.gp import gp_Ax3

from d2c import export
from d2c.checks import dimension_check, metrics, solid_check, step_roundtrip
from d2c.spec import Expected, ExpectedHole

PLATE = (40.0, 30.0, 10.0)  # um den Ursprung zentriert


def plate_with_hole(d: float = 5.0) -> cq.Workplane:
    """Platte mit einer durchgehenden Bohrung in Z-Richtung bei x=10, y=5."""
    return cq.Workplane().box(*PLATE).faces(">Z").workplane().pushPoints([(10, 5)]).hole(d)


def hole_entry(wp: cq.Workplane, **changes) -> dict:
    """Eintrag aus `dimension_check` für eine erwartete Bohrung: die tatsächliche Bohrung von `plate_with_hole`, mit `changes`."""
    hole = {"d": 5.0, "at": (10.0, 5.0, 0.0), "axis": (0.0, 0.0, 1.0), "dim_ids": ["d2"]} | changes
    return dimension_check(wp, Expected(bbox=PLATE, holes=[ExpectedHole(**hole)]))[-1]


def self_intersecting_solid() -> cq.Workplane:
    """Extrudiertes, selbstüberschneidendes Profil: ein geschlossener Körper mit positivem Volumen, der dennoch ungültig ist."""
    return cq.Workplane().polyline([(0, 0), (20, 10), (20, 0), (0, 4)]).close().extrude(5)


def test_box_solid_ok():
    assert solid_check(cq.Workplane().box(10, 20, 30)) == {
        "valid": True,
        "solids": 1,
        "free_edges": 0,
        "volume": pytest.approx(6000),
        "bbox": pytest.approx([-5, -10, -15, 5, 10, 15]),
        "ok": True,
    }

    # `build()` liefert zusammengesetzte Körper, die den Volumenkörper enthalten, keine einzelnen Volumenkörper
    plate = plate_with_hole()
    assert isinstance(plate.val(), cq.Compound)
    result = solid_check(plate)
    assert (result["solids"], result["ok"]) == (1, True)
    assert result["volume"] == pytest.approx(40 * 30 * 10 - math.pi * 2.5**2 * 10)


def test_two_solids_not_ok(tmp_path):
    """Alles außer genau einem geschlossenen, gültigen Volumenkörper schlägt fehl; jeder Fall verletzt einen anderen Teil von `ok`."""
    box = cq.Workplane().box(10, 10, 10)
    apart = box.union(box.translate((50, 0, 0)))
    stray_face = cq.Face.makePlane(10, 10, basePnt=(50, 0, 0))
    with_stray_face = cq.Workplane().add(cq.Compound.makeCompound([box.val(), stray_face]))
    second_box = box.translate((50, 0, 0))
    two_in_compound = cq.Workplane().add(cq.Compound.makeCompound([box.val(), second_box.val()]))
    cases = {  # Name: (Modell, (gültig, Volumenkörper, offene Kanten))
        "union of two solids": (apart, (True, 2, 0)),
        "compound of two solids": (two_in_compound, (True, 2, 0)),
        # Eine einzelne Fläche: Nur der Konstruktor für freie Begrenzungen findet ihre Kanten, wenn keine Toleranz angegeben ist.
        "lone face": (cq.Workplane().add(cq.Face.makePlane(10, 10)), (True, 0, 4)),
        "solid and stray face": (with_stray_face, (True, 1, 4)),
        "self-intersecting profile": (self_intersecting_solid(), (False, 1, 0)),
        # gültig, ein Volumenkörper, geschlossen, aber negatives Volumen
        "inside-out solid": (cq.Workplane().add(cq.Shape.cast(box.val().wrapped.Reversed())), (True, 1, 0)),
    }
    for name, (wp, counts) in cases.items():
        result = solid_check(wp)
        assert (result["valid"], result["solids"], result["free_edges"]) == counts, name
        assert result["ok"] is False, name

    # Beim STEP-Rückimport wird auch der zweite Volumenkörper erkannt.
    roundtrip = step_roundtrip(apart, str(tmp_path / "apart.step"))
    assert (roundtrip["solids"], roundtrip["ok"]) == (2, False)


def test_bbox_check_pass_and_fail():
    wp = cq.Workplane().box(10, 20, 30)
    passed = dimension_check(wp, Expected(bbox=(10, 20, 30)))
    assert [c["check"] for c in passed] == ["bbox_x", "bbox_y", "bbox_z"]
    assert [c["actual"] for c in passed] == pytest.approx([10, 20, 30])
    assert all(c["ok"] and c["dim_ids"] == [] for c in passed)

    # 0,009 mm Abweichung liegt innerhalb der Toleranz von 0,01 mm, 0,011 mm nicht.
    off = dimension_check(wp, Expected(bbox=(10.009, 20.011, 30)))
    assert [c["ok"] for c in off] == [True, False, True]
    assert off[1]["expected"] == 20.011
    assert off[1]["actual"] == pytest.approx(20)

    # Ein vom GLB-Export am Körper belassenes Netz darf das gemessene Maß nicht vergrößern.
    cylinder = cq.Workplane().cylinder(10, 5)
    cylinder.val().tessellate(1.0, 0.5)
    assert all(c["ok"] for c in dimension_check(cylinder, Expected(bbox=(10, 10, 10))))


def test_hole_found_by_diameter_and_position():
    wp = plate_with_hole()
    assert hole_entry(wp) == {
        "check": "hole",
        "expected": {"d": 5.0, "at": (10.0, 5.0, 0.0), "axis": (0.0, 0.0, 1.0)},
        "actual": {"d": pytest.approx(5.0), "dist": pytest.approx(0.0, abs=1e-9), "concave": True},
        "ok": True,
        "dim_ids": ["d2"],
    }
    # Jeder Punkt auf der Achse, beide Richtungen und jede Achslänge bezeichnen dieselbe Bohrung.
    assert hole_entry(wp, at=(10.0, 5.0, 3.0), axis=(0.0, 0.0, -2.0))["ok"]

    # Eine zylindrisch gesenkte Bohrung besteht aus zwei koaxialen Zylindern; beide Durchmesser werden erkannt.
    bore = cq.Workplane().box(*PLATE).faces(">Z").workplane().pushPoints([(10, 5)]).cboreHole(5, 8, 3)
    assert [hole_entry(bore, d=d)["actual"]["d"] for d in (5.0, 8.0)] == pytest.approx([5.0, 8.0])

    # Der passende Zylinder wird gemeldet, auch wenn die Achse eines Zylinders mit anderem Durchmesser näher an `at` liegt.
    drills = cq.Compound.makeCompound(
        [
            cq.Solid.makeCylinder(2.5, 10, cq.Vector(10.008, 5, -5)),  # Ø5 durchgehend, Achse um 0,008 versetzt: die Bohrung
            cq.Solid.makeCylinder(4.0, 3, cq.Vector(10.001, 5, 2)),  # Ø8 von oben, Achse um 0,001 versetzt
        ]
    )
    entry = hole_entry(cq.Workplane().box(*PLATE).cut(cq.Workplane(obj=drills)))
    assert entry["ok"]
    assert entry["actual"]["d"] == pytest.approx(5.0)


def test_hole_wrong_position_fails():
    wp = plate_with_hole()
    off = hole_entry(wp, at=(10.5, 5.0, 0.0))
    assert off["ok"] is False
    # Der nächstgelegene Zylinder ist die tatsächliche Bohrung, 0,5 mm entfernt.
    assert off["actual"] == {"d": pytest.approx(5.0), "dist": pytest.approx(0.5), "concave": True}
    assert hole_entry(wp, at=(10.005, 5.0, 0.0))["ok"]
    assert not hole_entry(wp, at=(10.02, 5.0, 0.0))["ok"]
    # Richtige Lage und richtiger Durchmesser, falsche Richtung: |cos| >= 0,9999 erlaubt eine Neigung von 0,014 je Einheit, aber nicht 0,015.
    assert not hole_entry(wp, axis=(1.0, 0.0, 0.0))["ok"]
    assert hole_entry(wp, axis=(0.014, 0.0, 1.0))["ok"]
    assert not hole_entry(wp, axis=(0.015, 0.0, 1.0))["ok"]


def test_hole_wrong_diameter_fails():
    wp = plate_with_hole()
    wrong = hole_entry(wp, d=6.0)
    assert wrong["ok"] is False
    assert wrong["actual"] == {"d": pytest.approx(5.0), "dist": pytest.approx(0.0, abs=1e-9), "concave": True}
    assert hole_entry(wp, d=5.009)["ok"]
    assert not hole_entry(wp, d=5.011)["ok"]
    # Ein konvexer Zylinder mit richtiger Größe und Lage ist keine Bohrung: ein Ø5-Zapfen auf der Oberseite ...
    pin = cq.Workplane().box(*PLATE).faces(">Z").workplane().center(10, 5).circle(2.5).extrude(4)
    pin_entry = hole_entry(pin)
    assert pin_entry["ok"] is False
    assert pin_entry["actual"] == {"d": pytest.approx(5.0), "dist": pytest.approx(0.0, abs=1e-9), "concave": False}
    # ... und eine Eckrundung R2,5, betrachtet von ihrem Bogenmittelpunkt aus.
    rounded = cq.Workplane().box(*PLATE).edges("|Z and >X and >Y").fillet(2.5)
    round_entry = hole_entry(rounded, at=(17.5, 12.5, 0.0))
    assert (round_entry["ok"], round_entry["actual"]["concave"]) == (False, False)
    # Ein Teil ohne zylindrische Fläche hat keinen nächstgelegenen Zylinder.
    missing = hole_entry(cq.Workplane().box(*PLATE))
    assert (missing["ok"], missing["actual"]) == (False, None)


def test_roundtrip_ok(tmp_path, monkeypatch):
    plate = plate_with_hole()
    path = str(tmp_path / "plate.step")
    result = step_roundtrip(plate, path)
    assert result == {
        "path": path,
        "reimport_valid": True,
        "solids": 1,
        "volume_rel_diff": pytest.approx(0, abs=1e-6),
        "marks": [],
        "pmi": {"written": 0, "read_back": 0},
        "ok": True,
    }
    assert (tmp_path / "plate.step").stat().st_size > 0
    assert step_roundtrip(plate, str(tmp_path / "plate.stp"))["ok"]  # Die Dateiendung ist unerheblich.

    # Entscheidend ist das Volumen nach dem Rückimport: Eine Abweichung von 3e-6 überschreitet die Grenze 1e-6 (nach oben oder unten), 3e-7 nicht.
    for scale, ok in ((1.000001, False), (0.999999, False), (1.0000001, True)):
        read_back = cq.Workplane(obj=plate.val().scale(scale))
        monkeypatch.setattr(cq.importers, "importStep", lambda _, wp=read_back: wp)
        assert step_roundtrip(plate, path)["ok"] is ok

    invalid = self_intersecting_solid()
    monkeypatch.setattr(cq.importers, "importStep", lambda _: invalid)
    result = step_roundtrip(invalid, path)
    assert (result["reimport_valid"], result["ok"]) == (False, False)


def test_marked_faces_colored(tmp_path):
    path = tmp_path / "box.step"
    above_top = (0, 0, 15.4)  # 0,4 mm über der Oberseite, innerhalb der standardmäßigen Reichweite von 0,5 mm
    marks = [{"id": "rv1", "near": [above_top]}]
    box = cq.Workplane(obj=cq.Compound.makeCompound(cq.Workplane().box(10, 20, 30).vals()))  # entspricht dem Rückgabewert von `build()`
    result = step_roundtrip(box, str(path), marks=marks)
    assert result["marks"] == [{"id": "rv1", "faces": 1}]
    assert result["ok"] is True
    text = path.read_text()
    assert text.count("COLOUR_RGB") >= 2  # steel body and the orange face
    assert "ADVANCED_FACE('PRUEFEN rv1'" in text
    assert "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF" in text
    back = cq.importers.importStep(str(path)).val()
    assert back.isValid()
    assert len(back.Solids()) == 1


def test_roundtrip_fails_when_pmi_is_lost(tmp_path, monkeypatch):
    plate = plate_with_hole()
    bore = next(f for f in plate.val().Faces() if f.geomType() == "CYLINDER")
    entry = {"type": "diameter", "face": bore, "value": 5.0, "tol_upper": None, "tol_lower": None, "fit": "H7"}
    result = step_roundtrip(plate, str(tmp_path / "plate.step"), pmi=[entry])
    assert (result["pmi"], result["ok"]) == ({"written": 1, "read_back": 1}, True)

    # a dimension that does not come back from the file fails the round trip while the solid is fine
    monkeypatch.setattr(export, "read_pmi", lambda _: {"dimensions": []})
    result = step_roundtrip(plate, str(tmp_path / "plate.step"), pmi=[entry])
    assert (result["pmi"], result["reimport_valid"], result["ok"]) == ({"written": 1, "read_back": 0}, True, False)


def test_roundtrip_colors_thread_faces(tmp_path):
    tapped = cq.Workplane().box(*PLATE).faces(">Z").workplane().pushPoints([(10, 5)]).hole(4.2)
    thread = {"at": (10.0, 5.0, 0.0), "axis": (0.0, 0.0, 1.0), "d": 4.2}
    path = str(tmp_path / "tapped.step")
    assert step_roundtrip(tapped, path, threads=[thread])["ok"] is True
    assert export.read_pmi(path)["colors"] == {"#99abb0": 1, "#65f073": 1}  # steel body, green thread


def test_mark_without_face_not_ok(tmp_path):
    marks = [
        {"id": "far", "near": [(55, 0, 0)]},  # 50 mm von der nächstgelegenen Fläche entfernt
        {"id": "just_off", "near": [(0, 0, 15.6)]},  # 0,6 mm über der Oberseite, außerhalb der Reichweite von 0,5 mm
    ]
    result = step_roundtrip(cq.Workplane().box(10, 20, 30), str(tmp_path / "box.step"), marks=marks)
    assert result["marks"] == [{"id": "far", "faces": 0}, {"id": "just_off", "faces": 0}]
    assert (result["reimport_valid"], result["solids"]) == (True, 1)  # Nur die Markierungen sind dafür verantwortlich.
    assert result["ok"] is False


def test_metrics_cyl_diameters():
    wp = cq.Workplane().box(*PLATE).faces(">Z").workplane()
    for x, y, d in [(-10, 0, 5), (-10, 8, 5.004), (10, 0, 8)]:  # 5,004 wird auf dasselbe Hundertstel wie 5 gerundet.
        wp = wp.pushPoints([(x, y)]).hole(d)
    result = metrics(wp.val())
    assert result["cyl_diameters"] == [5.0, 8.0]
    assert result["bbox_sorted"] == pytest.approx([10, 30, 40])
    removed = math.pi * (2.5**2 + 2.502**2 + 4**2) * 10
    assert result["volume"] == pytest.approx(40 * 30 * 10 - removed)


def test_cylinder_on_trimmed_surface():
    """STEP readers put cylinders on trimmed surfaces. Such a face has no point at the parameters (0.5, 0.5)."""
    surface = Geom_RectangularTrimmedSurface(Geom_CylindricalSurface(gp_Ax3(), 5.0), 2.0, 3.0, 2.0, 4.0)
    outside = cq.Face(BRepBuilderAPI_MakeFace(surface, 1e-7).Face())  # its normals point away from the Z axis
    inside = cq.Face(outside.wrapped.Reversed())  # its normals point to the axis, like those of a hole
    assert metrics(outside)["cyl_diameters"] == [10.0]
    hole = ExpectedHole(d=10, at=(0, 0, 0), axis=(0, 0, 1), dim_ids=[])
    for face, concave in ((outside, False), (inside, True)):
        entry = dimension_check(cq.Workplane().add(face), Expected(bbox=(1, 1, 1), holes=[hole]))[-1]
        assert (entry["ok"], entry["actual"]["concave"]) == (concave, concave)

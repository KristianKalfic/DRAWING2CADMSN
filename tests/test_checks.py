"""Prüfungen von Volumenkörpern und Zeichnungsmaßen, STEP-Rückimport und Referenzwerten."""

import math

import cadquery as cq
import pytest
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.Geom import Geom_CylindricalSurface, Geom_RectangularTrimmedSurface
from OCP.gp import gp_Ax3

from d2c.cad import export
from d2c.cad.checks import circle_diameters, dimension_candidates, dimension_check, dims_found, metrics, position_distances, solid_check
from d2c.cad.export import step_roundtrip
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
        "through": True,
        "entry": True,
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
    assert text.count("COLOUR_RGB") >= 2  # Stahlkörper und die orange Fläche
    assert "ADVANCED_FACE('CHECK rv1'" in text
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

    # ein Maß, das nicht aus der Datei zurückkommt, lässt die Hinreise fehlschlagen, während der Festkörper in Ordnung ist
    monkeypatch.setattr(export, "read_pmi", lambda _: {"dimensions": []})
    result = step_roundtrip(plate, str(tmp_path / "plate.step"), pmi=[entry])
    assert (result["pmi"], result["reimport_valid"], result["ok"]) == ({"written": 1, "read_back": 0}, True, False)


def test_roundtrip_colors_thread_faces(tmp_path):
    tapped = cq.Workplane().box(*PLATE).faces(">Z").workplane().pushPoints([(10, 5)]).hole(4.2)
    thread = {"at": (10.0, 5.0, 0.0), "axis": (0.0, 0.0, 1.0), "d": 4.2}
    path = str(tmp_path / "tapped.step")
    assert step_roundtrip(tapped, path, threads=[thread])["ok"] is True
    assert export.read_pmi(path)["colors"] == {"#99abb0": 1, "#65f073": 1}  # Stahlkörper, grünes Gewinde


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
    """STEP-Leser setzen Zylinder auf abgeschnittene Flächen. Eine solche Fläche hat keinen Punkt an den Parametern (0,5, 0,5)."""
    surface = Geom_RectangularTrimmedSurface(Geom_CylindricalSurface(gp_Ax3(), 5.0), 2.0, 3.0, 2.0, 4.0)
    outside = cq.Face(BRepBuilderAPI_MakeFace(surface, 1e-7).Face())  # ihre Normalen weisen von der Z-Achse weg
    inside = cq.Face(outside.wrapped.Reversed())  # ihre Normalen weisen zur Achse, wie bei einer Bohrung
    assert metrics(outside)["cyl_diameters"] == [10.0]
    hole = ExpectedHole(d=10, at=(0, 0, 0), axis=(0, 0, 1), dim_ids=[])
    for face, concave in ((outside, False), (inside, True)):
        entry = dimension_check(cq.Workplane().add(face), Expected(bbox=(1, 1, 1), holes=[hole]))[-1]
        assert (entry["ok"], entry["actual"]["concave"]) == (concave, concave)


def drilled(*cuts: cq.Solid) -> cq.Workplane:
    """Die Platte mit den gegebenen Zylindern herausgeschnitten."""
    return cq.Workplane().box(*PLATE).cut(cq.Workplane(obj=cq.Compound.makeCompound(list(cuts))))


def test_through_hole_runs_through():
    through = hole_entry(plate_with_hole())
    assert (through["through"], through["entry"], through["ok"]) == (True, True, True)
    bore = cq.Workplane().box(*PLATE).faces(">Z").workplane().pushPoints([(10, 5)]).cboreHole(5, 8, 3)
    assert hole_entry(bore, d=5.0)["through"] is True


def test_blind_hole_is_not_through_but_has_an_entry():
    """Ein Sackloch hat einen Eintritt, aber der Strahl trifft den Lochgrund: ok, nicht durchgehend."""
    blind = drilled(cq.Solid.makeCylinder(2.5, 6, cq.Vector(10, 5, 0)))  # von unten, 6 von 10 mm tief
    entry = hole_entry(blind)
    assert (entry["through"], entry["entry"], entry["ok"]) == (False, True, True)


def test_hole_closed_by_a_web_is_not_through():
    """Ein Steg quer in der Bohrung: beide Hälften sind offen, aber die Achse trifft den Steg."""
    web = cq.Workplane().box(*PLATE).faces(">Z").workplane().pushPoints([(10, 5)]).hole(5)
    web = web.union(cq.Workplane().box(10, 10, 2).translate((10, 5, 0)))
    entry = hole_entry(web)
    assert (entry["through"], entry["entry"], entry["ok"]) == (False, True, True)


def test_closed_cavity_has_no_entry():
    """Ein Hohlraum im Inneren hat die Fläche einer Bohrung, aber keinen Zugang von außen: nicht ok."""
    cavity = drilled(cq.Solid.makeCylinder(2.5, 4, cq.Vector(10, 5, -2)))
    entry = hole_entry(cavity)
    assert (entry["through"], entry["entry"], entry["ok"]) == (False, False, False)
    assert entry["actual"]["d"] == pytest.approx(5.0)


def test_hole_not_found_has_no_through_verdict():
    entry = hole_entry(plate_with_hole(), d=6.0)
    assert (entry["through"], entry["entry"], entry["ok"]) == (None, None, False)


def printed(dim_id: str, kind: str, value: float, **more) -> dict:
    return {"id": dim_id, "kind": kind, "value": value, "text": f"{value:g}", "assumed": False} | more


def test_dims_found_finds_sizes_in_the_body():
    """Maße der Platte mit Bohrung: Hüllquader, Bohrungs-Ø und -radius, Abstand der Bohrungsachse zur Plattenkante."""
    wp = plate_with_hole()
    dims = [
        printed("w", "length", 40),
        printed("t", "length", 10),
        printed("d", "diameter", 5),
        printed("r", "radius", 2.5),
        printed("e", "length", 10.04),
    ]  # 10 mm von der Kante x=20
    result = dims_found(wp.val(), dims)
    assert [m["id"] for m in result["found"]] == ["w", "t", "d", "r", "e"]
    assert result["missing"] == []
    assert result["found"][0]["nearest"] == pytest.approx(40)


def test_dims_found_reports_a_wrong_dimension_as_missing():
    dims = [printed("ok", "length", 40), printed("falsch", "length", 68.5), printed("knapp", "length", 40.06)]
    result = dims_found(plate_with_hole().val(), dims)
    assert [m["id"] for m in result["found"]] == ["ok"]
    assert result["missing"] == [{"id": "falsch", "text": "68.5", "value": 68.5}, {"id": "knapp", "text": "40.06", "value": 40.06}]


def test_dims_found_skips_assumed_angle_and_count_dimensions():
    dims = [
        printed("a", "length", 99, assumed=True),
        printed("w", "angle", 50),
        printed("n", "count", 4),
        printed("c", "chamfer", 1.5),
        printed("t", "thread", 8),
    ]
    assert dims_found(plate_with_hole().val(), dims) == {"found": [], "missing": []}


def test_dim_candidates_cover_distances_between_features():
    """Ebenen und Zylinderachsen: Abstand paralleler Ebenen, paralleler Achsen und einer Achse von einer Ebene."""
    plate = cq.Workplane().box(40, 30, 10).faces(">Z").workplane().pushPoints([(-12, 0), (12, 0)]).hole(4)
    found = set(dimension_candidates(plate.val()).round(6))
    assert {40.0, 30.0, 10.0, 24.0, 8.0, 2.0, 4.0}.issubset(found)  # 8 = 20 - 12: Achse zu Plattenkante x=-20
    assert 0.0 not in found


def test_dims_found_finds_the_distance_of_a_hole_from_the_edge():
    """Lagemaß: Bohrungsmitte 38 mm von der Unterkante. Ein anderer Abstand bleibt unbelegt."""
    plate = cq.Workplane().box(100, 60, 10).faces(">Z").workplane().pushPoints([(12, 8)]).hole(6)  # Kante y=-30: 38; Kante x=-50: 62
    dims = [printed("lage", "length", 38), printed("rand", "length", 62), printed("falsch", "length", 37)]
    result = dims_found(plate.val(), dims)
    assert [m["id"] for m in result["found"]] == ["lage", "rand"]
    assert [m["id"] for m in result["missing"]] == ["falsch"]


def test_dims_found_finds_distances_between_holes_along_the_main_directions():
    """Zwei Bohrungen mit Versatz in beiden Richtungen: Abstand in X und in Y sowie der direkte Abstand."""
    plate = cq.Workplane().box(200, 100, 10).faces(">Z").workplane().pushPoints([(-40, 10), (40, 25)]).hole(6)
    dims = [printed("dx", "length", 80), printed("dy", "length", 15), printed("direkt", "length", 81.394), printed("falsch", "length", 70)]
    result = dims_found(plate.val(), dims)
    assert [m["id"] for m in result["found"]] == ["dx", "dy", "direkt"]
    assert [m["id"] for m in result["missing"]] == ["falsch"]


def test_position_distances_cover_the_end_arcs_of_a_slot():
    """Langloch: Die Endbögen sind Zylinder, ihr Mittenabstand ist das Lagemaß des Langlochs."""
    plate = cq.Workplane().box(80, 40, 10).faces(">Z").workplane().slot2D(42, 8).cutThruAll()
    found = set(position_distances(plate.val()).round(6))
    assert {34.0, 20.0, 23.0}.issubset(found)  # 34 = 42 - 8; 20: Achse zu Kante y=±20; 23: Bogenmitte x=17 zu Kante x=-40


def test_position_distances_pair_only_parallel_axes():
    """Eine Querbohrung bildet mit der Bohrung in Z kein Paar; es bleiben nur Abstände von Achse zu Ebene."""
    plate = cq.Workplane().box(60, 40, 30).faces(">Z").workplane().hole(4)
    cross = cq.Workplane("YZ").center(5, 0).circle(1.5).extrude(100, both=True)  # Achse in X bei y=5, z=0
    found = position_distances(plate.cut(cross).val())
    assert set(found.round(6)) == {15.0, 20.0, 25.0, 30.0}


def test_dims_found_stays_fast_with_many_faces():
    """Ein Teil mit über 300 Flächen: die Maßsuche braucht deutlich weniger als 2 s."""
    import time

    pts = [(x * 11 - 140, y * 15 - 90) for x in range(27) for y in range(12)]
    wp = cq.Workplane().box(300, 200, 20).faces(">Z").workplane().pushPoints(pts).hole(6)
    assert len(wp.val().Faces()) > 300
    started = time.monotonic()
    result = dims_found(wp.val(), [printed("l", "length", 300), printed("x", "length", 77.7)])
    assert time.monotonic() - started < 2
    assert [m["id"] for m in result["missing"]] == ["x"]


def test_dims_found_drops_hole_pairs_above_the_axis_limit():
    """Grenze: Mit mehr als MAX_AXES Achsen entfallen die Achspaare, der Abstand von Achse zu Plattenkante bleibt."""
    pts = [(x * 11 - 140, y * 15 - 90) for x in range(27) for y in range(12)]
    wp = cq.Workplane().box(300, 200, 20).faces(">Z").workplane().pushPoints(pts).hole(6)
    result = dims_found(wp.val(), [printed("kante", "length", 10), printed("teilung", "length", 77)])  # Kante x=-150 zu Achse -140; 7 · 11
    assert [m["id"] for m in result["found"]] == ["kante"]
    assert [m["id"] for m in result["missing"]] == ["teilung"]


def test_circle_diameters_cover_cylinders_cone_rims_and_arcs():
    """Platte mit Bohrung Ø5, Kegelsenkung Ø9 und einer Eckrundung R3 (Bogen Ø6); gesucht sind die Gegenstücke gemessener Kreise."""
    wp = plate_with_hole(5).faces(">Z").workplane().pushPoints([(-10, 5)]).cskHole(4, 9, 90).edges("|Z and (>X and >Y)").fillet(3)
    assert circle_diameters(wp.val()) == [4.0, 5.0, 6.0, 9.0]
    assert circle_diameters(cq.Workplane().box(10, 10, 10).val()) == []

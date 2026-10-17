"""Validation checks: solid integrity, drawing dimensions, STEP round trip, reference metrics."""

import math

import cadquery as cq
import pytest

from d2c.checks import dimension_check, metrics, solid_check, step_roundtrip
from d2c.spec import Expected, ExpectedHole

PLATE = (40.0, 30.0, 10.0)  # centred on the origin


def plate_with_hole(d: float = 5.0) -> cq.Workplane:
    """Plate with a through hole along Z at x=10, y=5."""
    return cq.Workplane().box(*PLATE).faces(">Z").workplane().pushPoints([(10, 5)]).hole(d)


def hole_entry(wp: cq.Workplane, **changes) -> dict:
    """The dimension_check entry for one expected hole: the real hole of plate_with_hole, with `changes`."""
    hole = {"d": 5.0, "at": (10.0, 5.0, 0.0), "axis": (0.0, 0.0, 1.0), "dim_ids": ["d2"]} | changes
    return dimension_check(wp, Expected(bbox=PLATE, holes=[ExpectedHole(**hole)]))[-1]


def self_intersecting_solid() -> cq.Workplane:
    """Extruded bowtie profile: one closed solid with positive volume that is still not valid."""
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

    # build() results are compounds that hold the solid, not bare solids
    plate = plate_with_hole()
    assert isinstance(plate.val(), cq.Compound)
    result = solid_check(plate)
    assert (result["solids"], result["ok"]) == (1, True)
    assert result["volume"] == pytest.approx(40 * 30 * 10 - math.pi * 2.5**2 * 10)


def test_two_solids_not_ok(tmp_path):
    """Anything but one closed, valid solid fails; each case breaks a different part of `ok`."""
    box = cq.Workplane().box(10, 10, 10)
    apart = box.union(box.translate((50, 0, 0)))
    stray_face = cq.Face.makePlane(10, 10, basePnt=(50, 0, 0))
    with_stray_face = cq.Workplane().add(cq.Compound.makeCompound([box.val(), stray_face]))
    second_box = box.translate((50, 0, 0))
    two_in_compound = cq.Workplane().add(cq.Compound.makeCompound([box.val(), second_box.val()]))
    cases = {  # name: (model, (valid, solids, free_edges))
        "union of two solids": (apart, (True, 2, 0)),
        "compound of two solids": (two_in_compound, (True, 2, 0)),
        # one face alone: only the free-bounds constructor without a tolerance finds its edges
        "lone face": (cq.Workplane().add(cq.Face.makePlane(10, 10)), (True, 0, 4)),
        "solid and stray face": (with_stray_face, (True, 1, 4)),
        "self-intersecting profile": (self_intersecting_solid(), (False, 1, 0)),
        # valid, one solid, closed, but negative volume
        "inside-out solid": (cq.Workplane().add(cq.Shape.cast(box.val().wrapped.Reversed())), (True, 1, 0)),
    }
    for name, (wp, counts) in cases.items():
        result = solid_check(wp)
        assert (result["valid"], result["solids"], result["free_edges"]) == counts, name
        assert result["ok"] is False, name

    # the STEP round trip sees the second solid too
    roundtrip = step_roundtrip(apart, str(tmp_path / "apart.step"))
    assert (roundtrip["solids"], roundtrip["ok"]) == (2, False)


def test_bbox_check_pass_and_fail():
    wp = cq.Workplane().box(10, 20, 30)
    passed = dimension_check(wp, Expected(bbox=(10, 20, 30)))
    assert [c["check"] for c in passed] == ["bbox_x", "bbox_y", "bbox_z"]
    assert [c["actual"] for c in passed] == pytest.approx([10, 20, 30])
    assert all(c["ok"] and c["dim_ids"] == [] for c in passed)

    # 0.009 mm off is inside the 0.01 mm tolerance, 0.011 mm is not
    off = dimension_check(wp, Expected(bbox=(10.009, 20.011, 30)))
    assert [c["ok"] for c in off] == [True, False, True]
    assert off[1]["expected"] == 20.011
    assert off[1]["actual"] == pytest.approx(20)

    # a mesh left on the shape by a GLB export must not enlarge the measured size
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
    # any point on the axis, either direction, any axis length: same hole
    assert hole_entry(wp, at=(10.0, 5.0, 3.0), axis=(0.0, 0.0, -2.0))["ok"]

    # a counterbored hole is two coaxial cylinders: each diameter is found
    bore = cq.Workplane().box(*PLATE).faces(">Z").workplane().pushPoints([(10, 5)]).cboreHole(5, 8, 3)
    assert [hole_entry(bore, d=d)["actual"]["d"] for d in (5.0, 8.0)] == pytest.approx([5.0, 8.0])

    # the matching cylinder is reported even when one of another diameter has its axis closer to `at`
    drills = cq.Compound.makeCompound(
        [
            cq.Solid.makeCylinder(2.5, 10, cq.Vector(10.008, 5, -5)),  # Ø5 through, axis 0.008 off: the hole
            cq.Solid.makeCylinder(4.0, 3, cq.Vector(10.001, 5, 2)),  # Ø8 from the top, axis 0.001 off
        ]
    )
    entry = hole_entry(cq.Workplane().box(*PLATE).cut(cq.Workplane(obj=drills)))
    assert entry["ok"]
    assert entry["actual"]["d"] == pytest.approx(5.0)


def test_hole_wrong_position_fails():
    wp = plate_with_hole()
    off = hole_entry(wp, at=(10.5, 5.0, 0.0))
    assert off["ok"] is False
    # the nearest cylinder is the real hole, 0.5 mm away
    assert off["actual"] == {"d": pytest.approx(5.0), "dist": pytest.approx(0.5), "concave": True}
    assert hole_entry(wp, at=(10.005, 5.0, 0.0))["ok"]
    assert not hole_entry(wp, at=(10.02, 5.0, 0.0))["ok"]
    # right place and diameter, wrong direction: |cos| >= 0.9999 allows a tilt of 0.014 per unit, not 0.015
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
    # a convex cylinder of the right size and place is no hole: a Ø5 pin on the top face...
    pin = cq.Workplane().box(*PLATE).faces(">Z").workplane().center(10, 5).circle(2.5).extrude(4)
    pin_entry = hole_entry(pin)
    assert pin_entry["ok"] is False
    assert pin_entry["actual"] == {"d": pytest.approx(5.0), "dist": pytest.approx(0.0, abs=1e-9), "concave": False}
    # ...and an R2.5 corner round, seen from its arc centre
    rounded = cq.Workplane().box(*PLATE).edges("|Z and >X and >Y").fillet(2.5)
    round_entry = hole_entry(rounded, at=(17.5, 12.5, 0.0))
    assert (round_entry["ok"], round_entry["actual"]["concave"]) == (False, False)
    # a part without any cylindrical face has no nearest cylinder
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
        "ok": True,
    }
    assert (tmp_path / "plate.step").stat().st_size > 0
    assert step_roundtrip(plate, str(tmp_path / "plate.stp"))["ok"]  # the file extension does not matter

    # the re-imported volume decides: 3e-6 off breaks the 1e-6 limit (too large or too small), 3e-7 does not
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
    above_top = (0, 0, 15.4)  # 0.4 mm over the top face, inside the default 0.5 mm reach
    marks = [{"id": "rv1", "near": [above_top]}]
    box = cq.Workplane(obj=cq.Compound.makeCompound(cq.Workplane().box(10, 20, 30).vals()))  # as build returns
    result = step_roundtrip(box, str(path), marks=marks)
    assert result["marks"] == [{"id": "rv1", "faces": 1}]
    assert result["ok"] is True
    text = path.read_text()
    assert text.count("COLOUR_RGB") >= 2  # steel body and the orange face
    assert "ADVANCED_FACE('rv1'" in text
    back = cq.importers.importStep(str(path)).val()
    assert back.isValid()
    assert len(back.Solids()) == 1


def test_mark_without_face_not_ok(tmp_path):
    marks = [
        {"id": "far", "near": [(55, 0, 0)]},  # 50 mm from the nearest face
        {"id": "just_off", "near": [(0, 0, 15.6)]},  # 0.6 mm over the top face, outside the 0.5 mm reach
    ]
    result = step_roundtrip(cq.Workplane().box(10, 20, 30), str(tmp_path / "box.step"), marks=marks)
    assert result["marks"] == [{"id": "far", "faces": 0}, {"id": "just_off", "faces": 0}]
    assert (result["reimport_valid"], result["solids"]) == (True, 1)  # only the marks are to blame
    assert result["ok"] is False


def test_metrics_cyl_diameters():
    wp = cq.Workplane().box(*PLATE).faces(">Z").workplane()
    for x, y, d in [(-10, 0, 5), (-10, 8, 5.004), (10, 0, 8)]:  # 5.004 rounds to the same 0.01 as 5
        wp = wp.pushPoints([(x, y)]).hole(d)
    result = metrics(wp.val())
    assert result["cyl_diameters"] == [5.0, 8.0]
    assert result["bbox_sorted"] == pytest.approx([10, 30, 40])
    removed = math.pi * (2.5**2 + 2.502**2 + 4**2) * 10
    assert result["volume"] == pytest.approx(40 * 30 * 10 - removed)

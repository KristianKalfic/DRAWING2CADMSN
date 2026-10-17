"""Operation models and the CadQuery build. Expected values are worked out by hand."""
import math

import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder
from pydantic import TypeAdapter

from d2c.ops import BuildError, ExtrudeOp, HolesOp, Op, RectProfile, SketchPlane, build, get_path, set_path

OPS = TypeAdapter(list[Op])
REL = 1e-6


def rect(w, h, center=(0, 0), **kw):
    return {"type": "rect", "center": center, "w": w, "h": h, **kw}


def extrude(profile, distance, plane="XY", offset=0.0, **kw):
    return {"op": "extrude", "profile": profile, "plane": {"name": plane, "offset": offset},
            "distance": distance, **kw}


def holes(offset, positions, d, plane="XY", **kw):
    return {"op": "holes", "plane": {"name": plane, "offset": offset}, "positions": positions, "d": d, **kw}


def edge_finish(kind, size, near, **kw):
    return {"op": "edge_finish", "kind": kind, "size": size, "near": near, **kw}


def block(w=20, h=20, t=10):
    """Box w x h x t standing on XY, centred on the Z axis."""
    return extrude(rect(w, h), t)


def run(*ops):
    """Parse plain dicts through the Op union, as the worker does, then build."""
    wp, notes = build(OPS.validate_python(list(ops)))
    return wp.val(), notes


def note(notes, op_index):
    return next(n for n in notes if n["op_index"] == op_index)


def cylinders(shape):
    """(axis point, axis direction, radius) of every cylindrical face."""
    found = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            cyl = surface.Cylinder()
            p, d = cyl.Axis().Location(), cyl.Axis().Direction()
            found.append(((p.X(), p.Y(), p.Z()), (d.X(), d.Y(), d.Z()), cyl.Radius()))
    return found


def test_extrude_xy_positive_z():
    shape, _ = run(extrude(rect(10, 20), 5))
    bb = shape.BoundingBox()
    assert (bb.zmin, bb.zmax) == pytest.approx((0, 5))
    assert shape.Volume() == pytest.approx(1000, rel=REL)
    # a negative distance runs against the normal, both is symmetric and distance stays the total
    bb = run(extrude(rect(10, 20), -5))[0].BoundingBox()
    assert (bb.zmin, bb.zmax) == pytest.approx((-5, 0))
    bb = run(extrude(rect(10, 20), 5, both=True))[0].BoundingBox()
    assert (bb.zmin, bb.zmax) == pytest.approx((-2.5, 2.5))
    # a profile centre is a local (u, v) offset
    bb = run(extrude(rect(10, 20, center=(3, 4)), 5))[0].BoundingBox()
    assert (bb.xmin, bb.xmax, bb.ymin, bb.ymax) == pytest.approx((-2, 8, -6, 14))
    bb = run(extrude({"type": "circle", "center": (3, 4), "d": 10}, 5))[0].BoundingBox()
    assert (bb.xmin, bb.xmax, bb.ymin, bb.ymax) == pytest.approx((-2, 8, -1, 9))


def test_extrude_xz_goes_negative_y():
    bb = run(extrude(rect(10, 20), 5, plane="XZ"))[0].BoundingBox()
    assert (bb.ymin, bb.ymax) == pytest.approx((-5, 0))
    assert (bb.xmin, bb.xmax, bb.zmin, bb.zmax) == pytest.approx((-5, 5, -10, 10))  # u = X, v = Z
    bb = run(extrude(rect(10, 20), 5, plane="YZ"))[0].BoundingBox()
    assert (bb.xmin, bb.xmax) == pytest.approx((0, 5))
    assert (bb.ymin, bb.ymax, bb.zmin, bb.zmax) == pytest.approx((-5, 5, -10, 10))  # u = Y, v = Z


def test_extrude_offset_plane():
    bb = run(extrude(rect(10, 20), 5, offset=19))[0].BoundingBox()
    assert (bb.zmin, bb.zmax) == pytest.approx((19, 24))
    bb = run(extrude(rect(10, 20), 5, plane="XZ", offset=3))[0].BoundingBox()  # offset runs along the normal -Y
    assert (bb.ymin, bb.ymax) == pytest.approx((-8, -3))


def test_revolve_cylinder_volume():
    shape, _ = run({"op": "revolve", "points": [(0, 0), (10, 0), (10, 5), (0, 5)]})
    assert shape.Volume() == pytest.approx(math.pi * 100 * 5, rel=REL)
    bb = shape.BoundingBox()
    assert (bb.xmin, bb.xmax, bb.zmin, bb.zmax) == pytest.approx((-10, 10, 0, 5))
    quarter, _ = run({"op": "revolve", "points": [(0, 0), (10, 0), (10, 5), (0, 5)], "angle": 90})
    assert quarter.Volume() == pytest.approx(math.pi * 100 * 5 / 4, rel=REL)


def test_revolve_negative_r_rejected():
    with pytest.raises(ValueError, match=">= 0"):
        OPS.validate_python([{"op": "revolve", "points": [(-1, 0), (10, 0), (10, 5)]}])
    lines = [{"kind": "line", "to": (-2, 5)}, {"kind": "line", "to": (0, 5)}]
    path = {"type": "path", "start": (0, 0), "segments": lines}
    with pytest.raises(ValueError, match=">= 0"):
        OPS.validate_python([{"op": "revolve", "profile": path}])
    with pytest.raises(ValueError, match="exactly one"):
        OPS.validate_python([{"op": "revolve"}])


def test_holes_through():
    shape, notes = run(block(), holes(10, [(0, 0)], 5))
    assert shape.Volume() == pytest.approx(4000 - math.pi * 6.25 * 10, rel=REL)
    assert note(notes, 1) == {"op_index": 1, "drilled_d": 5, "holes": 1}
    # flip drills along the normal: from the bottom face upwards
    shape, _ = run(block(), holes(0, [(0, 0)], 5, flip=True))
    assert shape.Volume() == pytest.approx(4000 - math.pi * 6.25 * 10, rel=REL)


def test_holes_blind_depth():
    shape, _ = run(block(), holes(10, [(-5, 0), (5, 0)], 5, depth=4))
    assert shape.Volume() == pytest.approx(4000 - 2 * math.pi * 6.25 * 4, rel=REL)
    assert [round(p[0], 6) for p, _, _ in sorted(cylinders(shape))] == [-5, 5]


def test_holes_tapped_m5_uses_4_2():
    shape, notes = run(block(), holes(10, [(0, 0)], 5, kind="tapped", thread="M5"))
    assert note(notes, 1)["drilled_d"] == 4.2
    assert [r for _, _, r in cylinders(shape)] == pytest.approx([2.1])
    assert shape.Volume() == pytest.approx(4000 - math.pi * 2.1 ** 2 * 10, rel=REL)


def test_holes_tapped_wrong_nominal_rejected():
    with pytest.raises(ValueError, match="nominal"):
        OPS.validate_python([holes(10, [(0, 0)], 6, kind="tapped", thread="M5")])
    with pytest.raises(ValueError, match="thread"):
        OPS.validate_python([holes(10, [(0, 0)], 5, kind="tapped")])
    with pytest.raises(ValueError, match="thread"):
        OPS.validate_python([holes(10, [(0, 0)], 5, kind="tapped", thread="5mm")])
    with pytest.raises(ValueError, match="thread"):
        OPS.validate_python([holes(10, [(0, 0)], 5, thread="M5")])  # kind is plain


def test_holes_polar_positions():
    polar = {"center": (0, 0), "pitch_d": 50, "count": 6}
    shape, notes = run(block(100, 100), {"op": "holes", "plane": {"name": "XY", "offset": 10}, "polar": polar, "d": 5})
    axes = [p for p, _, _ in cylinders(shape)]
    assert len(axes) == 6 and note(notes, 1)["holes"] == 6
    assert [math.hypot(x, y) for x, y, _ in axes] == pytest.approx([25] * 6, abs=1e-6)
    angles = sorted(round(math.degrees(math.atan2(y, x)) % 360, 6) for x, y, _ in axes)
    assert angles == pytest.approx([0, 60, 120, 180, 240, 300], abs=1e-6)
    # off-centre pattern turned by start_angle
    polar = {"center": (10, 5), "pitch_d": 40, "count": 3, "start_angle": 30}
    shape, _ = run(block(100, 100), {"op": "holes", "plane": {"name": "XY", "offset": 10}, "polar": polar, "d": 5})
    got = sorted((x, y) for (x, y, _), _, _ in cylinders(shape))
    want = sorted((10 + 20 * math.cos(math.radians(a)), 5 + 20 * math.sin(math.radians(a))) for a in (30, 150, 270))
    assert [c for xy in got for c in xy] == pytest.approx([c for xy in want for c in xy], abs=1e-6)


def test_holes_cbore_and_csk_volume_less_than_plain():
    plain, _ = run(block(), holes(10, [(0, 0)], 5))
    cbore, _ = run(block(), holes(10, [(0, 0)], 5, kind="cbore", cbore_d=10, cbore_depth=4))
    csk, _ = run(block(), holes(10, [(0, 0)], 5, kind="csk", csk_d=9))
    assert cbore.Volume() < plain.Volume() and csk.Volume() < plain.Volume()
    assert cbore.Volume() == pytest.approx(plain.Volume() - math.pi * (25 - 6.25) * 4, rel=REL)
    # 90 degree countersink: cone height (9 - 5) / 2 = 2, between radius 4.5 and 2.5
    cone = math.pi * 2 / 3 * (4.5 ** 2 + 4.5 * 2.5 + 2.5 ** 2)
    assert csk.Volume() == pytest.approx(4000 - cone - math.pi * 6.25 * 8, rel=REL)
    # 60 degree countersink: cone height 2 / tan(30 deg), the 5 mm hole continues below it
    height = 2 / math.tan(math.radians(30))
    steep, _ = run(block(), holes(10, [(0, 0)], 5, kind="csk", csk_d=9, csk_angle=60))
    cone = math.pi * height / 3 * (4.5 ** 2 + 4.5 * 2.5 + 2.5 ** 2)
    assert steep.Volume() == pytest.approx(4000 - cone - math.pi * 6.25 * (10 - height), rel=REL)
    with pytest.raises(ValueError, match="cbore_d"):
        OPS.validate_python([holes(10, [(0, 0)], 5, kind="cbore")])
    with pytest.raises(ValueError, match="csk_d"):
        OPS.validate_python([holes(10, [(0, 0)], 5, kind="csk", csk_d=4)])
    with pytest.raises(ValueError, match="only go with"):  # a forgotten kind must not give a plain hole silently
        OPS.validate_python([holes(10, [(0, 0)], 5, cbore_d=10, cbore_depth=4)])
    with pytest.raises(ValueError, match="shallower"):
        OPS.validate_python([holes(10, [(0, 0)], 5, depth=3, kind="cbore", cbore_d=10, cbore_depth=4)])


def test_slot_cut_volume():
    t = 5
    slot = {"type": "slot", "center": (0, 0), "center_distance": 80, "width": 12}
    expected = 120 * 120 * t - (80 * 12 + math.pi * 36) * t
    for angle in (0, 90):  # CadQuery measures the overall length, the op takes the arc centre distance
        shape, _ = run(extrude(rect(120, 120), t), extrude({**slot, "angle": angle}, -t, offset=t, mode="cut"))
        assert shape.Volume() == pytest.approx(expected, rel=REL)
    # an off-centre slot keeps its shape: 80 + 12 long, 12 wide
    off = {**slot, "center": (5, -3)}
    bb = run(extrude(rect(120, 120), t), extrude(off, -t, offset=t, mode="cut"))[0].BoundingBox()
    assert (bb.xmin, bb.xmax, bb.ymin, bb.ymax) == pytest.approx((-60, 60, -60, 60))
    bb = run(extrude(off, t))[0].BoundingBox()
    assert (bb.xmin, bb.xmax, bb.ymin, bb.ymax) == pytest.approx((5 - 46, 5 + 46, -3 - 6, -3 + 6))


def test_path_profile_with_arc():
    r, t = 10, 3
    expected = math.pi * r ** 2 / 2 * t
    through_middle = {"kind": "arc3", "via": (0, r), "to": (r, 0)}
    clockwise = {"kind": "radius_arc", "to": (r, 0), "r": r}  # r > 0 turns clockwise: over the top
    for arc in (through_middle, clockwise):
        shape, _ = run(extrude({"type": "path", "start": (-r, 0), "segments": [arc]}, t))
        assert shape.Volume() == pytest.approx(expected, rel=REL)
        assert (shape.BoundingBox().ymin, shape.BoundingBox().ymax) == pytest.approx((0, r))
    counter = {"kind": "radius_arc", "to": (r, 0), "r": -r}  # r < 0 turns counter-clockwise: under the bottom
    shape, _ = run(extrude({"type": "path", "start": (-r, 0), "segments": [counter]}, t))
    assert shape.Volume() == pytest.approx(expected, rel=REL)
    assert (shape.BoundingBox().ymin, shape.BoundingBox().ymax) == pytest.approx((-r, 0))
    explicit = {"type": "path", "start": (-r, 0), "segments": [through_middle, {"kind": "line", "to": (-r, 0)}]}
    assert run(extrude(explicit, t))[0].Volume() == pytest.approx(expected, rel=REL)  # already closed
    too_small = {"kind": "radius_arc", "to": (r, 0), "r": 4}
    with pytest.raises(BuildError, match="op 0"):
        run(extrude({"type": "path", "start": (-r, 0), "segments": [too_small]}, t))


def test_fillet_no_edge_errors():
    with pytest.raises(BuildError, match="no edge within tol of near points"):
        run(block(), edge_finish("fillet", 1, [(100, 100, 100)]))
    with pytest.raises(BuildError, match="no edge within tol of near points"):
        run(block(), edge_finish("fillet", 1, [(0, 0, 5)], tol=0.5))  # inside the box, 10 mm from every edge


def test_fillet_reports_edge_count():
    # midpoint of the top edge along X at the front (y = -10)
    shape, notes = run(block(), edge_finish("fillet", 2, [(0, -10, 10)]))
    assert note(notes, 1) == {"op_index": 1, "edges_selected": 1}
    assert shape.Volume() == pytest.approx(4000 - (1 - math.pi / 4) * 4 * 20, rel=REL)
    shape, notes = run(block(), edge_finish("chamfer", 2, [(0, -10, 10)]))
    assert note(notes, 1)["edges_selected"] == 1
    assert shape.Volume() == pytest.approx(4000 - 0.5 * 4 * 20, rel=REL)
    # a corner touches three edges, two near points on one edge still count it once
    assert note(run(block(), edge_finish("fillet", 1, [(10, -10, 10)]))[1], 1)["edges_selected"] == 3
    assert note(run(block(), edge_finish("fillet", 1, [(0, -10, 10), (1, -10, 10)]))[1], 1)["edges_selected"] == 1
    with pytest.raises(BuildError, match="op 1 .*too large"):
        run(block(), edge_finish("fillet", 15, [(0, -10, 10)]))  # larger than the box


def test_fillet_seam_not_counted():
    # post r10 on a plate: the seam of the post touches the junction circle at (10, 0, 5)
    post = extrude({"type": "circle", "center": (0, 0), "d": 20}, 15, offset=5)
    shape, notes = run(block(60, 60, 5), post, edge_finish("fillet", 1, [(10, 0, 5)]))
    assert note(notes, 2)["edges_selected"] == 1
    assert shape.isValid() and shape.Volume() > run(block(60, 60, 5), post)[0].Volume()  # concave fillet adds material


def test_holes_requires_positions():
    plane = SketchPlane(name="XY")
    with pytest.raises(ValueError, match="positions"):
        HolesOp(op="holes", plane=plane, d=5)
    polar = {"center": (0, 0), "pitch_d": 20, "count": 3}
    with pytest.raises(ValueError, match="exactly one"):
        HolesOp(op="holes", plane=plane, d=5, positions=[(0, 0)], polar=polar)
    assert HolesOp(op="holes", plane=plane, d=5, polar=polar).polar.count == 3


def test_path_fillets_corner():
    t = 4
    square = {"type": "path", "start": (0, 0), "segments": [{"kind": "line", "to": (20, 0)},
                                                             {"kind": "line", "to": (20, 20)},
                                                             {"kind": "line", "to": (0, 20)}],
              "fillets": [{"at": (20, 20), "r": 5}]}
    shape, _ = run(extrude(square, t))
    assert shape.Volume() / t == pytest.approx(400 - (25 - math.pi * 25 / 4), rel=REL)
    # corner_radius on a rect rounds all four corners
    shape, _ = run(extrude(rect(20, 20, center=(10, 10), corner_radius=5), t))
    assert shape.Volume() / t == pytest.approx(400 - 4 * (25 - math.pi * 25 / 4), rel=REL)
    # no corner near `at`, or a radius that does not fit, is a build error
    with pytest.raises(BuildError, match="exactly 1"):
        run(extrude({**square, "fillets": [{"at": (10, 10), "r": 5}]}, t))
    with pytest.raises(BuildError, match="fillet"):
        run(extrude({**square, "fillets": [{"at": (20, 20), "r": 50}]}, t))


def test_cut_first_rejected():
    with pytest.raises(BuildError, match="add"):
        run(extrude(rect(10, 10), 5, mode="cut"))
    with pytest.raises(BuildError, match="needs an existing solid"):
        run(holes(0, [(0, 0)], 3))
    with pytest.raises(BuildError):
        build([])


def test_script_op_runs():
    shape, _ = run(block(), {"op": "script", "code": 'result = result.faces(">Z").workplane().hole(2)'})
    assert shape.Volume() == pytest.approx(4000 - math.pi * 10, rel=REL)
    # a script may start the part, but must leave a Workplane in `result`
    shape, _ = run({"op": "script", "code": "result = result.box(2, 3, 4)"})
    assert shape.Volume() == pytest.approx(24, rel=REL)
    with pytest.raises(BuildError, match="op 1"):
        run(block(), {"op": "script", "code": "result = 5"})
    with pytest.raises(BuildError, match="ImportError"):
        run(block(), {"op": "script", "code": "import os"})  # builtins are restricted, __import__ is gone


def test_custom_plane_inclined_hole():
    plane = {"name": "custom", "origin": (0, 0, 20), "normal": (1, 0, 1)}  # not normalised on purpose
    shape, _ = run(block(40, 40, 20), {"op": "holes", "plane": plane, "positions": [(0, 0)], "d": 6})
    ((_, axis, radius),) = cylinders(shape)
    assert radius == pytest.approx(3)
    assert abs(axis[0] + axis[2]) / math.sqrt(2) >= 0.9999  # |cos| to (1, 0, 1) / sqrt(2)
    assert abs(axis[2]) == pytest.approx(math.sqrt(0.5), abs=1e-4)  # 45 degrees against Z
    assert shape.isValid() and shape.Volume() < 40 * 40 * 20
    # u defaults to global X projected onto the plane, global Y when the normal runs along X
    for normal, u in (((0, 0, 1), (1, 0, 0)), ((0, 1, 0), (1, 0, 0)), ((1, 0, 0), (0, 1, 0)), ((0, 0, -1), (1, 0, 0))):
        got = SketchPlane(name="custom", origin=(0, 0, 0), normal=normal).workplane().plane.xDir.toTuple()
        assert got == pytest.approx(u, abs=1e-9)
    tilted = SketchPlane(name="custom", origin=(0, 0, 0), normal=(0, 0, 1), xdir=(1, 1, 5)).workplane().plane
    assert tilted.xDir.toTuple() == pytest.approx((math.sqrt(0.5), math.sqrt(0.5), 0))  # made perpendicular
    for bad in ({"name": "XY", "origin": (0, 0, 1)}, {"name": "XZ", "xdir": (1, 0, 0)}, {"name": "custom"},
                {"name": "custom", "origin": (0, 0, 0), "normal": (0, 0, 0)},
                {"name": "custom", "origin": (0, 0, 0), "normal": (0, 0, 1), "xdir": (0, 0, 2)}):
        with pytest.raises(ValueError):
            SketchPlane(**bad)


def test_revolve_profile_with_fillet():
    sides = [{"kind": "line", "to": (10, 0)}, {"kind": "line", "to": (10, 20)}, {"kind": "line", "to": (0, 20)}]
    profile = {"type": "path", "start": (0, 0), "segments": sides, "fillets": [{"at": (10, 20), "r": 2}]}
    shape, _ = run({"op": "revolve", "profile": profile})
    # Pappus: 2 pi * first moment of the removed corner (square 2 x 2 minus a quarter disc, centre at r = 8)
    removed = 2 * math.pi * (4 * 9 - math.pi * (8 + 8 / (3 * math.pi)))
    assert shape.Volume() == pytest.approx(math.pi * 100 * 20 - removed, rel=1e-4)
    assert shape.isValid()


def test_thread_minor_and_fine():
    def drilled(thread, d, core):
        _, notes = run(block(), holes(10, [(0, 0)], d, kind="tapped", thread=thread, thread_core=core))
        return note(notes, 1)["drilled_d"]

    assert drilled("M3", 3, "minor") == pytest.approx(2.4588, abs=1e-4)
    assert drilled("M10x1", 10, "tap_drill") == 9.0
    assert drilled("M10x1", 10, "minor") == pytest.approx(8.9175, abs=1e-4)
    assert drilled("M3", 3, "tap_drill") == 2.5
    assert drilled("M2.5", 2.5, "tap_drill") == 2.05
    assert drilled("M10x1.5", 10, "tap_drill") == 8.5  # an explicit coarse pitch is still coarse
    assert drilled("M16", 16, "tap_drill") == 14.0  # not in the table: d - P
    with pytest.raises(ValueError, match="pitch"):
        OPS.validate_python([holes(10, [(0, 0)], 7, kind="tapped", thread="M7")])


def test_refs_valid_paths():
    op = ExtrudeOp(op="extrude", profile=RectProfile(type="rect", center=(0, 0), w=10, h=20),
                   plane=SketchPlane(name="XY", offset=2), distance=5,
                   refs={"profile.w": "d1", "plane.offset": "d2", "distance": "d3"})
    dumped = op.model_dump()
    assert get_path(dumped, "profile.w") == 10 and get_path(dumped, "plane.offset") == 2
    set_path(dumped, "profile.w", 12.5)
    assert get_path(dumped, "profile.w") == 12.5
    assert ExtrudeOp.model_validate(dumped).profile.w == 12.5
    # list indices are numbers, points are tuples in a dump and get rebuilt
    holes_op = OPS.validate_python([holes(10, [(1, 2), (3, 4)], 5, refs={"positions.1.0": "d9", "d": "d3"})])[0]
    dumped = holes_op.model_dump()
    assert get_path(dumped, "positions.1.0") == 3
    for bad in ("positions.-1.0", "positions. 1.0", "positions.1.-0"):
        with pytest.raises(KeyError):
            get_path(dumped, bad)  # list indices are non-negative whole numbers only
        with pytest.raises(KeyError):
            set_path(dumped, bad, 1.0)
    set_path(dumped, "positions.1.0", 3.5)
    assert dumped["positions"] == [(1, 2), (3.5, 4)]
    assert OPS.validate_python([dumped])[0].positions[1] == (3.5, 4)
    with pytest.raises(KeyError):
        set_path(dumped, "positions.2.0", 1.0)
    with pytest.raises(KeyError):
        get_path(dumped, "plane.offset.x")


def test_refs_bad_path_rejected():
    kw = {"op": "extrude", "profile": RectProfile(type="rect", center=(0, 0), w=10, h=20),
          "plane": SketchPlane(name="XY"), "distance": 5}
    with pytest.raises(ValueError, match=r"profile\.q"):
        ExtrudeOp(**kw, refs={"profile.q": "d1"})
    with pytest.raises(ValueError, match="mode"):
        ExtrudeOp(**kw, refs={"mode": "d1"})  # a string, not a number
    with pytest.raises(ValueError, match="both"):
        ExtrudeOp(**kw, refs={"both": "d1"})  # a bool is not a number
    with pytest.raises(ValueError, match=r"profile\.center\.2"):
        ExtrudeOp(**kw, refs={"profile.center.2": "d1"})
    with pytest.raises(ValueError, match="distanse"):  # extra="forbid" also catches misspelt fields
        ExtrudeOp(**kw, distanse=5)


def test_extrude_taper_volume():
    shape, notes = run(extrude({"type": "circle", "center": (0, 0), "d": 90}, 30, taper=2))
    assert shape.Volume() == pytest.approx(186443.1, abs=0.5)  # frustum, r 45 -> 43.952
    assert note(notes, 0) == {"op_index": 0, "taper": 2}
    assert run(block())[1] == []


def test_extrude_taper_collapse_rejected():
    circle = {"type": "circle", "center": (0, 0), "d": 40}
    with pytest.raises(BuildError, match="taper too large"):
        run(extrude(circle, 500, taper=20))
    with pytest.raises(BuildError, match="taper too large"):
        run(block(), extrude(rect(10, 10), -10, offset=10, mode="cut", taper=30))


def test_extrude_tiny_taper_rejected():
    with pytest.raises(ValueError, match="0.001"):
        ExtrudeOp(op="extrude", profile=rect(10, 10), plane={"name": "XY"}, distance=5, taper=0.0001)


def test_extrude_taper_cut_valid():
    plain, _ = run(block(100, 100, 20))
    cylindrical = plain.Volume() - math.pi * 400 * 10
    pocket = extrude({"type": "circle", "center": (0, 0), "d": 40}, -10, offset=20, mode="cut", taper=3)
    shape, _ = run(block(100, 100, 20), pocket)
    assert shape.isValid() and cylindrical < shape.Volume() < plain.Volume()


def test_extrude_intersect():
    circle = {"type": "circle", "center": (0, 25), "d": 40}
    shape, _ = run(block(50, 50, 50), extrude(circle, 50, plane="XZ", both=True, mode="intersect"))
    assert shape.isValid() and shape.Volume() == pytest.approx(math.pi * 400 * 50, abs=0.5)


def test_intersect_first_op_rejected():
    with pytest.raises(BuildError, match="add"):
        run(extrude(rect(10, 10), 5, mode="intersect"))


def circle_hull(circles, height=10, plane="XY"):
    return extrude({"type": "hull", "circles": circles}, height, plane=plane)


@pytest.mark.parametrize("circles, size", [
    ([(0, 0, 45), (-75, 0, 15), (75, 0, 15)], (180, 90)),
    ([(0, 0, 45), (0, -75, 15), (0, 75, 15)], (90, 180)),
])
def test_hull_collinear_u_and_v(circles, size):
    shape, notes = run(circle_hull(circles))
    bb = shape.BoundingBox()
    assert shape.isValid() and shape.Volume() == pytest.approx(104369.6, abs=0.5)
    assert (bb.xlen, bb.ylen, bb.zlen) == pytest.approx((*size, 10))
    assert set(note(notes, 0)["hull_circles"]) == {0, 1, 2}


def test_hull_three_equal_circles():
    shape, _ = run(circle_hull([(-50, 0, 10), (0, 0, 10), (50, 0, 10)]))
    bb = shape.BoundingBox()
    assert shape.isValid() and (bb.xlen, bb.ylen) == pytest.approx((120, 20))
    assert shape.Volume() == pytest.approx(10 * (100 * 20 + math.pi * 100), abs=0.5)
    # the middle circle may come first in the list, the result is the same
    shape, notes = run(circle_hull([(0, 0, 10), (-50, 0, 10), (50, 0, 10)]))
    assert shape.isValid() and shape.Volume() == pytest.approx(10 * (100 * 20 + math.pi * 100), abs=0.5)
    assert note(notes, 0)["hull_circles"] == [2, 1]


def test_hull_contained_circle():
    shape, notes = run(circle_hull([(0, 0, 30), (10, 0, 5)]))
    disc, _ = run(extrude({"type": "circle", "center": (0, 0), "d": 60}, 10))
    assert shape.Volume() == pytest.approx(disc.Volume(), rel=REL)
    assert note(notes, 0)["hull_circles"] == [0]


def test_hull_rejects_bad_circles():
    with pytest.raises(ValueError):
        OPS.validate_python([circle_hull([(0, 0, 5)])])
    with pytest.raises(ValueError, match="radius"):
        OPS.validate_python([circle_hull([(0, 0, 5), (10, 0, 0)])])


def test_cut_without_effect_warns():
    warning = "removed no material: check plane, offset and direction"
    far = extrude({"type": "circle", "center": (0, 0), "d": 4}, -3, offset=50, mode="cut")  # floats above the part
    assert note(run(block(), far)[1], 1) == {"op_index": 1, "warning": warning}
    swallow = extrude(rect(40, 40), 20, mode="intersect")  # the part lies fully inside
    assert note(run(block(), swallow)[1], 1) == {"op_index": 1, "warning": warning}
    away = holes(10, [(0, 0)], 3, flip=True)  # starts at the top face and drills upwards
    assert note(run(block(), away)[1], 1)["warning"] == warning
    assert "warning" not in note(run(block(), holes(10, [(0, 0)], 3))[1], 1)


def test_through_hole_from_far_plane():
    shape, _ = run(block(), holes(100, [(0, 0)], 5))  # the plane is 90 mm above the part
    assert shape.Volume() == pytest.approx(4000 - math.pi * 6.25 * 10, rel=REL)


def test_revolve_profile_crossing_axis_rejected():
    def bulge(r):
        return {"type": "path", "start": (0, 0), "segments": [{"kind": "radius_arc", "to": (0, 10), "r": r}]}

    assert run({"op": "revolve", "profile": bulge(-6)})[0].isValid()  # bulges to +r
    with pytest.raises(BuildError, match="crosses the revolve axis"):
        run({"op": "revolve", "profile": bulge(6)})

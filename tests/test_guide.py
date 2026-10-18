"""Kompakte Berichte und Folgeschritte (d2c.server.guide), rein auf Wörterbüchern ohne CadQuery."""

from helpers import demo_ops
from helpers import demo_spec as demo_dict

from d2c.server import guide
from d2c.spec import PartSpec

DEMO_OPS = demo_ops()


def demo_spec() -> PartSpec:
    return PartSpec.model_validate(demo_dict())


def hole_check(d, at, axis, ok, dim_ids):
    return {"check": "hole", "expected": {"d": d, "at": at, "axis": axis}, "actual": None, "ok": ok, "dim_ids": dim_ids}


def report(dims, bbox=(-40, -25, 0, 40, 25, 25), cyl_d=(10, 16, 30), **more):
    solid = {"valid": True, "solids": 1, "free_edges": 0, "volume": 1234.5678, "bbox": list(bbox), "ok": True}
    if cyl_d is not None:
        solid["cyl_d"] = list(cyl_d)
    return {
        "ok": all(d["ok"] for d in dims),
        "error": None,
        "solid": solid,
        "dims": dims,
        "build_notes": [],
        "roundtrip": {"ok": True},
        "op_index": 1,
    } | more


def bbox_checks(x=80, y=50, z=25, got=(80, 50, 25)):
    return [
        {"check": f"bbox_{a}", "expected": w, "actual": g, "ok": abs(w - g) < 0.01, "dim_ids": []} for a, w, g in zip("xyz", (x, y, z), got)
    ]


SIX_HOLES = [
    hole_check(16, [0, 0, 0], [0, 0, 1], False, ["d16"]),
    *[hole_check(6.6, [x, y, 0], [0, 0, 1], False, ["d66"]) for x, y in ((30, 15), (-30, 15), (30, -15), (-30, -15))],
]


def test_compact_lists_failures_as_short_lines():
    bad = report(bbox_checks(got=(80, 50, 10)) + SIX_HOLES, cyl_d=(10, 30))
    out = guide.compact(bad, demo_spec(), DEMO_OPS[:2], "demo_bracket")
    assert out["ok"] is False and out["error"] is None and out["op_index"] == 1
    assert out["volume"] == 1234.57 and out["bbox"] == [-40, -25, 0, 40, 25, 25]
    assert out["passed"] == 2 and len(out["failed"]) == 6
    assert out["failed"][0] == "bbox_z: expected 25, actual 10"
    assert out["failed"][1].startswith("Hole Ø16 at [0, 0, 0]")
    assert not {"dims", "solid", "roundtrip"} & set(out)


def test_compact_error_and_notes_and_coerced():
    failed = {
        "ok": False,
        "error": "BuildError: op 1 (holes): bad",
        "solid": None,
        "dims": [],
        "build_notes": [],
        "roundtrip": None,
        "op_index": 1,
    }
    out = guide.compact(failed, demo_spec(), DEMO_OPS[:1], "demo_bracket")
    assert out["error"].startswith("BuildError") and out["volume"] is None and out["bbox"] is None
    assert out["next"] == ["Not applied, the operation list is unchanged. Correct the arguments of this operation and call the tool again."]
    out = guide.compact(failed, demo_spec(), DEMO_OPS[:2], "demo_bracket")
    assert any('edit_op(part="demo_bracket", index=1' in s for s in out["next"])
    noted = report(bbox_checks(), build_notes=[{"op_index": 1, "warning": "No material removed"}], coerced=["op 1: flip=true"])
    out = guide.compact(noted, demo_spec(), DEMO_OPS, "demo_bracket")
    assert out["notes"] == ["op 1: No material removed"] and out["coerced"] == ["op 1: flip=true"]
    assert any("No material removed" in s for s in out["next"])


def test_next_proposes_add_holes_with_plane_offset_and_refs():
    bad = report(bbox_checks() + SIX_HOLES)
    steps = guide.next_steps(demo_spec(), DEMO_OPS[:2], bad, "demo_bracket")
    calls = [s for s in steps if s.startswith("add_holes(")]
    assert len(calls) == 2 and len(steps) <= 5
    bore = next(s for s in calls if "d=16" in s)
    assert 'plane={"name": "XY", "offset": 25}' in bore and "positions=[[0, 0]]" in bore
    assert 'refs={"d": "d16"}' in bore and "through hole assumed" in bore
    small = next(s for s in calls if "d=6.6" in s)
    assert "[30, 15]" in small and "[-30, -15]" in small and "depth" not in small


def test_next_hole_planes_for_x_and_y_axes():
    dims = bbox_checks() + [hole_check(5, [40, 3, 4], [1, 0, 0], False, ["a"]), hole_check(4, [7, -25, 9], [0, 1, 0], False, ["b"])]
    steps = guide.next_steps(demo_spec(), DEMO_OPS, report(dims), "p")
    x = next(s for s in steps if "d=5" in s)
    y = next(s for s in steps if "d=4" in s)
    assert 'plane={"name": "YZ", "offset": 40}' in x and "positions=[[3, 4]]" in x
    assert 'plane={"name": "XZ", "offset": 25}' in y and "positions=[[7, 9]]" in y  # -ymin


def test_next_bbox_axis_hints():
    dims = bbox_checks(got=(60, 50, 40))
    steps = guide.next_steps(demo_spec(), DEMO_OPS[:1], report(dims), "p")
    assert any("bbox_x" in s and "material missing" in s for s in steps)
    assert any("bbox_z" in s and "too large" in s for s in steps)


def test_next_lists_unreferenced_dimensions_tolerated_first():
    steps = guide.next_steps(demo_spec(), DEMO_OPS, report(bbox_checks()), "p")
    line = next(s for s in steps if s.startswith("Dimensions without refs"))
    assert 'p60 "60"' in line and "half dimension" in line
    spec = demo_spec().model_copy(deep=True)
    spec.dimensions[7].tol_upper, spec.dimensions[7].tol_lower = 0.1, -0.1  # p30
    line = next(s for s in guide.next_steps(spec, DEMO_OPS, report(bbox_checks()), "p") if s.startswith("Dimensions without refs"))
    assert line.index("p30") < line.index("p60") and 'p30 "30" (toleranced)' in line


def test_next_flags_printed_diameter_missing_in_model():
    steps = guide.next_steps(demo_spec(), DEMO_OPS, report(bbox_checks(), cyl_d=(10, 16)), "p")
    assert any("Ø30" in s and "does not occur in the model" in s and "circle profile" in s for s in steps)


def test_next_without_cyl_d_skips_that_rule():
    steps = guide.next_steps(demo_spec(), DEMO_OPS, report(bbox_checks(), cyl_d=None), "p")
    assert not any("does not occur in the model" in s for s in steps)


def test_next_all_passed_points_to_export():
    steps = guide.next_steps(demo_spec(), DEMO_OPS, report(bbox_checks()), "demo_bracket")
    assert 'export_step(part="demo_bracket")' in " ".join(steps)
    assert 'export_step(part="demo_bracket", variant="mid")' in " ".join(steps)


def test_next_invalid_solid():
    broken = report(bbox_checks())
    broken["solid"] |= {"valid": False, "ok": False}
    assert "invalid" in guide.next_steps(demo_spec(), DEMO_OPS, broken, "p")[0]


def test_submit_next_names_base_body_and_bbox():
    text = guide.submit_next(demo_spec())[0]
    assert "extrude" in text and "revolve" in text and "80 x 50 x 25" in text


def test_next_hole_outside_body_points_at_base_body_not_flip():
    dims = bbox_checks() + [hole_check(6.8, [39, 0, 0], [0, 0, 1], False, ["d6"])]
    bad = report(dims, bbox=(-24.5, 0, 0, 24.5, 20, 5), build_notes=[{"op_index": 1, "warning": "No material removed"}])
    steps = guide.next_steps(demo_spec(), DEMO_OPS, bad, "p")
    assert "lies outside the body (x -24.5…24.5, y 0…20, z 0…5)" in steps[0] and "edit_op index 0" in steps[0]
    assert not any(s.startswith("add_holes(") or "No material removed" in s for s in steps)


def test_next_separate_second_body_suggests_replacing_the_base_body():
    bad = report(bbox_checks(got=(100, 100, 25)))
    bad["solid"] |= {"solids": 2, "ok": False}
    steps = guide.next_steps(demo_spec(), DEMO_OPS[:2], bad, "p")
    assert steps[0].startswith("op 1 does not touch the existing body (solids=2)") and "index=0" in steps[0]


def test_next_without_ops_names_the_base_body():
    empty = {"ok": False, "error": None, "solid": None, "dims": [], "build_notes": [], "roundtrip": None, "op_index": 0}
    assert guide.next_steps(demo_spec(), [], empty, "p") == guide.submit_next(demo_spec())


def test_outside_ignores_oblique_axes_and_holes_within_their_radius():
    bbox = [0, 0, 0, 50, 50, 10]
    assert not guide._outside(hole_check(6, [20, 25, -20], [1, 0, 1], False, []), bbox)
    assert not guide._outside(hole_check(6, [52, 25, 0], [0, 0, 1], False, []), bbox)
    assert guide._outside(hole_check(6, [54, 25, 0], [0, 0, 1], False, []), bbox)

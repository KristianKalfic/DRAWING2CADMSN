"""STEP AP242 with semantic PMI, review colors and GLB, written and then read back like a CAD system would."""
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import cadquery as cq
import pytest
from pydantic import TypeAdapter

from d2c.checks import VOLUME_REL_TOL
from d2c.export import pmi_from, read_pmi, write_glb, write_step
from d2c.ops import Op, build
from d2c.spec import PartSpec

AP242 = "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"
STEEL, ORANGE, GREEN = "#99abb0", "#ff8000", "#65f073"  # the colors as read_pmi reports them
OPS = TypeAdapter(list[Op])

# The demo bracket: plate 80 x 50 (corner R5) at z 0..10, boss O30 up to z 25, bore O16, four O6.6 in the plate.
DEMO_OPS = [
    {"op": "extrude", "profile": {"type": "rect", "center": [0, 0], "w": 80, "h": 50, "corner_radius": 5},
     "plane": {"name": "XY"}, "distance": 10},
    {"op": "extrude", "profile": {"type": "circle", "center": [0, 0], "d": 30},
     "plane": {"name": "XY", "offset": 10}, "distance": 15},
    {"op": "holes", "plane": {"name": "XY", "offset": 25}, "positions": [[0, 0]], "d": 16},
    {"op": "holes", "plane": {"name": "XY", "offset": 10}, "d": 6.6,
     "positions": [[30, 15], [-30, 15], [30, -15], [-30, -15]]},
]
HOLES = [{"d": 16, "at": [0, 0, 0], "axis": [0, 0, 1], "dim_ids": ["d16"]}] + [
    {"d": 6.6, "at": [x, y, 0], "axis": [0, 0, 1], "dim_ids": ["d66"]} for x in (30, -30) for y in (15, -15)
]
# plate 30 x 20 x 10, the base of the tests that need a cylinder and nothing else
PLATE = [{"op": "extrude", "profile": {"type": "rect", "center": [0, 0], "w": 30, "h": 20},
          "plane": {"name": "XY"}, "distance": 10}]


def built(ops: list[dict]) -> cq.Shape:
    return build(OPS.validate_python(ops))[0].val()


def drilled(**hole) -> cq.Shape:
    """The plate with a through hole O10 at the center, or `hole` changing it."""
    drill = {"op": "holes", "plane": {"name": "XY", "offset": 10}, "positions": [[0, 0]], "d": 10}
    return built(PLATE + [drill | hole])


def dimension(dim_id: str, kind: str, value: float, text: str, **more) -> dict:
    return {"id": dim_id, "kind": kind, "value": value, "text": text, "source": None, "confidence": 0.95} | more


def demo_spec() -> PartSpec:
    return PartSpec.model_validate({
        "name": "demo_bracket", "drawing": "Demo Teil (A)/demo drawing.pdf", "title": "D2C Demo Bracket",
        "scale": "1:1", "views": [], "features": [],
        "dimensions": [
            dimension("d16", "diameter", 16, "Ø16 H7", fit="H7"),
            dimension("d66", "diameter", 6.6, "4x Ø6,6"),
            dimension("t10", "length", 10, "", assumed=True, confidence=0.6,
                      assumption_basis="plate thickness not dimensioned; measured in front view at scale 1:1"),
        ],
        "expected": {"bbox": [80, 50, 25], "holes": HOLES},
        "review": [{"id": "rv1", "refs": ["t10"], "issue": "missing", "assumption": "plate thickness 10 mm",
                    "reason": "not dimensioned; measured in the front view",
                    "mark_near": [[35, 0, 0], [35, 0, 10]]}],
    })


def report(spec: PartSpec, lost: tuple[int, ...] = ()) -> dict:
    """The dims of a build report in the checks format: bbox entries, then a hole entry each, found unless lost."""
    sizes = [{"check": f"bbox_{axis}", "expected": want, "actual": want, "ok": True, "dim_ids": []}
             for axis, want in zip("xyz", spec.expected.bbox)]
    holes = [{"check": "hole", "expected": h.model_dump(exclude={"dim_ids"}), "actual": {"d": h.d, "dist": 0.0},
              "ok": k not in lost, "dim_ids": h.dim_ids} for k, h in enumerate(spec.expected.holes)]
    return {"dims": sizes + holes}


def marks_of(spec: PartSpec) -> list[dict]:
    return [{"id": r.id, "near": r.mark_near} for r in spec.review]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """The demo bracket exported once: shape, spec, PMI entries, marks, the file and what write_step returned."""
    spec, ops = demo_spec(), OPS.validate_python(DEMO_OPS)
    shape = build(ops)[0].val()
    pmi = pmi_from(spec, ops, shape, report(spec))
    path = str(tmp_path_factory.mktemp("export") / "demo_bracket.step")
    marks = marks_of(spec)
    result = write_step(shape, path, name=spec.name, marks=marks, pmi=pmi, threads=[])
    return SimpleNamespace(spec=spec, ops=ops, shape=shape, pmi=pmi, marks=marks, path=path, result=result,
                           text=Path(path).read_text())


def export(tmp_path, shape: cq.Shape, *pmi: dict, marks=(), threads=()) -> str:
    path = str(tmp_path / "part.step")
    write_step(shape, path, name="part", marks=list(marks), pmi=list(pmi), threads=list(threads))
    return path


def bore_entry(shape: cq.Shape, **changes) -> dict:
    """A diameter entry for the one cylinder of `shape`."""
    face = next(f for f in shape.Faces() if f.geomType() == "CYLINDER")
    entry = {"type": "diameter", "face": face, "value": 10.0, "tol_upper": None, "tol_lower": None, "fit": None}
    return entry | changes


def test_ap242_schema_header(demo):
    assert AP242 in demo.text
    assert "PRODUCT('demo_bracket'" in demo.text
    assert demo.result == {"path": demo.path, "schema": "AP242DIS", "pmi_written": 6, "colored_faces": 2}
    assert read_pmi(demo.path)["schema"] == AP242


def test_write_step_leaves_other_exports_on_ap214(demo, tmp_path):
    other = tmp_path / "box.step"
    cq.exporters.export(cq.Workplane().box(1, 1, 1), str(other))
    assert "AP242" not in other.read_text()


def test_hole_fit_written_as_h7(demo):
    # OCCT 7.9.3 writes the grade of a fit 2 too high; write_step compensates, this pins the result
    assert "DIMENSIONAL_SIZE" in demo.text
    assert "LIMITS_AND_FITS('H','hole','7','')" in demo.text
    bores = [d for d in read_pmi(demo.path)["dimensions"] if d["type"] == "diameter"]
    assert len(bores) == 5
    assert next(d for d in bores if d["fit"] == "H7") == {
        "type": "diameter", "value": 16.0, "tol_upper": None, "tol_lower": None, "fit": "H7",
        "descriptions": [], "faces": ["cylinder d=16 at (0, 0, 12.5)"],
    }
    assert sorted(d["value"] for d in bores) == [6.6] * 4 + [16.0]


@pytest.mark.parametrize("fit, written", [
    ("H2", "'H','hole','2'"), ("H7", "'H','hole','7'"), ("H11", "'H','hole','11'"), ("H18", "'H','hole','18'"),
    ("g6", "'g','shaft','6'"), ("JS9", "'JS','hole','9'"),
])
def test_fit_round_trip(tmp_path, fit, written):
    shape = drilled()
    path = export(tmp_path, shape, bore_entry(shape, fit=fit))
    assert f"LIMITS_AND_FITS({written},'')" in Path(path).read_text()
    assert [d["fit"] for d in read_pmi(path)["dimensions"]] == [fit]


@pytest.mark.parametrize("fit", ["H1", "H19", "Q7"])  # IT1 cannot be written right, IT19 and the letter Q do not exist
def test_unwritable_fit_is_left_out(tmp_path, fit):
    shape = drilled()
    path = export(tmp_path, shape, bore_entry(shape, fit=fit))
    assert "LIMITS_AND_FITS" not in Path(path).read_text()
    assert [(d["value"], d["fit"]) for d in read_pmi(path)["dimensions"]] == [(10.0, None)]


@pytest.mark.parametrize("upper, lower", [(0.2, 0.0), (0.0, -0.05), (0.1, -0.1)])
def test_plus_minus_tolerance_round_trip(tmp_path, upper, lower):
    shape = drilled()
    path = export(tmp_path, shape, bore_entry(shape, tol_upper=upper, tol_lower=lower))
    assert "PLUS_MINUS_TOLERANCE" in Path(path).read_text()
    (bore,) = read_pmi(path)["dimensions"]
    assert (bore["tol_upper"], bore["tol_lower"], bore["fit"]) == (upper, lower, None)


def test_assumed_thickness_as_semantic_distance(demo):
    assert "DIMENSIONAL_LOCATION" in demo.text
    (thickness,) = [d for d in read_pmi(demo.path)["dimensions"] if d["type"] == "distance"]
    assert thickness["value"] == 10.0
    assert sorted(thickness["faces"]) == ["plane z=0", "plane z=10"]
    assert thickness["descriptions"] == [
        "ANNAHME: plate thickness 10 mm - not dimensioned; measured in the front view - bitte prufen"]


def test_pmi_from_skips_what_it_cannot_place(demo):
    spec, ops, shape = demo.spec, demo.ops, demo.shape

    def entries(spec: PartSpec = spec, lost: tuple[int, ...] = ()) -> list[tuple[str, float]]:
        return [(e["type"], e["value"]) for e in pmi_from(spec, ops, shape, report(spec, lost))]

    def distances(**changes) -> list[tuple[str, float]]:
        review = spec.review[0].model_copy(update=changes)
        return [e for e in entries(spec.model_copy(update={"review": [review]})) if e[0] == "distance"]

    assert sorted(entries()) == [("diameter", 6.6)] * 4 + [("diameter", 16.0), ("distance", 10.0)]
    # a hole the build check did not find gets no diameter; neither does one whose dimension is no diameter
    assert ("diameter", 16.0) not in entries(lost=(0,))
    thread = spec.dimensions[1].model_copy(update={"kind": "thread"})
    dimensions = [spec.dimensions[0], thread, spec.dimensions[2]]
    assert entries(spec.model_copy(update={"dimensions": dimensions})) == [("diameter", 16.0), ("distance", 10.0)]

    assert distances(issue="ambiguous") == []
    assert distances(mark_near=[(35, 0, 0)]) == []  # one plane only
    assert distances(mark_near=[(35, 0, 10), (40, 0, 5)]) == []  # the planes z=10 and x=40 are not parallel
    assert distances(mark_near=[(35, 0, 10), (12, 0, 25)]) == []  # z=10 and z=25 are 15 apart, not the assumed 10


def test_mark_and_thread_colors(demo, tmp_path):
    info = read_pmi(demo.path)
    assert info["colors"] == {STEEL: 1, ORANGE: 2}
    assert info["layers"] == {"ASSUMED_CHECK": 2}
    assert demo.text.count("ADVANCED_FACE('PRUFEN rv1'") == 2

    # an M5 hole is drilled to 4.2: its cylinder turns green
    tapped = drilled(d=5, kind="tapped", thread="M5")
    threads = [{"at": [0, 0, 0], "axis": [0, 0, 1], "d": 4.2}]
    path = str(tmp_path / "tapped.step")
    assert write_step(tapped, path, name="tapped", marks=[], pmi=[], threads=threads)["colored_faces"] == 1
    assert read_pmi(path)["colors"] == {STEEL: 1, GREEN: 1}
    # a thread entry that matches no cylinder colors nothing
    miss = [{"at": [0, 0, 0], "axis": [0, 0, 1], "d": 5.0}, {"at": [1, 0, 0], "axis": [0, 0, 1], "d": 4.2}]
    assert read_pmi(export(tmp_path, tapped, threads=miss))["colors"] == {STEEL: 1}


def test_a_marked_thread_face_stays_orange(tmp_path):
    tapped = drilled(d=5, kind="tapped", thread="M5")
    threads = [{"at": [0, 0, 0], "axis": [0, 0, 1], "d": 4.2}]
    path = str(tmp_path / "tapped.step")
    result = write_step(tapped, path, name="tapped", marks=[{"id": "m", "near": [(2.1, 0, 5)]}], pmi=[], threads=threads)
    assert result["colored_faces"] == 1
    assert read_pmi(path)["colors"] == {STEEL: 1, ORANGE: 1}


def test_two_marks_on_one_face_share_one_color(tmp_path):
    top = [(35, 0, 10)]  # the plate top, for both marks
    marks = [{"id": "rv1", "near": top}, {"id": "rv2", "near": top + [(-35, 0, 10)]}]
    path = export(tmp_path, built(DEMO_OPS), marks=marks)
    assert Path(path).read_text().count("ADVANCED_FACE('PRUFEN rv1 rv2'") == 1
    assert read_pmi(path)["colors"] == {STEEL: 1, ORANGE: 1}


def test_solid_with_a_location_keeps_its_faces(tmp_path):
    box = cq.Workplane().box(10, 10, 10).val()  # CadQuery's box carries a location, which hides its faces from XCAF
    path = export(tmp_path, box, marks=[{"id": "m", "near": [(0, 0, 5)]}])
    assert read_pmi(path)["colors"] == {STEEL: 1, ORANGE: 1}


def test_strings_in_the_file_are_ascii(tmp_path):
    shape = drilled()
    path = export(tmp_path, shape, bore_entry(shape, description="Ø10 Dicke geschätzt"))
    assert Path(path).read_bytes().isascii()
    assert [d["descriptions"] for d in read_pmi(path)["dimensions"]] == [["dia 10 Dicke geschatzt"]]


def test_pmi_face_must_belong_to_the_shape(tmp_path):
    shape = drilled()
    foreign = cq.Workplane().box(5, 5, 5).faces(">Z").val()
    with pytest.raises(ValueError, match="gehört nicht zum Körper"):
        export(tmp_path, shape, bore_entry(shape, face=foreign))


def test_glb_materials(demo, tmp_path):
    glb = tmp_path / "demo.glb"
    mapping = write_glb(demo.shape, str(glb), demo.marks, [])
    blob = glb.read_bytes()
    assert struct.unpack_from("<4sII", blob) == (b"glTF", 2, len(blob))
    json_length = struct.unpack_from("<I", blob, 12)[0]
    materials = json.loads(blob[20:20 + json_length])["materials"]
    assert 2 <= len(materials) <= 3
    assert {m["pbrMetallicRoughness"]["metallicFactor"] for m in materials} == {0.0}
    assert {m["pbrMetallicRoughness"]["roughnessFactor"] for m in materials} == {0.8}
    # glTF drops face names: marks.json says which review ids the orange material stands for
    assert mapping == {ORANGE: ["rv1"]}
    assert json.loads((tmp_path / "marks.json").read_text()) == mapping


def test_glb_leaves_no_mesh_on_the_callers_shape(tmp_path):
    cylinder = cq.Workplane().cylinder(10, 5).val()
    assert write_glb(cylinder, str(tmp_path / "cylinder.glb"), [], []) == {}
    assert cylinder.BoundingBox().xlen == pytest.approx(10)  # a mesh left on the shape would make it 10.07


def test_reimport_valid_and_volume(demo):
    back = cq.Compound.makeCompound(cq.importers.importStep(demo.path).vals())
    assert back.isValid()
    assert len(back.Solids()) == 1
    assert abs(back.Volume() - demo.shape.Volume()) / demo.shape.Volume() <= VOLUME_REL_TOL

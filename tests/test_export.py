"""STEP AP242 mit semantischem PMI, Prüfvermerkfarben und GLB; die Datei wird wie in einem CAD-System geschrieben und anschließend erneut eingelesen."""

import json
import math
import struct
from pathlib import Path
from types import SimpleNamespace

import cadquery as cq
import pytest
from helpers import AP242, GREEN, OPS, ORANGE, STEEL, dimension

from d2c.cad.export import (
    gdt_from,
    mark_faces,
    measure_geometry,
    missing_assumed_pmi,
    pin_marks,
    pmi_from,
    precise_volume,
    read_pmi,
    step_roundtrip,
    unplaced_pmi,
    write_glb,
    write_step,
)
from d2c.cad.ops import build
from d2c.spec import VOLUME_REL_TOL, PartSpec

# Demo-Halterung: Platte 80 × 50 (Ecke R5) von z=0 bis 10, Ansatz Ø30 bis z=25, Bohrung Ø16 und vier Bohrungen Ø6,6 in der Platte.
DEMO_OPS = [
    {
        "op": "extrude",
        "profile": {"type": "rect", "center": [0, 0], "w": 80, "h": 50, "corner_radius": 5},
        "plane": {"name": "XY"},
        "distance": 10,
    },
    {"op": "extrude", "profile": {"type": "circle", "center": [0, 0], "d": 30}, "plane": {"name": "XY", "offset": 10}, "distance": 15},
    {"op": "holes", "plane": {"name": "XY", "offset": 25}, "positions": [[0, 0]], "d": 16},
    {"op": "holes", "plane": {"name": "XY", "offset": 10}, "d": 6.6, "positions": [[30, 15], [-30, 15], [30, -15], [-30, -15]]},
]
HOLES = [{"d": 16, "at": [0, 0, 0], "axis": [0, 0, 1], "dim_ids": ["d16"]}] + [
    {"d": 6.6, "at": [x, y, 0], "axis": [0, 0, 1], "dim_ids": ["d66"]} for x in (30, -30) for y in (15, -15)
]
# Platte 30 × 20 × 10; Grundlage für Tests, die nur einen Zylinder benötigen
PLATE = [{"op": "extrude", "profile": {"type": "rect", "center": [0, 0], "w": 30, "h": 20}, "plane": {"name": "XY"}, "distance": 10}]


def built(ops: list[dict]) -> cq.Shape:
    return build(OPS.validate_python(ops))[0].val()


def drilled(**hole) -> cq.Shape:
    """Die Platte mit einer durchgehenden Bohrung Ø10 in der Mitte oder einer durch `hole` geänderten Bohrung."""
    drill = {"op": "holes", "plane": {"name": "XY", "offset": 10}, "positions": [[0, 0]], "d": 10}
    return built(PLATE + [drill | hole])


def demo_spec() -> PartSpec:
    return PartSpec.model_validate(
        {
            "name": "demo_bracket",
            "drawing": "Demo Teil (A)/demo drawing.pdf",
            "title": "D2C Demo-Halterung",
            "scale": "1:1",
            "views": [],
            "features": [],
            "dimensions": [
                dimension("d16", "diameter", 16, "Ø16 H7", fit="H7"),
                dimension("d66", "diameter", 6.6, "4x Ø6,6"),
                dimension(
                    "t10",
                    "length",
                    10,
                    "",
                    assumed=True,
                    confidence=0.6,
                    assumption_basis="Plattendicke nicht bemaßt; in der Vorderansicht bei Maßstab 1:1 gemessen",
                ),
            ],
            "expected": {"bbox": [80, 50, 25], "holes": HOLES},
            "review": [
                {
                    "id": "rv1",
                    "refs": ["t10"],
                    "issue": "missing",
                    "assumption": "Plattendicke 10 mm",
                    "reason": "nicht bemaßt; in der Vorderansicht gemessen",
                    "mark_near": [[35, 0, 0], [35, 0, 10]],
                }
            ],
        }
    )


def report(spec: PartSpec, lost: tuple[int, ...] = ()) -> dict:
    """Die Maßeinträge eines Prüfberichts im Format von `checks`: zuerst die Begrenzungsmaße, danach je eine Bohrung, sofern sie nicht verloren ging."""
    sizes = [
        {"check": f"bbox_{axis}", "expected": want, "actual": want, "ok": True, "dim_ids": []}
        for axis, want in zip("xyz", spec.expected.bbox)
    ]
    holes = [
        {
            "check": "hole",
            "expected": h.model_dump(exclude={"dim_ids"}),
            "actual": {"d": h.d, "dist": 0.0},
            "ok": k not in lost,
            "dim_ids": h.dim_ids,
        }
        for k, h in enumerate(spec.expected.holes)
    ]
    return {"dims": sizes + holes}


def marks_of(spec: PartSpec) -> list[dict]:
    return [{"id": r.id, "near": r.mark_near} for r in spec.review]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """Die einmal exportierte Demo-Halterung: Körper, Spezifikation, PMI-Einträge, Markierungen, Datei und Rückgabewert von `write_step`."""
    spec, ops = demo_spec(), OPS.validate_python(DEMO_OPS)
    shape = build(ops)[0].val()
    pmi = pmi_from(spec, shape, report(spec))
    path = str(tmp_path_factory.mktemp("export") / "demo_bracket.step")
    marks = marks_of(spec)
    result = write_step(shape, path, name=spec.name, marks=marks, pmi=pmi, threads=[])
    return SimpleNamespace(spec=spec, ops=ops, shape=shape, pmi=pmi, marks=marks, path=path, result=result, text=Path(path).read_text())


def export(tmp_path, shape: cq.Shape, *pmi: dict, marks=(), threads=()) -> str:
    path = str(tmp_path / "part.step")
    write_step(shape, path, name="part", marks=list(marks), pmi=list(pmi), threads=list(threads))
    return path


def bore_entry(shape: cq.Shape, **changes) -> dict:
    """Ein Durchmessereintrag für den einzigen Zylinder in `shape`."""
    face = next(f for f in shape.Faces() if f.geomType() == "CYLINDER")
    entry = {"type": "diameter", "face": face, "value": 10.0, "tol_upper": None, "tol_lower": None, "fit": None}
    return entry | changes


def test_ap242_schema_header(demo):
    assert AP242 in demo.text
    assert "PRODUCT('demo_bracket'" in demo.text
    assert demo.result == {"path": demo.path, "schema": "AP242DIS", "pmi_written": 6, "gdt_written": 0, "colored_faces": 2}
    assert read_pmi(demo.path)["schema"] == AP242


def test_write_step_leaves_other_exports_on_ap214(demo, tmp_path):
    other = tmp_path / "box.step"
    cq.exporters.export(cq.Workplane().box(1, 1, 1), str(other))
    assert "AP242" not in other.read_text()


def test_hole_fit_written_as_h7(demo):
    # OCCT 7.9.3 schreibt die Passungsklasse um 2 zu hoch; `write_step` gleicht das aus. Dieser Test sichert das Ergebnis ab.
    assert "DIMENSIONAL_SIZE" in demo.text
    assert "LIMITS_AND_FITS('H','hole','7','')" in demo.text
    bores = [d for d in read_pmi(demo.path)["dimensions"] if d["type"] == "diameter"]
    assert len(bores) == 5
    assert next(d for d in bores if d["fit"] == "H7") == {
        "type": "diameter",
        "value": 16.0,
        "tol_upper": None,
        "tol_lower": None,
        "fit": "H7",
        "descriptions": [],
        "faces": ["cylinder d=16 at (0, 0, 12.5)"],
    }
    assert sorted(d["value"] for d in bores) == [6.6] * 4 + [16.0]


@pytest.mark.parametrize(
    "fit, written",
    [
        ("H2", "'H','hole','2'"),
        ("H7", "'H','hole','7'"),
        ("H11", "'H','hole','11'"),
        ("H18", "'H','hole','18'"),
        ("g6", "'g','shaft','6'"),
        ("JS9", "'JS','hole','9'"),
    ],
)
def test_fit_round_trip(tmp_path, fit, written):
    shape = drilled()
    path = export(tmp_path, shape, bore_entry(shape, fit=fit))
    assert f"LIMITS_AND_FITS({written},'')" in Path(path).read_text()
    assert [d["fit"] for d in read_pmi(path)["dimensions"]] == [fit]


@pytest.mark.parametrize("fit", ["H1", "H19", "Q7"])  # IT1 lässt sich nicht korrekt schreiben; IT19 und der Buchstabe Q existieren nicht.
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
    assert thickness["descriptions"] == ["ASSUMPTION: Plattendicke 10 mm - nicht bemasst; in der Vorderansicht gemessen - please check"]


def test_pmi_from_skips_what_it_cannot_place(demo):
    spec, shape = demo.spec, demo.shape

    def entries(spec: PartSpec = spec, lost: tuple[int, ...] = ()) -> list[tuple[str, float]]:
        return [(e["type"], e["value"]) for e in pmi_from(spec, shape, report(spec, lost))]

    def distances(**changes) -> list[tuple[str, float]]:
        review = spec.review[0].model_copy(update=changes)
        return [e for e in entries(spec.model_copy(update={"review": [review]})) if e[0] == "distance"]

    assert sorted(entries()) == [("diameter", 6.6)] * 4 + [("diameter", 16.0), ("distance", 10.0)]
    # Für eine vom Aufbau-Test nicht gefundene Bohrung wird kein Durchmesser ausgegeben; dasselbe gilt, wenn ihr Maß kein Durchmessermaß ist.
    assert ("diameter", 16.0) not in entries(lost=(0,))
    thread = spec.dimensions[1].model_copy(update={"kind": "thread"})
    dimensions = [spec.dimensions[0], thread, spec.dimensions[2]]
    assert entries(spec.model_copy(update={"dimensions": dimensions})) == [("diameter", 16.0), ("distance", 10.0)]

    ten = [("distance", 10.0)]
    assert distances(issue="ambiguous") == ten  # jeder Prüfeintrag eines angenommenen Maßes zählt, nicht nur "missing"
    assert distances(mark_near=[(35, 0, 0)]) == ten  # eine Ebene genügt: die Gegenebene wird im ganzen Körper gesucht
    assert distances(mark_near=[(35, 0, 10), (40, 0, 5)]) == ten  # z=10 und die Seite x=40: z=10 hat die Gegenebene z=0
    assert distances(mark_near=[(35, 0, 10), (12, 0, 25)]) == ten
    assert distances(mark_near=[(12, 0, 25)]) == []  # die Ebene z=25 hat keine Gegenebene im Abstand 10
    assert distances(mark_near=[]) == []  # ohne Punkt gibt es keine Fläche, die gemeint ist


def test_pmi_from_tolerance_follows_the_build():
    """Eine Bohrung 0,05 über ihrer gezeichneten Größe bekommt ihren Durchmesser nur, wenn der Aufrufer so viel erlaubt, wie bei einem Mitte-Bau."""
    spec = PartSpec.model_validate(
        {
            "name": "plate",
            "drawing": "Demo/plate.pdf",
            "title": "Plate",
            "scale": "1:1",
            "views": [],
            "features": [],
            "dimensions": [dimension("d10", "diameter", 10, "Ø10")],
            "expected": {"bbox": [30, 20, 10], "holes": [{"d": 10, "at": [0, 0, 0], "axis": [0, 0, 1], "dim_ids": ["d10"]}]},
            "review": [],
        }
    )
    shape = drilled(d=10.05)
    assert pmi_from(spec, shape, report(spec)) == []
    assert [e["value"] for e in pmi_from(spec, shape, report(spec), tol=0.1)] == [10]


def test_mark_and_thread_colors(demo, tmp_path):
    info = read_pmi(demo.path)
    assert info["colors"] == {STEEL: 1, ORANGE: 2}
    assert info["layers"] == {"ASSUMED_CHECK": 2}
    assert demo.text.count("ADVANCED_FACE('CHECK rv1'") == 2

    # Eine M5-Bohrung erhält den Durchmesser 4,2; ihr Zylinder wird grün.
    tapped = drilled(d=5, kind="tapped", thread="M5", thread_core="tap_drill")
    threads = [{"at": [0, 0, 0], "axis": [0, 0, 1], "d": 4.2}]
    path = str(tmp_path / "tapped.step")
    assert write_step(tapped, path, name="tapped", marks=[], pmi=[], threads=threads)["colored_faces"] == 1
    assert read_pmi(path)["colors"] == {STEEL: 1, GREEN: 1}
    # Ein Gewindeeintrag, der zu keinem Zylinder passt, färbt nichts ein.
    miss = [{"at": [0, 0, 0], "axis": [0, 0, 1], "d": 5.0}, {"at": [1, 0, 0], "axis": [0, 0, 1], "d": 4.2}]
    assert read_pmi(export(tmp_path, tapped, threads=miss))["colors"] == {STEEL: 1}


def test_a_marked_thread_face_stays_orange(tmp_path):
    tapped = drilled(d=5, kind="tapped", thread="M5", thread_core="tap_drill")
    threads = [{"at": [0, 0, 0], "axis": [0, 0, 1], "d": 4.2}]
    path = str(tmp_path / "tapped.step")
    result = write_step(tapped, path, name="tapped", marks=[{"id": "m", "near": [(2.1, 0, 5)]}], pmi=[], threads=threads)
    assert result["colored_faces"] == 1
    assert read_pmi(path)["colors"] == {STEEL: 1, ORANGE: 1}


def test_two_marks_on_one_face_share_one_color(tmp_path):
    top = [(35, 0, 10)]  # die Oberseite der Platte für beide Markierungen
    marks = [{"id": "rv1", "near": top}, {"id": "rv2", "near": top + [(-35, 0, 10)]}]
    path = export(tmp_path, built(DEMO_OPS), marks=marks)
    assert Path(path).read_text().count("ADVANCED_FACE('CHECK rv1 rv2'") == 1
    assert read_pmi(path)["colors"] == {STEEL: 1, ORANGE: 1}


def test_solid_with_a_location_keeps_its_faces(tmp_path):
    box = cq.Workplane().box(10, 10, 10).val()  # Der Quader von CadQuery hat eine Lageangabe, die seine Flächen für XCAF verdeckt.
    path = export(tmp_path, box, marks=[{"id": "m", "near": [(0, 0, 5)]}])
    assert read_pmi(path)["colors"] == {STEEL: 1, ORANGE: 1}


def test_strings_in_the_file_are_ascii(tmp_path):
    shape = drilled()
    path = export(tmp_path, shape, bore_entry(shape, description="Ø10 Dicke geschätzt"))
    assert Path(path).read_bytes().isascii()
    assert [d["descriptions"] for d in read_pmi(path)["dimensions"]] == [["dia 10 Dicke geschaetzt"]]


def test_pmi_face_must_belong_to_the_shape(tmp_path):
    shape = drilled()
    foreign = cq.Workplane().box(5, 5, 5).faces(">Z").val()
    with pytest.raises(ValueError, match="does not belong to the solid"):
        export(tmp_path, shape, bore_entry(shape, face=foreign))


def test_glb_materials(demo, tmp_path):
    glb = tmp_path / "demo.glb"
    mapping = write_glb(demo.shape, str(glb), demo.marks, [])
    blob = glb.read_bytes()
    assert struct.unpack_from("<4sII", blob) == (b"glTF", 2, len(blob))
    json_length = struct.unpack_from("<I", blob, 12)[0]
    materials = json.loads(blob[20 : 20 + json_length])["materials"]
    assert 2 <= len(materials) <= 3
    assert {m["pbrMetallicRoughness"]["metallicFactor"] for m in materials} == {0.0}
    assert {m["pbrMetallicRoughness"]["roughnessFactor"] for m in materials} == {0.8}
    # glTF verwirft Flächennamen; `marks.json` ordnet dem orangefarbenen Material die Prüfvermerk-IDs zu.
    assert mapping == {ORANGE: ["rv1"]}
    assert json.loads((tmp_path / "marks.json").read_text()) == mapping


def test_glb_leaves_no_mesh_on_the_callers_shape(tmp_path):
    cylinder = cq.Workplane().cylinder(10, 5).val()
    assert write_glb(cylinder, str(tmp_path / "cylinder.glb"), [], []) == {}
    assert cylinder.BoundingBox().xlen == pytest.approx(10)  # Ein am Körper belassenes Netz würde das Maß auf 10,07 vergrößern.


def test_reimport_valid_and_volume(demo):
    back = cq.Compound.makeCompound(cq.importers.importStep(demo.path).vals())
    assert back.isValid()
    assert len(back.Solids()) == 1
    assert abs(back.Volume() - demo.shape.Volume()) / demo.shape.Volume() <= VOLUME_REL_TOL


def test_precise_volume_of_a_cone_cut_cylinder():
    """Zylinder Ø40 x 20 mit einem Kegel ausgedreht: das adaptive Volumen trifft den analytischen Wert auf 1e-9."""
    wp, _ = build(
        OPS.validate_python(
            [
                {"op": "extrude", "distance": 20, "plane": {"name": "XY"}, "profile": {"type": "circle", "center": [0, 0], "d": 40}},
                {"op": "revolve", "points": [[0, 10], [20, 15], [20, 30], [0, 30]], "mode": "cut"},
            ]
        )
    )
    removed = 2 * math.pi * (10 * 20**2 / 2 - 20**3 / 12)  # über r von 0 bis 20: (20 - (10 + r/4)) · 2πr
    expected = math.pi * 20**2 * 20 - removed
    assert precise_volume(wp.val()) == pytest.approx(expected, rel=1e-9)


# Annahme-PMI (F08): anonymisierter Quader 40 x 20 x 10 mit angenommener Breite 20

BLOCK = [{"op": "extrude", "profile": {"type": "rect", "center": [0, 0], "w": 40, "h": 20}, "plane": {"name": "XY"}, "distance": 10}]


def block_spec(*near: tuple[float, float, float], kind: str = "length", value: float = 20, extra: dict | None = None) -> PartSpec:
    """Quader mit angenommenem Maß `value` (ein Prüfeintrag mit den Punkten `near`); extra ersetzt Felder des Prüfeintrags."""
    item = {
        "id": "rv1",
        "refs": ["w"],
        "issue": "missing",
        "assumption": f"Breite {value:g} mm",
        "reason": "nicht bemaßt",
        "mark_near": [list(p) for p in near],
    } | (extra or {})
    return PartSpec.model_validate(
        {
            "name": "block",
            "drawing": "Demo/block.pdf",
            "title": "Block",
            "scale": "1:1",
            "views": [],
            "features": [],
            "dimensions": [dimension("w", kind, value, "", assumed=True, confidence=0.6, assumption_basis="gemessen")],
            "expected": {"bbox": [40, 20, 10]},
            "review": [item],
        }
    )


@pytest.mark.parametrize(
    "point, why",
    [
        ((0, 10, 5), "ein Punkt mitten auf der Seitenfläche y=10"),
        ((20, 10, 5), "ein Punkt auf der Kante x=20 / y=10"),
        ((0, -10, 5), "die andere Seitenfläche"),
    ],
)
def test_assumed_width_with_a_single_point(point, why):
    shape = built(BLOCK)
    spec = block_spec(point)
    (entry,) = pmi_from(spec, shape, {"dims": []})
    assert entry["type"] == "distance" and entry["value"] == 20
    assert sorted(f.Center().y for f in entry["faces"]) == [-10, 10], why
    assert entry["description"].startswith("ASSUMPTION: Breite 20 mm")


def test_assumed_width_marks_exactly_its_two_pmi_faces(tmp_path):
    """Ein Punkt auf der Ecke (20, 10, 10) träfe Oberseite und zwei Seitenflächen; markiert werden nur die beiden bemaßten Seitenflächen."""
    shape, spec = built(BLOCK), block_spec((20, 10, 10))
    marks = [{"id": "rv1", "near": [(20, 10, 10)]}]
    assert len(mark_faces(shape, marks[0])) == 3
    pmi = pmi_from(spec, shape, {"dims": []})
    pinned = pin_marks(marks, pmi, shape)
    assert sorted((f.Center().y, f.geomType()) for f in mark_faces(shape, pinned[0])) == [(-10, "PLANE"), (10, "PLANE")]
    result = step_roundtrip(cq.Workplane(obj=shape), str(tmp_path / "block.step"), pinned, pmi)
    assert result["marks"] == [{"id": "rv1", "faces": 2}] and result["ok"] is True
    info = read_pmi(result["path"])
    assert info["colors"] == {STEEL: 1, ORANGE: 2} and info["layers"] == {"ASSUMED_CHECK": 2}
    assert write_glb(shape.copy(), str(tmp_path / "block.glb"), pinned, []) == {ORANGE: ["rv1"]}


def test_a_mark_without_pmi_keeps_its_points():
    shape = built(BLOCK)
    marks = [{"id": "rv1", "near": [(0, 10, 10)]}, {"id": "rv2", "near": [(0, 0, 10)]}]
    assert pin_marks(marks, [], shape) == marks
    other = block_spec((0, 10, 10), extra={"id": "rv9"})  # das PMI gehört zum Eintrag rv9, nicht zu rv1 oder rv2
    assert pin_marks(marks, pmi_from(other, shape, {"dims": []}), shape) == marks


def test_assumed_width_round_trips_into_the_step_file(tmp_path):
    shape, spec = built(BLOCK), block_spec((20, 10, 5))
    pmi = pmi_from(spec, shape, {"dims": []})
    path = str(tmp_path / "block.step")
    write_step(shape, path, name="block", marks=[], pmi=pmi, threads=[])
    dims = read_pmi(path)["dimensions"]
    assert [(d["type"], d["value"], sorted(d["faces"])) for d in dims] == [("distance", 20.0, ["plane y=-10", "plane y=10"])]
    assert missing_assumed_pmi(spec, dims) == (["w"], [])


def test_assumed_width_without_a_fitting_plane_is_missing(tmp_path):
    shape, spec = built(BLOCK), block_spec((20, 0, 5), value=25)  # die Seite x=20 hat nur die Gegenebene bei 40
    assert pmi_from(spec, shape, {"dims": []}) == []
    assert missing_assumed_pmi(spec, []) == (["w"], ["w"])


def test_a_mark_off_the_surface_takes_the_nearest_face():
    shape, spec = built(BLOCK), block_spec((0, 12, 5))  # 2 mm neben der Fläche y=10
    (entry,) = pmi_from(spec, shape, {"dims": []})
    assert sorted(f.Center().y for f in entry["faces"]) == [-10, 10]


@pytest.mark.parametrize("kind, value, expected", [("diameter", 10, 10.0), ("radius", 5, 10.0)])
def test_assumed_diameter_and_radius_use_the_cylinder_at_the_point(tmp_path, kind, value, expected):
    shape = drilled()  # Platte 30 x 20 x 10 mit Bohrung Ø10 in der Mitte
    spec = block_spec((5, 0, 5), kind=kind, value=value)
    (entry,) = pmi_from(spec, shape, {"dims": []})
    assert (entry["type"], entry["value"]) == ("diameter", expected)
    assert entry["face"].geomType() == "CYLINDER"
    path = str(tmp_path / "part.step")
    write_step(shape, path, name="part", marks=[], pmi=[entry], threads=[])
    dims = read_pmi(path)["dimensions"]
    assert missing_assumed_pmi(spec, dims) == (["w"], [])


def test_assumed_diameter_on_a_hole_adds_its_note_to_the_hole_entry():
    """Ist die angenommene Zylinderfläche schon als Bohrung eingetragen, entsteht kein zweites PMI, sondern die Bohrung trägt den Hinweis."""
    hole = {"d": 10, "at": [0, 0, 0], "axis": [0, 0, 1], "dim_ids": ["w"]}
    spec = block_spec((5, 0, 5), kind="diameter", value=10).model_copy(deep=True)
    spec.expected.holes.append(type(spec.expected).model_validate({"bbox": [30, 20, 10], "holes": [hole]}).holes[0])
    report = {"dims": [{"check": "hole", "expected": hole, "actual": {"d": 10, "dist": 0}, "ok": True, "dim_ids": ["w"]}]}
    entries = pmi_from(spec, drilled(), report)
    assert len(entries) == 1 and entries[0]["description"].startswith("ASSUMPTION")


def test_missing_assumed_pmi_counts_each_entry_once():
    spec = block_spec((0, 10, 5))
    twin = spec.dimensions[0].model_copy(update={"id": "w2"})
    spec = spec.model_copy(update={"dimensions": [spec.dimensions[0], twin]})
    one = [{"type": "distance", "value": 20.0, "descriptions": ["ASSUMPTION: x"], "faces": []}]
    assert missing_assumed_pmi(spec, one) == (["w", "w2"], ["w2"])
    assert missing_assumed_pmi(spec, one * 2) == (["w", "w2"], [])
    assert missing_assumed_pmi(spec, [one[0] | {"descriptions": ["note"]}]) == (
        ["w", "w2"],
        ["w", "w2"],
    )  # ohne ASSUMPTION-Hinweis kein Beleg


def test_missing_assumed_pmi_ignores_kinds_without_pmi():
    spec = block_spec((0, 10, 5), kind="angle", value=45)
    assert missing_assumed_pmi(spec, []) == ([], [])


def test_measure_geometry_lists_diameters_and_plane_distances():
    geometry = measure_geometry(drilled())
    assert geometry["diameters"] == [10.0]
    assert geometry["distances"] == [10.0, 15.0, 20.0, 30.0]  # 10 und 15: Bohrungsachse zu den Plattenkanten y=±10 und x=±15


def test_measure_geometry_lists_hole_positions():
    """Zwei Bohrungen in der Platte: Abstand der Achsen in X und direkt sowie ihr Abstand von den Kanten."""
    drill = {"op": "holes", "plane": {"name": "XY", "offset": 10}, "positions": [[-9, 0], [6, 4]], "d": 4}
    distances = measure_geometry(built(PLATE + [drill]))["distances"]
    assert {15.0, 4.0, round(241**0.5, 6)}.issubset(distances)  # Achsen: dx, dy, direkt
    assert {6.0, 24.0, 9.0, 21.0, 10.0, 14.0}.issubset(distances)  # Achsen zu den Kanten x=±15 und y=±10


# Form- und Lagetoleranzen und Längen-PMI an benannten Flächen: Quader 40 x 20 x 10 mit Stufe und Bohrung, selbst erzeugt

STEPPED = BLOCK + [
    {
        "op": "extrude",
        "profile": {"type": "rect", "center": [10, 0], "w": 20, "h": 20},
        "plane": {"name": "XY", "offset": 10},
        "distance": -4,
        "mode": "cut",
    },
    {"op": "holes", "plane": {"name": "XY", "offset": 10}, "positions": [[-10, 0]], "d": 6},
]


def tolerance_spec(**more) -> PartSpec:
    """Stufenquader: Bezug A auf der Unterseite, Ebenheit der Oberseite, Parallelität der Stufenfläche (z=6) zu A, Stufentiefe 4 +0,1/0
    zwischen Oberseite und Stufe, Lage der Bohrung 30 ±0,05 ab der Seite x=20."""
    return PartSpec.model_validate(
        {
            "name": "stepped",
            "drawing": "Demo/stepped.pdf",
            "title": "Stufe",
            "scale": "1:1",
            "views": [],
            "features": [],
            "dimensions": [
                dimension("t", "length", 4, "4 +0,1 0", tol_upper=0.1, tol_lower=0.0, faces_near=[[-15, 5, 10], [10, 5, 6]]),
                dimension("p", "length", 30, "30 ±0,05", tol_upper=0.05, tol_lower=-0.05, faces_near=[[-10, 3, 5], [20, 0, 5]]),
            ],
            "datums": [{"id": "a", "label": "A", "near": [0, 0, 0]}],
            "geometric_tolerances": [
                {"id": "fl", "kind": "flatness", "value": 0.03, "text": "0.03", "near": [-15, 5, 10], "confidence": 0.9},
                {
                    "id": "pa",
                    "kind": "parallelism",
                    "value": 0.05,
                    "text": "0.05 A",
                    "datums": ["A"],
                    "near": [10, 5, 6],
                    "confidence": 0.9,
                },
            ],
            "expected": {"bbox": [40, 20, 10]},
            "review": [],
        }
        | more
    )


def test_geometric_tolerances_and_datums_round_trip(tmp_path):
    shape, spec = built(STEPPED), tolerance_spec()
    gdt = gdt_from(spec, shape)
    path = str(tmp_path / "stepped.step")
    trip = step_roundtrip(cq.Workplane(obj=shape), path, gdt=gdt)
    assert trip["ok"]
    assert (trip["pmi"]["gdt_written"], trip["pmi"]["gdt_read_back"], trip["pmi"]["gdt_ids"]) == (2, 2, ["fl", "pa"])
    found = read_pmi(path)
    tolerances = sorted((t["type"], t["value"], t["datums"], t["faces"]) for t in found["geometric_tolerances"])
    assert tolerances == [("flatness", 0.03, [], ["plane z=10"]), ("parallelism", 0.05, ["A"], ["plane z=6"])]
    assert found["datums"] == [{"label": "A", "faces": ["plane z=0"]}]


def test_length_pmi_between_named_faces_keeps_its_tolerance(tmp_path):
    shape, spec = built(STEPPED), tolerance_spec()
    entries = pmi_from(spec, shape, {"dims": []})
    assert [(e["dim_id"], e["value"]) for e in entries] == [("t", 4), ("p", 30)]
    path = str(tmp_path / "stepped.step")
    write_step(shape, path, name="stepped", marks=[], pmi=entries, threads=[])
    dims = sorted((d["value"], d["tol_upper"], d["tol_lower"], sorted(d["faces"])) for d in read_pmi(path)["dimensions"])
    assert dims[0] == (4.0, 0.1, 0.0, ["plane z=10", "plane z=6"])
    assert dims[1][:3] == (30.0, 0.05, -0.05) and any(f.startswith("cylinder d=6") for f in dims[1][3])
    assert unplaced_pmi(spec, entries, gdt_from(spec, shape)) == []


def test_unplaceable_pmi_is_named_not_written():
    """Ein Abstand, der nicht zum Maß passt, ein Punkt fern vom Körper und eine Toleranz auf einem fehlenden Bezug ergeben kein PMI."""
    base = tolerance_spec()
    dims = [base.dimensions[0].model_copy(update={"value": 5.0, "text": "5 +0,1 0"}), base.dimensions[1]]
    spec = base.model_copy(update={"dimensions": dims, "datums": [base.datums[0].model_copy(update={"near": (0, 0, -3)})]})
    shape = built(STEPPED)
    entries, gdt = pmi_from(spec, shape, {"dims": []}), gdt_from(spec, shape)
    assert [t["id"] for t in gdt["tolerances"]] == ["fl"] and gdt["datums"] == []
    assert unplaced_pmi(spec, entries, gdt) == ["t", "a", "pa"]


def test_tolerance_value_is_written_in_metres(tmp_path):
    """OCCT schreibt Toleranzwerte mit der Einheit Meter: 0,03 mm muss als 3e-5 m in der Datei stehen, nicht als 0,03 m."""
    shape, spec = built(STEPPED), tolerance_spec()
    path = tmp_path / "stepped.step"
    write_step(shape, str(path), name="stepped", marks=[], pmi=[], threads=[], gdt=gdt_from(spec, shape))
    text = path.read_text()
    assert "LENGTH_MEASURE(3.E-05)" in text and "LENGTH_MEASURE(3.E-02)" not in text

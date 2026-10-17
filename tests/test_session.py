"""Op-list sessions on real geometry: build rules, persistence, export files and the review list."""
import json
import re
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import cadquery as cq
import pytest
from pydantic import TypeAdapter

from d2c.checks import metrics
from d2c.export import read_pmi
from d2c.ops import Op
from d2c.review import render, to_json
from d2c.session import Session, results_root
from d2c.spec import Dimension, PartSpec, ReviewItem

FIXTURES = Path(__file__).parent / "fixtures"
AP242 = "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"
NO_TABLES = "Toleranztabellen nicht gefunden (D2C_TABLES)"
OPS = TypeAdapter(list[Op])
PLATE = {"bbox": [20, 10, 5]}


def dimension(dim_id: str, kind: str, value: float, text: str, **more) -> dict:
    return {"id": dim_id, "kind": kind, "value": value, "text": text, "source": None, "confidence": 0.95} | more


def make_spec(name: str = "plate") -> PartSpec:
    return PartSpec.model_validate({
        "name": name, "drawing": "Demo/plate.pdf", "title": "Plate", "scale": "1:1", "views": [], "features": [],
        "dimensions": [
            dimension("w", "length", 20, "20"),
            dimension("d1", "diameter", 4, "Ø4"),
            dimension("t", "length", 5, "", assumed=True, confidence=0.6, assumption_basis="read from the side view"),
        ],
        "expected": PLATE,
        "review": [{"id": "rv1", "refs": ["t"], "issue": "missing", "assumption": "thickness 5 mm",
                    "reason": "not dimensioned", "mark_near": [[0, 0, 5]]}],
        "general_tolerance": "ISO 2768-m",
    })


def box(w: float = 20, center=[0, 0], mode: str = "add", **more) -> dict:
    return {"op": "extrude", "profile": {"type": "rect", "center": center, "w": w, "h": 10},
            "plane": {"name": "XY"}, "distance": 5, "mode": mode} | more


def hole(d: float, **more) -> dict:
    return {"op": "holes", "plane": {"name": "XY", "offset": 5}, "positions": [[5, 2]], "d": d} | more


def section(markdown: str, title: str) -> str:
    """The text under the `## title` heading, up to the next heading."""
    return markdown.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]


@pytest.fixture
def session() -> Session:
    s = Session()
    s.submit_spec(make_spec())
    return s


def test_apply_appends_on_success(session):
    report = session.apply("plate", box())
    assert (report["ok"], report["error"], report["op_index"]) == (True, None, 0)
    assert session.ops["plate"] == [box()]
    assert session.last["plate"] is report
    assert json.loads((results_root() / "plate" / "ops.json").read_text()) == [box()]


def test_failed_build_not_appended(session):
    report = session.apply("plate", box(mode="cut"))
    assert report["error"] and not report["ok"]
    assert session.ops["plate"] == []
    assert json.loads((results_root() / "plate" / "ops.json").read_text()) == []


def test_invalid_geometry_appended_but_not_ok(session):
    session.apply("plate", box())
    report = session.apply("plate", box(center=[50, 0]))
    assert report["error"] is None and not report["ok"] and report["solid"]["solids"] == 2
    assert len(session.ops["plate"]) == 2


def test_edit_delete_move_op(session):
    session.apply("plate", box())
    session.apply("plate", hole(3))
    session.apply("plate", hole(4))

    assert session.edit_op("plate", 1, hole(2.5))["op_index"] == 1
    assert session.ops["plate"][1]["d"] == 2.5
    assert session.edit_op("plate", 0, box(mode="cut"))["error"]  # a build error leaves the list as it was
    assert session.ops["plate"][0] == box()

    assert session.move_op("plate", 2, 1)["op_index"] == 1
    assert [op["d"] for op in session.ops["plate"][1:]] == [4, 2.5]
    assert session.move_op("plate", 1, 0)["error"]  # a hole before the solid does not build
    assert session.ops["plate"][0] == box()

    assert session.delete_op("plate", 1)["op_index"] == 1
    assert [op["op"] for op in session.ops["plate"]] == ["extrude", "holes"]
    assert session.delete_op("plate", 0)["error"]
    assert len(session.ops["plate"]) == 2
    assert json.loads((results_root() / "plate" / "ops.json").read_text()) == session.ops["plate"]
    with pytest.raises(IndexError):
        session.delete_op("plate", 5)


def test_export_writes_files(session):
    session.apply("plate", box())
    result = session.export("plate")
    assert result["ok"] is True and result["report"]["ok"] is True
    folder = results_root() / "plate"
    assert result["files"] == {
        "step": str(folder / "plate.step"), "report": str(folder / "report.json"),
        "review": str(folder / "review.md"), "review_json": str(folder / "review.json"),
        "ops": str(folder / "ops.json"), "spec": str(folder / "spec.json"),
        "glb": str(folder / "plate.glb"), "marks": str(folder / "marks.json"), "pmi": str(folder / "pmi.json")}
    assert all(Path(p).stat().st_size > 0 for p in result["files"].values())
    assert json.loads((folder / "report.json").read_text())["ok"] is True
    review = json.loads((folder / "review.json").read_text())
    assert review["all_ok"] is True and review["assumptions"][0]["id"] == "rv1"
    assert "Alle Prüfungen bestanden: ja" in (folder / "review.md").read_text()


def test_relative_results_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("D2C_RESULTS", "out")
    s = Session()
    s.submit_spec(make_spec())
    s.apply("plate", box())
    assert s.export("plate")["ok"] is True
    assert (tmp_path / "out" / "plate" / "plate.step").stat().st_size > 0


def test_export_refuses_when_not_ok(session):
    session.apply("plate", box())
    session.apply("plate", box(center=[50, 0]))
    result = session.export("plate")
    assert (result["ok"], result["error"]) == (False, "Prüfung fehlgeschlagen")
    assert result["report"]["ok"] is False
    assert not list((results_root() / "plate").glob("*.step"))


def test_failed_export_removes_older_results(session):
    session.apply("plate", box())
    folder = results_root() / "plate"
    assert session.export("plate")["ok"] is True
    session.apply("plate", box(center=[50, 0]))
    assert session.export("plate")["ok"] is False
    assert not any((folder / name).exists() for name in ("plate.step", "report.json", "review.md", "review.json"))


def test_review_lists_every_assumed_dimension(session):
    session.apply("plate", box())
    markdown = (Path(session.export("plate")["files"]["review"])).read_text()
    assumptions = section(markdown, "Annahmen und offene Punkte")
    assert "| rv1 | t | fehlt | thickness 5 mm | not dimensioned | - | Flächen: 1 |" in assumptions
    assert "Plate" in markdown and "ISO 2768-m" in markdown
    assert "| Außenmaß X |" in markdown
    assert to_json(session.specs["plate"], session.ops["plate"], session.last["plate"])["assumptions"][0]["issue"] == "missing"


def test_umlaut_part_name():
    name = unicodedata.normalize("NFC", "Trägerplatte")
    session = Session()
    session.submit_spec(make_spec(unicodedata.normalize("NFD", name)))
    session.apply(name, box())
    files = session.export(name)["files"]
    assert all(Path(p).exists() for p in files.values())
    assert Path(files["step"]).parent.name == name


def test_unknown_part_error():
    with pytest.raises(KeyError, match="Unbekanntes Bauteil 'ghost'; rufe zuerst `submit_spec` auf"):
        Session().state("ghost")


def test_review_lists_unreferenced_op_values(session):
    session.apply("plate", box())
    session.apply("plate", hole(3))
    markdown = Path(session.export("plate")["files"]["review"]).read_text()
    unreferenced = section(markdown, "Werte ohne Maßbezug")
    assert "| 1 | d | 3 |" in unreferenced  # the hole diameter matches no dimension
    assert "profile.w" not in unreferenced  # 20 equals dimension w by value
    assert "profile.w" not in section(markdown, "Rückverfolgbarkeit")


def test_refs_unknown_dimension_rejected(session):
    session.apply("plate", box())
    with pytest.raises(ValueError, match="Unbekannte Maß-ID 'nope' in `refs`"):
        session.apply("plate", hole(4, refs={"d": "nope"}))
    with pytest.raises(ValueError, match="Unbekannte Maß-ID 'nope' in `refs`"):
        session.edit_op("plate", 0, box(refs={"profile.w": "nope"}))
    assert session.ops["plate"] == [box()]


def test_review_traceability_table(session):
    session.apply("plate", box())
    session.apply("plate", hole(4, refs={"d": "d1"}))
    markdown = Path(session.export("plate")["files"]["review"]).read_text()
    assert "| 1 | d | 4 | d1 | Ø4 |" in section(markdown, "Rückverfolgbarkeit")
    assert "| 1 | d |" not in section(markdown, "Werte ohne Maßbezug")
    data = to_json(session.specs["plate"], session.ops["plate"], session.last["plate"])
    assert data["traceability"] == [{"op_index": 1, "path": "d", "value": 4, "dimension": "d1", "text": "Ø4"}]


def test_reload_from_disk(session):
    session.apply("plate", box())
    session.apply("plate", hole(3))
    fresh = Session()
    state = fresh.state("plate")
    assert state["ops"] == [box(), hole(3)]
    assert state["spec_summary"]["name"] == "plate"
    assert fresh.apply("plate", hole(2))["op_index"] == 2


@pytest.mark.parametrize("name", ["../x", "a/b", "..", "x\n"])
def test_part_name_with_path_rejected(session, name):
    outside = results_root().parent / "x"
    outside.mkdir()
    (outside / "spec.json").write_text(make_spec().model_dump_json(), encoding="utf-8")
    for call in (session.state, lambda n: session.apply(n, box()), session.export):
        with pytest.raises(KeyError, match="Unbekanntes Bauteil"):
            call(name)
    assert sorted(p.name for p in outside.iterdir()) == ["spec.json"]


def test_refs_must_be_an_object(session):
    with pytest.raises(ValueError, match="`refs` muss ein Objekt sein"):
        session.apply("plate", box(refs=["w"]))
    assert session.ops["plate"] == []


def test_unreadable_file_names_part_and_file(session):
    (results_root() / "plate" / "ops.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="plate/ops.json ist nicht lesbar"):
        Session().state("plate")


def test_state_after_apply(session):
    report = session.apply("plate", box())
    state = session.state("plate")
    assert state["ops"] == [box()]
    assert state["last_report"] is report
    assert state["spec_summary"] == {
        "name": "plate", "title": "Plate", "scale": "1:1", "dimensions": 3, "review_items": 1,
        "expected_bbox": (20, 10, 5)}


def test_move_op_index_out_of_range(session):
    session.apply("plate", box())
    session.apply("plate", hole(3))
    for index, new_index in [(0, 99), (-1, 0), (99, 0)]:
        with pytest.raises(IndexError):
            session.move_op("plate", index, new_index)
    assert session.ops["plate"] == [box(), hole(3)]


def test_resubmitted_spec_resets_ops(session):
    session.apply("plate", box())
    session.submit_spec(make_spec())
    assert session.state("plate")["ops"] == []
    assert Session().state("plate")["ops"] == []


def test_review_lists_revolve_radii(session):
    session.apply("plate", box())
    session.apply("plate", {"op": "revolve", "points": [[3, 0], [7, 0], [7, 2], [3, 2]], "mode": "cut"})
    unreferenced = section(Path(session.export("plate")["files"]["review"]).read_text(),
                           "Werte ohne Maßbezug")
    assert "| 1 | points.0.0 | 3 |" in unreferenced and "| 1 | points.1.0 | 7 |" in unreferenced
    assert "points.0.1" not in unreferenced


def plain_ops(*ops: dict) -> list[dict]:
    """Ops as the session keeps them: parsed through the Op union, then dumped."""
    return [op.model_dump() for op in OPS.validate_python(list(ops))]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """The demo bracket exported nominal and on the tolerance mid, once for the tests that read the files."""
    results = tmp_path_factory.mktemp("results")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("D2C_RESULTS", str(results))
        patch.setenv("D2C_TABLES", str(FIXTURES / "tables"))
        session = Session()
        session.submit_spec(PartSpec.model_validate_json((FIXTURES / "demo_spec.json").read_text("utf-8")))
        session.ops["demo_bracket"] = plain_ops(*json.loads((FIXTURES / "demo_ops.json").read_text("utf-8")))
        nominal, mid = session.export("demo_bracket"), session.export("demo_bracket", variant="mid")
    return SimpleNamespace(session=session, nominal=nominal, mid=mid, folder=results / "demo_bracket")


def block_session(width_lower: float) -> Session:
    """A block 20 wide with a hole O4: the width 20 0/<width_lower> and the bore O4 +0.2/0 are bound to the ops."""
    session = Session()
    session.submit_spec(PartSpec.model_validate({
        "name": "block", "drawing": "Demo/block.pdf", "title": "Block", "scale": "1:1", "views": [], "features": [],
        "dimensions": [
            dimension("w", "length", 20, "20", tol_upper=0.0, tol_lower=width_lower),
            dimension("d4", "diameter", 4, "Ø4", tol_upper=0.2, tol_lower=0.0),
        ],
        "expected": {"bbox": [20, 10, 5], "holes": [{"d": 4, "at": [5, 2, 0], "axis": [0, 0, 1], "dim_ids": ["d4"]}]},
        "review": [],
    }))
    session.ops["block"] = plain_ops(box(refs={"profile.w": "w"}), hole(4, refs={"d": "d4"}))
    return session


def test_export_mid_variant(demo):
    assert demo.mid["ok"] is True
    assert demo.mid["files"] == {
        "step": str(demo.folder / "demo_bracket_mid.step"), "report": str(demo.folder / "report_mid.json")}
    report = json.loads((demo.folder / "report_mid.json").read_text())
    assert report == demo.mid["report"] and report["ok"] is True
    d16 = next(c for c in report["compliance"] if c["dim_id"] == "d16")
    assert d16["values"] == [16.009, pytest.approx(16.009)]  # the op value, then the diameter measured in the model
    assert d16["in_tolerance"] is True
    assert all(c["in_tolerance"] is not False for c in report["compliance"])

    # the bore is O16.009 in the mid file and O16 in the nominal one; the op list itself is untouched
    def diameters(name: str) -> list[float]:
        return metrics(cq.importers.importStep(str(demo.folder / name)).val())["cyl_diameters"]

    assert diameters("demo_bracket.step") == [6.6, 10.0, 16.0, 30.0]
    assert diameters("demo_bracket_mid.step") == [6.6, 10.0, 16.01, 30.0]
    assert demo.session.ops["demo_bracket"][2]["d"] == 16
    assert demo.session.last["demo_bracket"] is demo.nominal["report"]

    nominal = json.loads((demo.folder / "report.json").read_text())
    assert next(c for c in nominal["compliance"] if c["dim_id"] == "d16")["values"] == [16, pytest.approx(16.0)]


def test_session_export_ap242_header(demo):
    for name in ("demo_bracket.step", "demo_bracket_mid.step"):
        text = (demo.folder / name).read_text()
        assert AP242 in text
        assert "LIMITS_AND_FITS('H','hole','7','')" in text  # O16 H7
        assert "DIMENSIONAL_LOCATION" in text and "ANNAHME: Plattendicke 10 mm" in text


def test_session_export_writes_glb_and_marks(demo):
    assert (demo.folder / "demo_bracket.glb").read_bytes()[:4] == b"glTF"
    assert json.loads((demo.folder / "marks.json").read_text()) == {"#ff8000": ["rv1"]}
    assert not (demo.folder / "demo_bracket_mid.glb").exists()


def test_roundtrip_pmi_counts(demo):
    trip = demo.nominal["report"]["roundtrip"]
    assert trip["pmi"]["written"] == trip["pmi"]["read_back"] >= 2
    assert trip["pmi"] == {"written": 6, "read_back": 6}  # O16 H7, four O6.6 and the assumed thickness
    assert trip["ok"] is True
    assert demo.mid["report"]["roundtrip"]["pmi"] == {"written": 6, "read_back": 6}
    stored = json.loads((demo.folder / "pmi.json").read_text())
    assert stored == read_pmi(str(demo.folder / "demo_bracket.step"))
    assert stored["schema"] == AP242 and len(stored["dimensions"]) == 6


def test_review_has_tolerance_and_pmi_sections(demo):
    markdown = (demo.folder / "review.md").read_text()
    tolerances = section(markdown, "Toleranzen")
    assert "| Maß | Gedruckter Text | Nennmaß | Mindestmaß | Höchstmaß | Toleranzmitte | Quelle |" in tolerances
    assert "| d16 | Ø16 H7 | 16 | 0 | 0.018 | 16.009 | fit | ja | ja |" in tolerances
    assert "| d80 | 80 | 80 | -0.3 | 0.3 | 80 | general | ja | ja |" in tolerances
    # nothing is bound to the hole spacing: it is not checked, which is not the same as in tolerance
    assert "| p60 | 60 | 60 | -0.3 | 0.3 | 60 | general | nicht geprüft | nicht geprüft |" in tolerances
    pmi = section(markdown, "PMI in der STEP-Datei")
    assert "| Art | Wert | Toleranz | Flächen | Beschreibung |" in pmi
    assert "| diameter | 16 | H7 | cylinder d=16 at (0, 0, 12.5) | - |" in pmi
    assert "| distance | 10 | - |" in pmi and "ANNAHME: Plattendicke 10 mm" in pmi
    assert "| STEP-PMI zurückgelesen | 6 | 6 | ja |" in section(markdown, "Prüfungen")

    review = json.loads((demo.folder / "review.json").read_text())
    rows = {t["dim_id"]: t for t in review["tolerances"]}
    assert rows["d16"] == {
        "dim_id": "d16", "text": "Ø16 H7", "nominal": 16, "lower": 0.0, "upper": 0.018, "mid": 16.009,
        "source": "fit", "in_tolerance_nominal": True, "in_tolerance_mid": True}
    assert (rows["p60"]["in_tolerance_nominal"], rows["p60"]["in_tolerance_mid"]) == (None, None)
    assert [(p["type"], p["value"]) for p in review["pmi"]].count(("diameter", 6.6)) == 4
    assert review["all_ok"] is True


def test_review_writes_fits_and_deviations_of_the_pmi(demo):
    def entry(**more) -> dict:
        return {"type": "diameter", "value": 4.0, "tol_upper": None, "tol_lower": None, "fit": None,
                "descriptions": [], "faces": ["cylinder d=4 at (5, 2, 2.5)"]} | more

    pmi = [entry(fit="H7"), entry(tol_upper=0.2, tol_lower=0.0), entry(tol_upper=0.0, tol_lower=-0.05),
           entry(tol_upper=0.1, tol_lower=-0.1), entry(), entry(type="distance", descriptions=["a", "b"])]
    markdown = render(demo.session.specs["demo_bracket"], demo.session.ops["demo_bracket"],
                      demo.nominal["report"], [], pmi)
    rows = section(markdown, "PMI in der STEP-Datei").splitlines()[2:]
    assert [row.split(" | ")[2] for row in rows] == ["H7", "+0.2/0", "0/-0.05", "+0.1/-0.1", "-", "-"]
    assert rows[-1] == "| distance | 4 | - | cylinder d=4 at (5, 2, 2.5) | a; b |"
    assert section(markdown, "Toleranzen").strip() == "Keine Einträge."


def test_export_without_tables_still_ok(session, monkeypatch, tmp_path):
    monkeypatch.setenv("D2C_TABLES", str(tmp_path / "no tables"))
    session.apply("plate", box())
    result = session.export("plate")
    folder = results_root() / "plate"
    assert result["ok"] is True and result["report"]["compliance"] is None
    assert json.loads((folder / "report.json").read_text())["compliance"] is None
    markdown = (folder / "review.md").read_text()
    assert section(markdown, "Toleranzen").strip() == NO_TABLES
    assert json.loads((folder / "review.json").read_text())["tolerances"] is None
    assert "## PMI in der STEP-Datei" in markdown  # the PMI does not need the tables

    with pytest.raises(ValueError, match=re.escape(NO_TABLES)):
        session.export("plate", variant="mid")
    assert not (folder / "plate_mid.step").exists() and not (folder / "report_mid.json").exists()


def test_failed_export_removes_glb_and_mid_files(session):
    session.apply("plate", box())
    folder = results_root() / "plate"
    assert session.export("plate")["ok"] is True and session.export("plate", variant="mid")["ok"] is True
    assert sorted(p.name for p in folder.iterdir()) == sorted([
        "plate.step", "plate_mid.step", "plate.glb", "marks.json", "pmi.json", "report.json", "report_mid.json",
        "review.md", "review.json", "ops.json", "spec.json"])
    session.apply("plate", box(center=[50, 0]))
    assert session.export("plate")["ok"] is False
    assert sorted(p.name for p in folder.iterdir()) == ["ops.json", "spec.json"]


def test_failed_mid_export_removes_only_the_mid_files():
    """On the mid the block is 19.4 wide: a mark 0.4 mm off its right face at 20 is 0.7 mm off it then, out of reach."""
    session = block_session(width_lower=-1.2)
    folder = results_root() / "block"
    assert session.export("block")["ok"] is True and session.export("block", variant="mid")["ok"] is True
    mark = ReviewItem(id="rv1", refs=[], issue="ambiguous", assumption="side face", reason="unclear",
                      mark_near=[(10.4, 0, 2.5)])
    session.specs["block"] = session.specs["block"].model_copy(update={"review": [mark]})
    result = session.export("block", variant="mid")
    assert (result["ok"], result["error"]) == (False, "Prüfung fehlgeschlagen")
    assert result["report"]["roundtrip"]["marks"] == [{"id": "rv1", "faces": 0}]
    assert all(d["ok"] for d in result["report"]["dims"])  # only the mark is to blame, not the shifted sizes
    assert not (folder / "block_mid.step").exists() and not (folder / "report_mid.json").exists()
    assert (folder / "block.step").is_file() and (folder / "report.json").is_file()


def test_mid_variant_with_wide_band():
    """20 0/-0.2 and O4 +0.2/0 move 0.1 mm each, ten times what a dimension check allows on its own."""
    session = block_session(width_lower=-0.2)
    nominal, mid = session.export("block"), session.export("block", variant="mid")
    assert (nominal["ok"], mid["ok"]) == (True, True)
    assert metrics(cq.importers.importStep(mid["files"]["step"]).val())["bbox_sorted"] == pytest.approx([5, 10, 19.9])
    by_id = {c["dim_id"]: c for c in mid["report"]["compliance"]}
    assert by_id["w"]["values"] == [19.9] and by_id["d4"]["values"] == [4.1, pytest.approx(4.1)]
    assert all(c["in_tolerance"] is True for c in by_id.values())
    assert [(d["value"], d["tol_upper"], d["tol_lower"]) for d in read_pmi(mid["files"]["step"])["dimensions"]] == [
        (4.0, 0.2, 0.0)]  # the bore is found 0.1 mm over its drawn size and keeps its dimension
    assert all(d["ok"] for d in nominal["report"]["dims"])


def test_mid_variant_moves_a_radius_twice_as_far_across():
    """R20 +0.2/0 is R20.1 on the mid: the cone grows 0.2 across, which the checks have to allow."""
    session = Session()
    session.submit_spec(PartSpec.model_validate({
        "name": "disc", "drawing": "Demo/disc.pdf", "title": "Cone", "scale": "1:1", "views": [], "features": [],
        "dimensions": [dimension("r20", "radius", 20, "R20", tol_upper=0.2, tol_lower=0.0)],
        "expected": {"bbox": [40, 40, 10]}, "review": [],
    }))
    session.ops["disc"] = plain_ops({"op": "revolve", "points": [[0, 0], [20, 0], [10, 10], [0, 10]],
                                     "refs": {"points.1.0": "r20"}})
    assert session.export("disc")["ok"] is True
    mid = session.export("disc", variant="mid")
    assert mid["ok"] is True and mid["report"]["compliance"][0]["values"] == [20.1]
    sizes = metrics(cq.importers.importStep(mid["files"]["step"]).val())["bbox_sorted"]
    assert sizes == pytest.approx([10, 40.2, 40.2])


def test_variant_must_be_known(session):
    session.ops["plate"] = [box()]
    with pytest.raises(ValueError, match="variant"):
        session.export("plate", variant="max")
    assert not list((results_root() / "plate").glob("*.step"))


def test_export_refuses_a_tolerance_the_tables_lack(session):
    spec = session.specs["plate"]
    fit = Dimension.model_validate(dimension("d100", "diameter", 100, "Ø100 H7", fit="H7"))  # tables stop at 18
    session.submit_spec(spec.model_copy(update={"dimensions": [*spec.dimensions, fit]}))
    session.ops["plate"] = [box()]
    for variant in ("nominal", "mid"):
        with pytest.raises(ValueError, match="fit H7 has no row for 100"):
            session.export("plate", variant=variant)
    assert sorted(p.name for p in (results_root() / "plate").iterdir()) == ["ops.json", "spec.json"]

"""Op-list sessions on real geometry: build rules, persistence, export files and the review list."""
import json
import unicodedata
from pathlib import Path

import pytest

from d2c.review import to_json
from d2c.session import Session, results_root
from d2c.spec import PartSpec

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
        "ops": str(folder / "ops.json"), "spec": str(folder / "spec.json")}
    assert all(Path(p).stat().st_size > 0 for p in result["files"].values())
    assert json.loads((folder / "report.json").read_text())["ok"] is True
    review = json.loads((folder / "review.json").read_text())
    assert review["all_ok"] is True and review["assumptions"][0]["id"] == "rv1"
    assert "All checks passed: yes" in (folder / "review.md").read_text()


def test_export_refuses_when_not_ok(session):
    session.apply("plate", box())
    session.apply("plate", box(center=[50, 0]))
    result = session.export("plate")
    assert (result["ok"], result["error"]) == (False, "validation failed")
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
    assumptions = section(markdown, "Assumptions and open issues")
    assert "| rv1 | t | missing | thickness 5 mm | not dimensioned | - | 1 face(s) |" in assumptions
    assert "Plate" in markdown and "ISO 2768-m" in markdown


def test_umlaut_part_name():
    name = unicodedata.normalize("NFC", "Trägerplatte")
    session = Session()
    session.submit_spec(make_spec(unicodedata.normalize("NFD", name)))
    session.apply(name, box())
    files = session.export(name)["files"]
    assert all(Path(p).exists() for p in files.values())
    assert Path(files["step"]).parent.name == name


def test_unknown_part_error():
    with pytest.raises(KeyError, match="unknown part 'ghost'; call submit_spec first"):
        Session().state("ghost")


def test_review_lists_unreferenced_op_values(session):
    session.apply("plate", box())
    session.apply("plate", hole(3))
    markdown = Path(session.export("plate")["files"]["review"]).read_text()
    unreferenced = section(markdown, "Values without dimension reference")
    assert "| 1 | d | 3 |" in unreferenced  # the hole diameter matches no dimension
    assert "profile.w" not in unreferenced  # 20 equals dimension w by value
    assert "profile.w" not in section(markdown, "Traceability")


def test_refs_unknown_dimension_rejected(session):
    session.apply("plate", box())
    with pytest.raises(ValueError, match="unknown dimension id 'nope' in refs"):
        session.apply("plate", hole(4, refs={"d": "nope"}))
    with pytest.raises(ValueError, match="unknown dimension id 'nope' in refs"):
        session.edit_op("plate", 0, box(refs={"profile.w": "nope"}))
    assert session.ops["plate"] == [box()]


def test_review_traceability_table(session):
    session.apply("plate", box())
    session.apply("plate", hole(4, refs={"d": "d1"}))
    markdown = Path(session.export("plate")["files"]["review"]).read_text()
    assert "| 1 | d | 4 | d1 | Ø4 |" in section(markdown, "Traceability")
    assert "| 1 | d |" not in section(markdown, "Values without dimension reference")
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
        with pytest.raises(KeyError, match="unknown part"):
            call(name)
    assert sorted(p.name for p in outside.iterdir()) == ["spec.json"]


def test_refs_must_be_an_object(session):
    with pytest.raises(ValueError, match="refs must be an object"):
        session.apply("plate", box(refs=["w"]))
    assert session.ops["plate"] == []


def test_unreadable_file_names_part_and_file(session):
    (results_root() / "plate" / "ops.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="plate/ops.json is unreadable"):
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
                           "Values without dimension reference")
    assert "| 1 | points.0.0 | 3 |" in unreferenced and "| 1 | points.1.0 | 7 |" in unreferenced
    assert "points.0.1" not in unreferenced

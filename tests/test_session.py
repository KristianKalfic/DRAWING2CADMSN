"""Sitzungstests mit echter Geometrie: Aufbauregeln, Speicherung, Exportdateien und Prüfliste."""

import json
import logging
import math
import os
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import cadquery as cq
import pytest
from helpers import AP242, OPS, demo_ops, demo_spec, dimension, plain_ops

from d2c.cad.checks import metrics
from d2c.cad.export import read_pmi
from d2c.config import results_root
from d2c.demo import DATA
from d2c.quality.review import _table, render, to_json
from d2c.session import Session
from d2c.spec import Dimension, PartSpec, ReviewItem

PLATE = {"bbox": [20, 10, 5]}


def make_spec(name: str = "plate") -> PartSpec:
    return PartSpec.model_validate(
        {
            "name": name,
            "drawing": "Demo/plate.pdf",
            "title": "Platte",
            "scale": "1:1",
            "views": [],
            "features": [],
            "dimensions": [
                dimension("w", "length", 20, "20"),
                dimension("d1", "diameter", 4, "Ø4"),
                dimension("t", "length", 5, "", assumed=True, confidence=0.6, assumption_basis="aus der Seitenansicht abgelesen"),
            ],
            "expected": PLATE,
            "review": [
                {
                    "id": "rv1",
                    "refs": ["t"],
                    "issue": "missing",
                    "assumption": "Dicke 5 mm",
                    "reason": "nicht bemaßt",
                    "mark_near": [[0, 0, 5]],
                }
            ],
            "general_tolerance": "ISO 2768-m",
        }
    )


def box(w: float = 20, center: list[float] | None = None, mode: str = "add", **more) -> dict:
    center = [0, 0] if center is None else center
    return {
        "op": "extrude",
        "profile": {"type": "rect", "center": center, "w": w, "h": 10},
        "plane": {"name": "XY"},
        "distance": 5,
        "mode": mode,
    } | more


def hole(d: float, **more) -> dict:
    return {"op": "holes", "plane": {"name": "XY", "offset": 5}, "positions": [[5, 2]], "d": d} | more


def section(markdown: str, title: str) -> str:
    """Der Text unter der Überschrift `## <Titel>` bis zur nächsten Überschrift."""
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


def test_build_logs_its_outcome(session, caplog):
    """Jeder Aufbau meldet sein Ergebnis als Protokollzeile; D2C_LOG=debug zeigt zusätzlich die Worker-Aufrufe."""
    with caplog.at_level(logging.INFO, logger="d2c.session"):
        session.apply("plate", box())
        session.apply("plate", box(mode="cut"))
    lines = [r.getMessage() for r in caplog.records if r.name == "d2c.session"]
    assert any(m.startswith("Build of 'plate' with 1 ops ok after") for m in lines)
    assert any(m.startswith("Build of 'plate' with 2 ops failed") for m in lines)


def test_open_features_are_info_but_broken_bodies_stay_warnings(session, caplog):
    """Fehlende Merkmale sind bis zum letzten Schritt normal (Info); ein ungültiger Körper bleibt Warnung."""
    with caplog.at_level(logging.INFO, logger="d2c.session"):
        assert not session.apply("plate", box(w=10))["ok"]  # Das Teil ist noch kleiner als erwartet.
        session.apply("plate", box(center=[50, 0]))  # Zwei getrennte Körper.
    records = [r for r in caplog.records if r.name == "d2c.session"]
    open_ = [r for r in records if "still open: " in r.getMessage()]
    assert [r.levelno for r in open_] == [logging.INFO]
    assert "Number of solids" not in open_[0].getMessage()
    broken = [r for r in records if r.levelno == logging.WARNING]
    assert len(broken) == 1 and "Number of solids" in broken[0].getMessage()


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
    assert session.edit_op("plate", 0, box(mode="cut"))["error"]  # Ein Aufbaufehler lässt die Liste unverändert.
    assert session.ops["plate"][0] == box()

    assert session.move_op("plate", 2, 1)["op_index"] == 1
    assert [op["d"] for op in session.ops["plate"][1:]] == [4, 2.5]
    moved = session.move_op("plate", 1, 0)  # Eine Bohrung vor dem Grundkörper kann nicht aufgebaut werden.
    # Der Fehler nennt den Index der gespeicherten Liste: die Bohrung steht dort weiter an Stelle 1.
    assert "op 1 (holes)" in moved["error"] and moved["op_index"] == 1
    assert session.ops["plate"][0] == box()

    assert session.delete_op("plate", 1)["op_index"] == 1
    assert [op["op"] for op in session.ops["plate"]] == ["extrude", "holes"]
    deleted = session.delete_op("plate", 0)
    assert "op 1 (holes)" in deleted["error"] and deleted["op_index"] == 1 and "not applied" in deleted["error"]
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
        "step": str(folder / "plate.step"),
        "report": str(folder / "report.json"),
        "review": str(folder / "review.md"),
        "review_json": str(folder / "review.json"),
        "ops": str(folder / "ops.json"),
        "spec": str(folder / "spec.json"),
        "glb": str(folder / "plate.glb"),
        "marks": str(folder / "marks.json"),
        "pmi": str(folder / "pmi.json"),
    }
    assert all(Path(p).stat().st_size > 0 for p in result["files"].values())
    assert json.loads((folder / "report.json").read_text())["ok"] is True
    review = json.loads((folder / "review.json").read_text())
    assert review["all_ok"] is True and review["assumptions"][0]["id"] == "rv1"
    assert "All checks passed: yes" in (folder / "review.md").read_text()


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
    assert (result["ok"], result["error"]) == (False, "Check failed")
    assert result["report"]["ok"] is False
    assert not list((results_root() / "plate").glob("*.step"))


@pytest.mark.parametrize("variant, step", [("nominal", "plate.step"), ("mid", "plate_mid.step")])
def test_interrupted_export_keeps_the_old_step(session, monkeypatch, variant, step):  # noqa: ARG001
    """Das STEP gilt als "fertig" und wird zuletzt ersetzt: Bricht der Export bei der zweiten Datei ab, bleibt das alte STEP stehen."""
    session.apply("plate", box())
    folder = results_root() / "plate"
    assert session.export("plate", variant=variant)["ok"] is True
    old = (folder / step).read_bytes()
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(Path(dst).name)
        if len(calls) == 2:
            raise OSError("simulated abort")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError, match="simulated abort"):
        session.export("plate", variant=variant)
    assert (folder / step).read_bytes() == old
    assert not [p for p in folder.iterdir() if p.name.startswith(".export-")]


@pytest.mark.parametrize("variant, step, report", [("nominal", "plate.step", "report.json"), ("mid", "plate_mid.step", "report_mid.json")])
def test_export_replaces_the_step_last_and_after_its_report(session, monkeypatch, variant, step, report):
    session.apply("plate", box())
    real, calls = os.replace, []
    monkeypatch.setattr(os, "replace", lambda src, dst: (calls.append(Path(dst).name), real(src, dst))[1])
    assert session.export("plate", variant=variant)["ok"] is True
    assert calls[-1] == step and report in calls[:-1]


def test_failed_export_keeps_the_last_good_result(session):
    session.apply("plate", box())
    folder = results_root() / "plate"
    assert session.export("plate")["ok"] is True
    good = {name: (folder / name).read_bytes() for name in ("plate.step", "plate.glb", "report.json", "review.md", "review.json")}
    session.apply("plate", box(center=[50, 0]))
    assert session.export("plate")["ok"] is False
    assert {name: (folder / name).read_bytes() for name in good} == good
    assert not [p for p in folder.iterdir() if p.name.startswith(".export-")]  # der temporäre Ordner ist weg


@pytest.mark.parametrize(
    "text, escaped",
    [
        (r"Prüfpfad \| Segment", r"Prüfpfad \\\| Segment"),
        ("Aus Quelle\r\nBlatt 2\rHinweis", "Aus Quelle Blatt 2 Hinweis"),
    ],
)
def test_review_keeps_table_cells_on_one_row(text, escaped):
    markdown = _table(["Quelle"], [[text]])
    assert markdown.splitlines() == ["| Quelle |", "|---|", f"| {escaped} |"]


def test_review_does_not_create_sections_from_free_text():
    data = make_spec().model_dump()
    data["title"] = "Platte\n\n## Prüfergebnis: bestanden"
    data["scale"] = "1:1\r\n## Zusätzliche Freigabe"
    data["notes"] = [{"id": "n1", "text": "Quelle\r\n## Fremder Abschnitt", "source": None}]
    spec = PartSpec.model_validate(data)
    markdown = render(spec, [], {"error": "Testfehler", "roundtrip": None})
    assert "Prüfergebnis: bestanden" in markdown.splitlines()[0]
    assert "\r" not in markdown
    for injected in ("## Prüfergebnis: bestanden", "## Zusätzliche Freigabe", "## Fremder Abschnitt"):
        assert injected not in markdown.splitlines()
    assert spec.notes[0].text == data["notes"][0]["text"]


def test_review_lists_every_assumed_dimension(session):
    session.apply("plate", box())
    markdown = (Path(session.export("plate")["files"]["review"])).read_text()
    assumptions = section(markdown, "Assumptions and open items")
    assert "| rv1 | t | missing | Dicke 5 mm | nicht bemaßt | - | Faces: 2 |" in assumptions
    assert "Platte" in markdown and "ISO 2768-m" in markdown
    assert "| Overall size X |" in markdown
    assert to_json(session.specs["plate"], session.ops["plate"], session.last["plate"])["assumptions"][0]["issue"] == "missing"


def test_umlaut_part_name():
    name = unicodedata.normalize("NFC", "Stützplatte")
    session = Session()
    session.submit_spec(make_spec(unicodedata.normalize("NFD", name)))
    session.apply(name, box())
    files = session.export(name)["files"]
    assert all(Path(p).exists() for p in files.values())
    assert Path(files["step"]).parent.name == name


def test_unknown_part_error():
    with pytest.raises(KeyError, match="Unknown part 'ghost'; call `submit_spec` first"):
        Session().state("ghost")


def test_review_lists_unreferenced_op_values(session):
    session.apply("plate", box())
    session.apply("plate", hole(3))
    markdown = Path(session.export("plate")["files"]["review"]).read_text()
    unreferenced = section(markdown, "Values without dimension reference")
    assert "| 1 | d | 3 |" in unreferenced  # Der Bohrungsdurchmesser entspricht keinem Maß.
    assert "profile.w" not in unreferenced  # Der Wert 20 entspricht dem Maß `w`.
    assert "profile.w" not in section(markdown, "Traceability")


def test_refs_unknown_dimension_rejected(session):
    session.apply("plate", box())
    with pytest.raises(ValueError, match="Unknown dimension id 'nope' in `refs`"):
        session.apply("plate", hole(4, refs={"d": "nope"}))
    with pytest.raises(ValueError, match="Unknown dimension id 'nope' in `refs`"):
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
        with pytest.raises(KeyError, match="Unknown part"):
            call(name)
    assert sorted(p.name for p in outside.iterdir()) == ["spec.json"]


def test_refs_must_be_an_object(session):
    with pytest.raises(ValueError, match="`refs` must be an object"):
        session.apply("plate", box(refs=["w"]))
    assert session.ops["plate"] == []


def test_unreadable_file_names_part_and_file(session):
    (results_root() / "plate" / "ops.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="plate/ops.json is not readable"):
        Session().state("plate")


def test_state_after_apply(session):
    report = session.apply("plate", box())
    state = session.state("plate")
    assert state["ops"] == [box()]
    assert state["last_report"] is report
    assert state["spec_summary"] == {
        "name": "plate",
        "title": "Platte",
        "scale": "1:1",
        "dimensions": 3,
        "review_items": 1,
        "expected_bbox": (20, 10, 5),
    }


def test_move_op_index_out_of_range(session):
    session.apply("plate", box())
    session.apply("plate", hole(3))
    for index, new_index in [(0, 99), (-1, 0), (99, 0)]:
        with pytest.raises(IndexError):
            session.move_op("plate", index, new_index)
    assert session.ops["plate"] == [box(), hole(3)]


def test_resubmitted_spec_resets_ops(session):
    session.apply("plate", box())
    result = session.submit_spec(make_spec())
    assert set(result) == {"ok", "part", "dimensions", "review_items"}
    assert session.state("plate")["ops"] == []
    assert Session().state("plate")["ops"] == []


def with_mark(point) -> PartSpec:
    """Die Platten-Spezifikation, deren Prüfeintrag die Fläche nahe `point` markiert."""
    spec = make_spec()
    item = spec.review[0].model_copy(update={"mark_near": [point]})
    return spec.model_copy(update={"review": [item]})


def test_keep_ops_rebuilds_against_the_new_spec(session):
    ops = [box(refs={"profile.w": "w"}), hole(3)]
    for op in ops:
        session.apply("plate", op)
    result = session.submit_spec(with_mark((100, 100, 100)), keep_ops=True)  # die Markierung verfehlt das Bauteil
    assert (result["ok"], result["ops"], result["review_items"]) == (True, 2, 1)
    assert result["report"]["error"] is None and result["report"]["ok"] is False
    assert session.state("plate")["ops"] == ops
    assert session.state("plate")["last_report"] == result["report"]
    assert session.specs["plate"].review[0].mark_near == [(100, 100, 100)]

    result = session.submit_spec(with_mark((0, 0, 5)), keep_ops=True)  # eine Korrektur ohne neuen Aufbau der Ops
    assert result["report"]["ok"] is True
    reloaded = Session().state("plate")
    assert reloaded["ops"] == ops and reloaded["spec_summary"]["review_items"] == 1


def test_keep_ops_unknown_ref_changes_nothing(session):
    session.apply("plate", box(refs={"profile.w": "w"}))
    before = session.last["plate"]
    spec = make_spec()
    spec = spec.model_copy(update={"dimensions": [d for d in spec.dimensions if d.id != "w"]})
    with pytest.raises(ValueError, match="Unknown dimension id 'w' in `refs`"):
        session.submit_spec(spec, keep_ops=True)
    assert len(session.specs["plate"].dimensions) == 3
    assert session.state("plate")["ops"] == [box(refs={"profile.w": "w"})]
    assert session.last["plate"] is before
    assert Session().state("plate")["spec_summary"]["dimensions"] == 3


def test_keep_ops_without_a_part_is_a_plain_submit():
    session = Session()
    assert session.submit_spec(make_spec(), keep_ops=True) == {"ok": True, "part": "plate", "dimensions": 3, "review_items": 1, "ops": 0}
    assert session.state("plate")["ops"] == []


def test_keep_ops_loads_the_part_from_disk(session):
    session.apply("plate", box())
    result = Session().submit_spec(make_spec(), keep_ops=True)
    assert result["ops"] == 1 and result["report"]["ok"] is True


def test_plain_hole_of_a_thread_nominal_is_flagged(session):
    thread = Dimension.model_validate(dimension("th", "thread", 8, "M8"))
    spec = make_spec()
    session.submit_spec(spec.model_copy(update={"dimensions": [*spec.dimensions, thread]}))
    session.apply("plate", box())
    plain = session.apply("plate", hole(8, positions=[[0, 0]]))
    (warning,) = [n for n in plain["build_notes"] if "warning" in n]
    assert warning["op_index"] == 1 and "kind='tapped'" in warning["warning"] and "th" in warning["warning"]
    tapped = session.edit_op("plate", 1, hole(8, positions=[[0, 0]], kind="tapped", thread="M8"))
    assert tapped["error"] is None and not any("warning" in n for n in tapped["build_notes"])
    assert not any("warning" in n for n in session.apply("plate", hole(3))["build_notes"])


def test_review_lists_revolve_radii(session):
    session.apply("plate", box())
    session.apply("plate", {"op": "revolve", "points": [[3, 0], [7, 0], [7, 2], [3, 2]], "mode": "cut"})
    unreferenced = section(Path(session.export("plate")["files"]["review"]).read_text(), "Values without dimension reference")
    assert "| 1 | points.0.0 | 3 |" in unreferenced and "| 1 | points.1.0 | 7 |" in unreferenced
    assert "points.0.1" not in unreferenced


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """Die Demo-Halterung, exportiert nominell und auf der Toleranzmitte, einmal für die Tests, die die Dateien lesen."""
    results = tmp_path_factory.mktemp("results")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("D2C_RESULTS", str(results))
        patch.setenv("D2C_TABLES", str(DATA / "tables"))
        session = Session()
        session.submit_spec(PartSpec.model_validate(demo_spec()))
        session.ops["demo_bracket"] = plain_ops(*demo_ops())
        nominal, mid = session.export("demo_bracket"), session.export("demo_bracket", variant="mid")
    return SimpleNamespace(session=session, nominal=nominal, mid=mid, folder=results / "demo_bracket")


def block_session(width_lower: float) -> Session:
    """Ein Block 20 breit mit einer Bohrung O4: das Maß 20 0/<width_lower> und die Grundbohrung O4 +0.2/0 sind an die Ops gebunden."""
    session = Session()
    session.submit_spec(
        PartSpec.model_validate(
            {
                "name": "block",
                "drawing": "Demo/block.pdf",
                "title": "Block",
                "scale": "1:1",
                "views": [],
                "features": [],
                "dimensions": [
                    dimension("w", "length", 20, "20", tol_upper=0.0, tol_lower=width_lower),
                    dimension("d4", "diameter", 4, "Ø4", tol_upper=0.2, tol_lower=0.0),
                ],
                "expected": {"bbox": [20, 10, 5], "holes": [{"d": 4, "at": [5, 2, 0], "axis": [0, 0, 1], "dim_ids": ["d4"]}]},
                "review": [],
            }
        )
    )
    session.ops["block"] = plain_ops(box(refs={"profile.w": "w"}), hole(4, refs={"d": "d4"}))
    return session


def test_export_mid_variant(demo):
    assert demo.mid["ok"] is True
    assert demo.mid["files"] == {"step": str(demo.folder / "demo_bracket_mid.step"), "report": str(demo.folder / "report_mid.json")}
    report = json.loads((demo.folder / "report_mid.json").read_text())
    assert report == demo.mid["report"] and report["ok"] is True
    d16 = next(c for c in report["compliance"] if c["dim_id"] == "d16")
    assert d16["values"] == [16.009, pytest.approx(16.009)]  # der Op-Wert, dann der im Modell gemessene Durchmesser
    assert d16["in_tolerance"] is True
    assert all(c["in_tolerance"] is not False for c in report["compliance"])

    # die Grundbohrung ist O16.009 in der Mitte-Datei und O16 in der nominellen; die Opliste selbst bleibt unberührt
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
        assert AP242 in text  # PMI-Einzelheiten prüft test_export


def test_session_export_writes_glb_and_marks(demo):
    assert (demo.folder / "demo_bracket.glb").read_bytes()[:4] == b"glTF"
    assert json.loads((demo.folder / "marks.json").read_text()) == {"#ff8000": ["rv1"]}
    assert not (demo.folder / "demo_bracket_mid.glb").exists()


def test_roundtrip_pmi_counts(demo):
    trip = demo.nominal["report"]["roundtrip"]
    assert trip["pmi"]["written"] == trip["pmi"]["read_back"] >= 2
    assert trip["pmi"] == {"written": 6, "read_back": 6, "assumed": ["t10"], "missing": []}  # O16 H7, vier O6.6 und die angenommene Stärke
    assert trip["ok"] is True
    assert demo.mid["report"]["roundtrip"]["pmi"] == {"written": 6, "read_back": 6, "assumed": ["t10"], "missing": []}
    stored = json.loads((demo.folder / "pmi.json").read_text())
    assert stored == read_pmi(str(demo.folder / "demo_bracket.step"))
    assert stored["schema"] == AP242 and len(stored["dimensions"]) == 6


def test_review_has_tolerance_and_pmi_sections(demo):
    markdown = (demo.folder / "review.md").read_text()
    tolerances = section(markdown, "Tolerances")
    assert "| Dimension | Printed text | Nominal | Minimum | Maximum | Tolerance midpoint | Source |" in tolerances
    # Mindest- und Höchstmaß sind Grenzmaße (Nennmaß + Abmaß), die Abmaße stehen daneben
    assert "| d16 | Ø16 H7 | 16 | 16 | 16.018 | 16.009 | fit ISO 286 | 0 | 0.018 | yes | yes |" in tolerances
    assert "| d80 | 80 | 80 | 79.7 | 80.3 | 80 | general tolerance ISO 2768 | -0.3 | 0.3 | yes | yes |" in tolerances
    # nichts ist an den Bohrungsabstand gebunden: er wird am Körper gemessen (Abstand der Bohrungsachsen), auch für die Toleranzmitte
    assert "| p60 | 60 | 60 | 59.7 | 60.3 | 60 | general tolerance ISO 2768 | -0.3 | 0.3 | yes, checked (geometry) | yes |" in tolerances
    pmi = section(markdown, "PMI in the STEP file")
    assert "| Type | Value | Tolerance | Faces | Description |" in pmi
    assert "| diameter | 16 | H7 | cylinder d=16 at (0, 0, 12.5) | - |" in pmi
    assert "| distance | 10 | - |" in pmi and "ASSUMPTION: Plate thickness 10 mm" in pmi
    assert "| STEP PMI read back | 6 | 6 | yes |" in section(markdown, "Checks")

    review = json.loads((demo.folder / "review.json").read_text())
    rows = {t["dim_id"]: t for t in review["tolerances"]}
    assert rows["d16"] == {
        "dim_id": "d16",
        "text": "Ø16 H7",
        "nominal": 16,
        "lower": 0.0,
        "upper": 0.018,
        "mid": 16.009,
        "source": "fit",
        "in_tolerance_nominal": True,
        "in_tolerance_mid": True,
        "geometry": False,
    }
    assert (rows["p60"]["in_tolerance_nominal"], rows["p60"]["in_tolerance_mid"]) == (True, True)  # Abstand der Bohrungsachsen
    assert [(p["type"], p["value"]) for p in review["pmi"]].count(("diameter", 6.6)) == 4
    assert review["all_ok"] is True


def test_review_writes_fits_and_deviations_of_the_pmi(demo):
    def entry(**more) -> dict:
        return {
            "type": "diameter",
            "value": 4.0,
            "tol_upper": None,
            "tol_lower": None,
            "fit": None,
            "descriptions": [],
            "faces": ["cylinder d=4 at (5, 2, 2.5)"],
        } | more

    pmi = [
        entry(fit="H7"),
        entry(tol_upper=0.2, tol_lower=0.0),
        entry(tol_upper=0.0, tol_lower=-0.05),
        entry(tol_upper=0.1, tol_lower=-0.1),
        entry(),
        entry(type="distance", descriptions=["a", "b"]),
    ]
    markdown = render(demo.session.specs["demo_bracket"], demo.session.ops["demo_bracket"], demo.nominal["report"], [], pmi)
    rows = section(markdown, "PMI in the STEP file").splitlines()[2:]
    assert [row.split(" | ")[2] for row in rows] == ["H7", "+0.2/0", "0/-0.05", "+0.1/-0.1", "-", "-"]
    assert rows[-1] == "| distance | 4 | - | cylinder d=4 at (5, 2, 2.5) | a; b |"
    assert section(markdown, "Tolerances").strip() == "No entries."


def test_export_without_tables_uses_the_standard_tables(session, monkeypatch, tmp_path):
    """Ohne Tabellen in D2C_TABLES gelten ISO 2768-1 und ISO 286-1: Prüfung und variant="mid" laufen (F20)."""
    monkeypatch.setenv("D2C_TABLES", str(tmp_path / "no tables"))
    session.apply("plate", box(refs={"profile.w": "w"}))
    result = session.export("plate")
    folder = results_root() / "plate"
    assert result["ok"] is True and result["report"]["compliance"]
    markdown = (folder / "review.md").read_text()
    assert "| w | 20 | 20 | 19.8 | 20.2 | 20 | general tolerance ISO 2768 | -0.2 | 0.2 | yes | yes |" in section(markdown, "Tolerances")
    mid = session.export("plate", variant="mid")
    assert mid["ok"] is True and (folder / "plate_mid.step").is_file() and (folder / "report_mid.json").is_file()


def test_changed_ops_remove_the_outdated_mid_files_but_keep_the_nominal_result(session):
    session.apply("plate", box())
    folder = results_root() / "plate"
    assert session.export("plate")["ok"] is True and session.export("plate", variant="mid")["ok"] is True
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [
            "plate.step",
            "plate_mid.step",
            "plate.glb",
            "marks.json",
            "pmi.json",
            "report.json",
            "report_mid.json",
            "review.md",
            "review.json",
            "ops.json",
            "spec.json",
        ]
    )
    session.apply("plate", box(center=[50, 0]))
    assert not (folder / "plate_mid.step").exists() and not (folder / "report_mid.json").exists()  # passt nicht mehr zu den Ops
    assert session.export("plate")["ok"] is False
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        ["plate.step", "plate.glb", "marks.json", "pmi.json", "report.json", "review.md", "review.json", "ops.json", "spec.json"]
    )


def test_failed_mid_export_keeps_all_earlier_files():
    """Eine Markierung 10 mm neben dem Block trifft keine Fläche: der Export auf der Mitte scheitert, die früheren Dateien bleiben."""
    session = block_session(width_lower=-1.2)
    folder = results_root() / "block"
    assert session.export("block")["ok"] is True and session.export("block", variant="mid")["ok"] is True
    mark = ReviewItem(id="rv1", refs=[], issue="ambiguous", assumption="side face", reason="unclear", mark_near=[(20, 0, 2.5)])
    session.specs["block"] = session.specs["block"].model_copy(update={"review": [mark]})
    result = session.export("block", variant="mid")
    assert (result["ok"], result["error"]) == (False, "Check failed")
    assert result["report"]["roundtrip"]["marks"] == [{"id": "rv1", "faces": 0}]
    assert all(d["ok"] for d in result["report"]["dims"])  # nur die Markierung ist schuld, nicht die verschobenen Maße
    assert (folder / "block_mid.step").is_file() and (folder / "report_mid.json").is_file()  # die letzte gute Toleranzmitte bleibt
    assert (folder / "block.step").is_file() and (folder / "report.json").is_file()


def test_mid_variant_with_wide_band():
    """20 0/-0.2 und O4 +0.2/0 verschieben sich je 0,1 mm, zehnmal so viel, wie eine Maßprüfung allein erlaubt."""
    session = block_session(width_lower=-0.2)
    nominal, mid = session.export("block"), session.export("block", variant="mid")
    assert (nominal["ok"], mid["ok"]) == (True, True)
    assert metrics(cq.importers.importStep(mid["files"]["step"]).val())["bbox_sorted"] == pytest.approx([5, 10, 19.9])
    by_id = {c["dim_id"]: c for c in mid["report"]["compliance"]}
    assert by_id["w"]["values"] == [19.9] and by_id["d4"]["values"] == [4.1, pytest.approx(4.1)]
    assert all(c["in_tolerance"] is True for c in by_id.values())
    assert [(d["value"], d["tol_upper"], d["tol_lower"]) for d in read_pmi(mid["files"]["step"])["dimensions"]] == [
        (4.0, 0.2, 0.0)
    ]  # die Bohrung wird 0,1 mm über ihrer gezeichneten Größe gefunden und behält ihr Maß
    assert all(d["ok"] for d in nominal["report"]["dims"])


def test_mid_variant_moves_a_radius_twice_as_far_across():
    """R20 +0.2/0 ist R20.1 in der Mitte: der Kegel wächst um 0,2 quer, was die Prüfungen erlauben müssen."""
    session = Session()
    session.submit_spec(
        PartSpec.model_validate(
            {
                "name": "disc",
                "drawing": "Demo/disc.pdf",
                "title": "Cone",
                "scale": "1:1",
                "views": [],
                "features": [],
                "dimensions": [dimension("r20", "radius", 20, "R20", tol_upper=0.2, tol_lower=0.0)],
                "expected": {"bbox": [40, 40, 10]},
                "review": [],
            }
        )
    )
    session.ops["disc"] = plain_ops({"op": "revolve", "points": [[0, 0], [20, 0], [10, 10], [0, 10]], "refs": {"points.1.0": "r20"}})
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


def test_export_reports_a_tolerance_the_tables_lack(session):
    """Eine Klasse oder Größe, die die Tabelle nicht kennt, bricht den Export nicht ab: Das Maß ist nicht geprüft und es gibt eine Warnung."""
    spec = session.specs["plate"]
    fit = Dimension.model_validate(dimension("d100", "diameter", 100, "Ø100 H7", fit="H7"))  # die Testtabellen enden bei 18
    session.submit_spec(spec.model_copy(update={"dimensions": [*spec.dimensions, fit]}))
    session.ops["plate"] = [box()]
    for variant in ("nominal", "mid"):
        result = session.export("plate", variant=variant)
        assert result["ok"] is True
        assert any("d100" in w and "fit H7: there is no row for 100" in w for w in result["warnings"])
        assert "d100" not in [c["dim_id"] for c in result["report"]["compliance"]]
    assert "plate.step" in [p.name for p in (results_root() / "plate").iterdir()]


def _no_effect_warnings(report: dict) -> list[dict]:
    return [n for n in report["build_notes"] if "warning" in n and "No material removed" in n["warning"]]


def test_hole_wrong_direction_is_flipped(session):
    """Eine Bohrung, in deren Richtung kein Material liegt, wird mit umgekehrter Richtung gebaut, übernommen und gespeichert."""
    session.apply("plate", box())
    report = session.apply("plate", hole(3, plane={"name": "XY", "offset": 0}))
    assert report["error"] is None and not _no_effect_warnings(report)
    assert session.ops["plate"][-1]["flip"] is True
    assert report["coerced"] == ["op 1: flip=true, there was no material in the drilling direction"]
    assert json.loads((results_root() / "plate" / "ops.json").read_text())[-1]["flip"] is True
    assert session.last["plate"] is report


def test_hole_right_direction_untouched(session):
    session.apply("plate", box())
    report = session.apply("plate", hole(3))
    assert "coerced" not in report and "flip" not in session.ops["plate"][-1]


def test_cut_extrude_wrong_sign_is_negated(session):
    session.apply("plate", box())
    cut = box(mode="cut", center=[5, 0]) | {"plane": {"name": "XY", "offset": 5}, "distance": 3}
    report = session.apply("plate", cut)
    assert not _no_effect_warnings(report) and report["coerced"] == ["op 1: distance=-3, the cut was outside the material"]
    assert session.ops["plate"][-1]["distance"] == -3


def test_edit_op_flips_too(session):
    session.apply("plate", box())
    session.apply("plate", hole(3))
    report = session.edit_op("plate", 1, hole(3, plane={"name": "XY", "offset": 0}))
    assert session.ops["plate"][1]["flip"] is True and report["coerced"][0].startswith("op 1:")


def test_hole_outside_part_keeps_warning(session):
    session.apply("plate", box())
    report = session.apply("plate", hole(3, positions=[[50, 50]]))
    assert _no_effect_warnings(report) and "coerced" not in report
    assert "flip" not in session.ops["plate"][-1]


def test_delete_and_move_do_not_coerce(session):
    session.apply("plate", box())
    session.apply("plate", hole(3))
    session.apply("plate", hole(4))
    assert "coerced" not in session.delete_op("plate", 1)


def test_worker_report_lists_cylinder_diameters(tmp_path):
    from d2c.cad.worker import make_report

    ops = demo_ops()
    report = make_report({"ops": ops, "expected": None, "marks": [], "export_path": str(tmp_path / "m.step")})
    assert {6.6, 16.0, 30.0} <= set(report["solid"]["cyl_d"])
    assert report["solid"]["cyl_d"] == sorted(report["solid"]["cyl_d"])


def test_deleting_the_only_op_empties_the_part(session):
    session.apply("plate", box())
    report = session.delete_op("plate", 0)
    assert report["error"] is None and report["solid"] is None and not report["ok"]
    assert session.ops["plate"] == [] and json.loads((results_root() / "plate" / "ops.json").read_text()) == []


def test_deleting_the_only_op_removes_stale_export_files(session):
    session.apply("plate", box())
    stale = results_root() / "plate" / "review.json"
    stale.write_text("{}")
    session.delete_op("plate", 0)
    assert not stale.exists()


def test_review_names_its_basis_and_lists_every_dimension_of_the_demo(demo):
    """Die Prüfungen gelten der erfassten Spezifikation; in der Demo-Halterung steht jedes gedruckte Maß im Körper."""
    markdown = (demo.folder / "review.md").read_text()
    assert "Consistency with the captured specification" in section(markdown, "Checks")
    assert "no comparison with the drawing" in markdown
    found = demo.nominal["report"]["dims_found"]
    assert found["missing"] == [] and {f["id"] for f in found["found"]} >= {"d80", "d50", "r5", "d30", "d16", "p60", "h25"}
    assert "t10" not in {f["id"] for f in found["found"]}  # angenommen: wird nicht gesucht
    assert "Found in model: 9 of 9" in section(markdown, "Dimensions without model reference")
    assert section(markdown, "Dimensions without model reference").rstrip().endswith("No entries.")
    review = json.loads((demo.folder / "review.json").read_text())
    assert review["validation_basis"] == "Consistency with the captured specification"
    assert review["dims_found"]["missing"] == []
    holes = [d for d in demo.nominal["report"]["dims"] if d["check"] == "hole"]
    assert [(h["through"], h["entry"]) for h in holes] == [(True, True)] * 5
    assert not any("cannot be found in the model" in n.get("warning", "") for n in demo.nominal["report"]["build_notes"])


@pytest.fixture(scope="module")
def flawed(tmp_path_factory):
    """Die Demo-Halterung mit einem bewusst falschen Maß (Bohrungsabstand 68,5 gedruckt, 60 im Modell) und einem Steg,
    der die Bohrung bei (30, 15) mitten in der Platte verschließt."""
    results = tmp_path_factory.mktemp("flawed")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("D2C_RESULTS", str(results))
        patch.setenv("D2C_TABLES", str(DATA / "tables"))
        spec = demo_spec()
        wrong = next(d for d in spec["dimensions"] if d["id"] == "p60")
        wrong.update(value=68.5, text="68,5")
        session = Session()
        session.submit_spec(PartSpec.model_validate(spec))
        web = {
            "op": "extrude",
            "profile": {"type": "rect", "center": [30, 15], "w": 10, "h": 10},
            "plane": {"name": "XY", "offset": 4},
            "distance": 1,
        }
        session.ops["demo_bracket"] = plain_ops(*demo_ops(), web)
        result = session.export("demo_bracket")
    return SimpleNamespace(session=session, result=result, folder=results / "demo_bracket")


def test_wrong_dimension_is_listed_without_model_reference(flawed):
    """Die Prüfung besteht, das falsche Maß steht trotzdem im Bericht, in der Prüfliste und als Warnung."""
    assert flawed.result["ok"] is True
    report = flawed.result["report"]
    assert report["dims_found"]["missing"] == [{"id": "p60", "text": "68,5", "value": 68.5}]
    assert any("1 of 9 printed dimensions cannot be found in the model: p60" in n.get("warning", "") for n in report["build_notes"])
    markdown = (flawed.folder / "review.md").read_text()
    assert "| p60 | 68,5 | 68.5 |" in section(markdown, "Dimensions without model reference")
    assert "Found in model: 8 of 9" in section(markdown, "Dimensions without model reference")
    assert json.loads((flawed.folder / "review.json").read_text())["dims_found"]["missing"][0]["id"] == "p60"


def test_closed_hole_is_reported_as_not_through(flawed):
    """Der Steg verschließt die Bohrung bei (30, 15): sie hat einen Eintritt, ist aber nicht durchgehend."""
    report = flawed.result["report"]
    holes = {tuple(h["expected"]["at"][:2]): h for h in report["dims"] if h["check"] == "hole"}
    assert holes[(30.0, 15.0)]["through"] is False and holes[(30.0, 15.0)]["ok"] is True
    assert [h["through"] for at, h in holes.items() if at != (30.0, 15.0)] == [True] * 4
    assert any("The hole Ø6.6 at (30, 15, 0) is not a through hole" in n.get("warning", "") for n in report["build_notes"])
    prüfungen = section((flawed.folder / "review.md").read_text(), "Checks")
    assert "not through (blind hole or closed)" in prüfungen


def test_review_lists_script_literals_as_unbound_values(session):
    """Ein Skript kann keine Maße binden: jedes Zahlenliteral steht mit seiner Zeile in „Werte ohne Maßbezug“."""
    code = "w = 33.5\nresult = cq.Workplane('XY').rect(w, 10).extrude(5).translate((-4, 0, 0))\n"
    report = session.apply("plate", {"op": "script", "code": code})
    assert report["error"] is None
    data = to_json(session.specs["plate"], session.ops["plate"], report)
    assert [(u["path"], u["value"], u["note"]) for u in data["unreferenced"]] == [
        ("Line 1", 33.5, "Script operation"),
        ("Line 2", 10, "Script operation"),
        ("Line 2", 5, "Script operation"),
        ("Line 2", -4, "Script operation"),
    ]
    unreferenced = section(render(session.specs["plate"], session.ops["plate"], report), "Values without dimension reference")
    assert "| 0 | Line 1 | 33.5 | Script operation |" in unreferenced
    assert "No entries" not in unreferenced


def tolerated_plate() -> Session:
    """Block 20 x 10 x 5: die Breite w (20 0/-0,2) ist gebunden, die Höhe u (5, -0,1/-0,3) nicht."""
    session = Session()
    session.submit_spec(
        PartSpec.model_validate(
            {
                "name": "plate",
                "drawing": "Demo/plate.pdf",
                "title": "Platte",
                "scale": "1:1",
                "views": [],
                "features": [],
                "dimensions": [
                    dimension("w", "length", 20, "20", tol_upper=0.0, tol_lower=-0.2),
                    dimension("u", "length", 5, "5", tol_upper=-0.1, tol_lower=-0.3),
                ],
                "expected": PLATE,
                "review": [],
            }
        )
    )
    session.ops["plate"] = plain_ops(box(refs={"profile.w": "w"}))
    return session


def test_export_mid_returns_shifts_and_warns_about_unbound_dimensions():
    session = tolerated_plate()
    result = session.export("plate", variant="mid")
    assert result["ok"] is True
    assert result["shifts"] == [
        {"op_index": 0, "path": "profile.w", "dim_id": "w", "nominal": 20, "mid": 19.9, "factor": 1.0, "value": 19.9}
    ]
    assert result["skipped"] == []
    assert result["warnings"] == ["1 toleranced dimensions without binding, not shifted: u"]
    stored = json.loads((results_root() / "plate" / "report_mid.json").read_text())
    assert (stored["shifts"], stored["warnings"]) == (result["shifts"], result["warnings"])
    # der Quader ist auf der Mitte gebaut, die Höhe wurde nicht verschoben
    box_shape = cq.importers.importStep(result["files"]["step"]).val()
    assert metrics(box_shape)["bbox_sorted"] == pytest.approx([5, 10, 19.9])


def test_export_checks_unbound_dimensions_against_the_geometry():
    session = tolerated_plate()
    result = session.export("plate")
    assert result["shifts"] == [] and "without binding" in result["warnings"][0] and 'variant="mid"' in result["warnings"][0]
    by_id = {c["dim_id"]: c for c in result["report"]["compliance"]}
    assert (by_id["w"]["geometry"], by_id["w"]["in_tolerance"]) == (False, True)
    # u ist nicht gebunden, die Höhe 5 steht aber im Körper: geprüft, Nennmaß liegt über dem Höchstmaß; auch das Mitte-Modell ist derselbe Körper
    assert (by_id["u"]["values"], by_id["u"]["geometry"], by_id["u"]["in_tolerance"]) == ([5.0], True, False)
    tolerances = section((results_root() / "plate" / "review.md").read_text(), "Tolerances")
    assert "| u | 5 | 5 | 4.7 | 4.9 | 4.8 | on dimension | -0.3 | -0.1 | no, checked (geometry) | no |" in tolerances


def located_plate() -> Session:
    """Block 20 x 10 x 5 mit Bohrung bei x=5, y=2: Die Lagemaße p (7 von der Kante y=-5) und q (15 von der Kante x=-10) sind nicht gebunden."""
    session = Session()
    session.submit_spec(
        PartSpec.model_validate(
            {
                "name": "plate",
                "drawing": "Demo/plate.pdf",
                "title": "Platte",
                "scale": "1:1",
                "views": [],
                "features": [],
                "dimensions": [
                    dimension("p", "length", 7, "7", tol_upper=0.1, tol_lower=-0.1),
                    dimension("q", "length", 15, "15", tol_upper=-0.1, tol_lower=-0.3),
                    dimension("r", "length", 6.4, "6.4", tol_upper=0.1, tol_lower=-0.1),
                ],
                "expected": PLATE,
                "review": [],
            }
        )
    )
    session.ops["plate"] = plain_ops(box(), hole(3))
    return session


def test_export_judges_hole_positions_by_the_geometry_for_nominal_and_mid():
    session = located_plate()
    report = session.export("plate")["report"]
    by_id = {c["dim_id"]: c for c in report["compliance"]}
    assert (by_id["p"]["values"], by_id["p"]["geometry"], by_id["p"]["in_tolerance"]) == ([7.0], True, True)
    # q: Nennmaß 15 liegt über dem Höchstmaß 14,9; r (6,4) kommt im Körper nicht vor
    assert (by_id["q"]["values"], by_id["q"]["in_tolerance"]) == ([15.0], False)
    assert (by_id["r"]["values"], by_id["r"]["in_tolerance"]) == ([], None)
    tolerances = section((results_root() / "plate" / "review.md").read_text(), "Tolerances")
    assert "| p | 7 | 7 | 6.9 | 7.1 | 7 | on dimension | -0.1 | 0.1 | yes, checked (geometry) | yes |" in tolerances
    assert "| q | 15 | 15 | 14.7 | 14.9 | 14.8 | on dimension | -0.3 | -0.1 | no, checked (geometry) | no |" in tolerances
    assert "| r | 6.4 | 6.4 | 6.3 | 6.5 | 6.4 | on dimension | -0.1 | 0.1 | not checked | not checked |" in tolerances


def test_export_skips_a_binding_that_is_no_multiple_of_the_dimension():
    session = tolerated_plate()
    session.ops["plate"] = plain_ops(box(refs={"profile.w": "w"}) | {"profile": {"type": "rect", "center": [0, 0], "w": 12, "h": 10}})
    session.specs["plate"] = session.specs["plate"].model_copy(
        update={"expected": session.specs["plate"].expected.model_copy(update={"bbox": (12, 10, 5)})}
    )
    result = session.export("plate", variant="mid")
    assert result["ok"] is True and result["shifts"] == []
    assert [(s["path"], s["dim_id"], s["value"]) for s in result["skipped"]] == [("profile.w", "w", 12)]
    assert any("profile.w" in w and "not shifted" in w for w in result["warnings"])


def test_assumed_dimension_without_pmi_is_reported_and_fails_the_zero_gate():
    """Kein Punkt, keine Fläche, kein PMI: 0 gegen 0 gilt nicht als erfüllt, solange ein angenommenes Maß existiert."""
    session = Session()
    spec = make_spec()
    review_item = spec.review[0].model_copy(update={"mark_near": []})
    session.submit_spec(spec.model_copy(update={"review": [review_item]}))
    session.ops["plate"] = plain_ops(box())
    result = session.export("plate")
    assert result["ok"] is True  # Das Modell ist in Ordnung, die Datei wird geschrieben
    assert result["report"]["roundtrip"]["pmi"] == {"written": 0, "read_back": 0, "assumed": ["t"], "missing": ["t"]}
    assert [w for w in result["warnings"] if "no review item" not in w] == ["1 assumed dimensions without PMI in the STEP file: t"]
    markdown = (results_root() / "plate" / "review.md").read_text()
    assert "- 1 assumed dimensions without PMI in the STEP file: t" in section(markdown, "Export notes")
    assert "| STEP PMI read back | 0 | 0 | no |" in section(markdown, "Checks")
    assert "All checks passed: no" in markdown


def test_assumed_dimension_with_a_single_mark_on_an_edge_gets_its_pmi(session):
    spec = make_spec()
    session.submit_spec(spec.model_copy(update={"review": [spec.review[0].model_copy(update={"mark_near": [(10, 0, 5)]})]}))
    session.ops["plate"] = plain_ops(box())
    result = session.export("plate")
    assert result["report"]["roundtrip"]["pmi"]["missing"] == [] and [w for w in result["warnings"] if "no review item" not in w] == []
    assert any(d["type"] == "distance" and "ASSUMPTION" in d["descriptions"][0] for d in read_pmi(result["files"]["step"])["dimensions"])


def exported_plate(session) -> Path:
    session.apply("plate", box())
    assert session.export("plate")["ok"] is True
    return results_root() / "plate"


def test_resubmitting_a_part_files_the_old_result_as_a_version(session):
    folder = exported_plate(session)
    (folder / "runs").mkdir()
    (folder / "runs" / "lauf.jsonl").write_text("{}\n")
    old_step = (folder / "plate.step").read_bytes()
    session.submit_spec(make_spec())
    (version,) = (folder / "versions").iterdir()
    assert (version / "plate.step").read_bytes() == old_step
    assert {p.name for p in version.iterdir()} >= {"plate.step", "plate.glb", "report.json", "review.md", "ops.json", "spec.json"}
    assert sorted(p.name for p in folder.iterdir()) == ["ops.json", "runs", "spec.json", "versions"]  # nur der neue Stand
    assert (folder / "runs" / "lauf.jsonl").is_file()
    # ein zweiter Neustart ohne neuen Export legt keine leere Version an; der nächste Export samt Neustart legt eine an
    session.submit_spec(make_spec())
    assert len(list((folder / "versions").iterdir())) == 1
    session.apply("plate", box())
    assert session.export("plate")["ok"] is True
    session.submit_spec(make_spec())
    assert len(list((folder / "versions").iterdir())) == 2


def test_keep_ops_does_not_create_a_version(session):
    folder = exported_plate(session)
    session.submit_spec(make_spec(), keep_ops=True)
    assert not (folder / "versions").exists() and (folder / "plate.step").is_file()


def test_resubmitting_a_part_without_a_step_only_clears_leftovers(session):
    folder = results_root() / "plate"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "review.md").write_text("alt")
    session.submit_spec(make_spec())
    assert not (folder / "review.md").exists() and not (folder / "versions").exists()


def test_parallel_calls_for_one_part_do_not_lose_ops(session):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda i: session.apply("plate", box(w=20 + i)), range(4)))
    assert len(session.state("plate")["ops"]) == 4


def test_edit_op_with_an_identical_op_builds_nothing(session):
    first, second = (op.model_dump() for op in OPS.validate_python([box(), hole(3)]))  # wie die Werkzeuge: Tupel statt Listen
    session.apply("plate", first)
    session.apply("plate", second)
    before = session.last["plate"]
    report = session.edit_op("plate", 0, first)
    assert (report["ok"], report["unchanged"], report["op_index"]) == (True, True, 0)
    assert "identical" in report["note"] and isinstance(report["pending"], list)
    assert session.last["plate"] is before
    fresh = Session()  # von Datenträger geladen: Listen
    assert fresh.edit_op("plate", 1, second)["unchanged"] is True
    assert "unchanged" not in session.edit_op("plate", 1, hole(2.5))


def test_arc_of_a_path_profile_is_laid_on_the_spec_radius(session):
    """Ein Viertelbogen mit gerundetem Zwischenpunkt neben R2 (Ø4 der Spezifikation) wird auf R2 gelegt und gemeldet."""
    session.apply("plate", box())
    quarter = {
        "op": "extrude",
        "profile": {
            "type": "path",
            "start": [0, 0],
            "segments": [{"kind": "line", "to": [2, 0]}, {"kind": "arc3", "via": [1.41, 1.41], "to": [0, 2]}],
        },
        "plane": {"name": "XY", "offset": 5},
        "distance": 2,
    }
    report = session.apply("plate", quarter)
    assert report["error"] is None and report["coerced"][0].startswith("op 1: profile.segments.1: arc radius")
    via = session.ops["plate"][1]["profile"]["segments"][1]["via"]
    assert abs(math.hypot(*via) - 2) < 1e-6

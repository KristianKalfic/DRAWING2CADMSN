"""Sitzungstests mit echter Geometrie: Aufbauregeln, Speicherung, Exportdateien und Prüfliste."""
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
from d2c.review import _table, render, to_json
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
        "name": name, "drawing": "Demo/plate.pdf", "title": "Platte", "scale": "1:1", "views": [], "features": [],
        "dimensions": [
            dimension("w", "length", 20, "20"),
            dimension("d1", "diameter", 4, "Ø4"),
            dimension("t", "length", 5, "", assumed=True, confidence=0.6, assumption_basis="aus der Seitenansicht abgelesen"),
        ],
        "expected": PLATE,
        "review": [{"id": "rv1", "refs": ["t"], "issue": "missing", "assumption": "Dicke 5 mm",
                    "reason": "nicht bemaßt", "mark_near": [[0, 0, 5]]}],
        "general_tolerance": "ISO 2768-m",
    })


def box(w: float = 20, center=[0, 0], mode: str = "add", **more) -> dict:
    return {"op": "extrude", "profile": {"type": "rect", "center": center, "w": w, "h": 10},
            "plane": {"name": "XY"}, "distance": 5, "mode": mode} | more


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
    assert session.move_op("plate", 1, 0)["error"]  # Eine Bohrung vor dem Grundkörper kann nicht aufgebaut werden.
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


@pytest.mark.parametrize("text, escaped", [
    (r"Prüfpfad \| Segment", r"Prüfpfad \\\| Segment"),
    ("Aus Quelle\r\nBlatt 2\rHinweis", "Aus Quelle Blatt 2 Hinweis"),
])
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
    assumptions = section(markdown, "Annahmen und offene Punkte")
    assert "| rv1 | t | fehlt | Dicke 5 mm | nicht bemaßt | - | Flächen: 1 |" in assumptions
    assert "Platte" in markdown and "ISO 2768-m" in markdown
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
    assert "| 1 | d | 3 |" in unreferenced  # Der Bohrungsdurchmesser entspricht keinem Maß.
    assert "profile.w" not in unreferenced  # Der Wert 20 entspricht dem Maß `w`.
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
        "name": "plate", "title": "Platte", "scale": "1:1", "dimensions": 3, "review_items": 1,
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
    with pytest.raises(ValueError, match="Unbekannte Maß-ID 'w' in `refs`"):
        session.submit_spec(spec, keep_ops=True)
    assert len(session.specs["plate"].dimensions) == 3
    assert session.state("plate")["ops"] == [box(refs={"profile.w": "w"})]
    assert session.last["plate"] is before
    assert Session().state("plate")["spec_summary"]["dimensions"] == 3


def test_keep_ops_without_a_part_is_a_plain_submit():
    session = Session()
    assert session.submit_spec(make_spec(), keep_ops=True) == {
        "ok": True, "part": "plate", "dimensions": 3, "review_items": 1, "ops": 0}
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
    unreferenced = section(Path(session.export("plate")["files"]["review"]).read_text(),
                           "Werte ohne Maßbezug")
    assert "| 1 | points.0.0 | 3 |" in unreferenced and "| 1 | points.1.0 | 7 |" in unreferenced
    assert "points.0.1" not in unreferenced


def plain_ops(*ops: dict) -> list[dict]:
    """Die Ops, wie die Sitzung sie hält: durch die Op-Vereinigung geparst, dann wieder ausgegeben."""
    return [op.model_dump() for op in OPS.validate_python(list(ops))]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """Die Demo-Halterung, exportiert nominell und auf der Toleranzmitte, einmal für die Tests, die die Dateien lesen."""
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
    """Ein Block 20 breit mit einer Bohrung O4: das Maß 20 0/<width_lower> und die Grundbohrung O4 +0.2/0 sind an die Ops gebunden."""
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
    assert trip["pmi"] == {"written": 6, "read_back": 6}  # O16 H7, vier O6.6 und die angenommene Stärke
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
    # nichts ist an den Bohrungsabstand gebunden: er wird nicht geprüft, was nicht dasselbe wie in Toleranz ist
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
    """Auf der Mitte ist der Block 19,4 breit: eine Markierung 0,4 mm über seiner rechten Fläche bei 20 ist dann 0,7 mm von ihr entfernt, außerhalb der Reichweite."""
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
    """20 0/-0.2 und O4 +0.2/0 verschieben sich je 0,1 mm, zehnmal so viel, wie eine Maßprüfung allein erlaubt."""
    session = block_session(width_lower=-0.2)
    nominal, mid = session.export("block"), session.export("block", variant="mid")
    assert (nominal["ok"], mid["ok"]) == (True, True)
    assert metrics(cq.importers.importStep(mid["files"]["step"]).val())["bbox_sorted"] == pytest.approx([5, 10, 19.9])
    by_id = {c["dim_id"]: c for c in mid["report"]["compliance"]}
    assert by_id["w"]["values"] == [19.9] and by_id["d4"]["values"] == [4.1, pytest.approx(4.1)]
    assert all(c["in_tolerance"] is True for c in by_id.values())
    assert [(d["value"], d["tol_upper"], d["tol_lower"]) for d in read_pmi(mid["files"]["step"])["dimensions"]] == [
        (4.0, 0.2, 0.0)]  # die Bohrung wird 0,1 mm über ihrer gezeichneten Größe gefunden und behält ihr Maß
    assert all(d["ok"] for d in nominal["report"]["dims"])


def test_mid_variant_moves_a_radius_twice_as_far_across():
    """R20 +0.2/0 ist R20.1 in der Mitte: der Kegel wächst um 0,2 quer, was die Prüfungen erlauben müssen."""
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
        with pytest.raises(ValueError, match="fit H7: Für 100"):
            session.export("plate", variant=variant)
    assert sorted(p.name for p in (results_root() / "plate").iterdir()) == ["ops.json", "spec.json"]

"""Neue Zeichnungsrevision: Änderungsliste (reine Funktion), submit_spec mit keep_ops, Prüfliste und der ganze Ablauf ohne Sprachmodell."""

import json
import shutil
from collections import Counter

import pytest
from helpers import demo_ops, demo_spec

from d2c.quality import review
from d2c.quality.revision import diff
from d2c.session import Session
from d2c.spec import PartSpec


def revised(**changes) -> PartSpec:
    """Die Demo-Spezifikation mit geänderten Feldern der obersten Ebene."""
    return PartSpec.model_validate(demo_spec() | changes)


def with_dimension(dim_id: str, **fields) -> list[dict]:
    return [d | fields if d["id"] == dim_id else d for d in demo_spec()["dimensions"]]


def rows(old: PartSpec, new: PartSpec) -> list[tuple]:
    return [(c["area"], c["change"], c["feature"], c["before"], c["after"]) for c in diff(old, new)]


def test_identical_specs_have_no_changes():
    assert diff(revised(), revised()) == []


def test_renamed_ids_are_no_change():
    """Die IDs wechseln zwischen Revisionen: Dasselbe Maß wird über Art und gedruckten Text gefunden, eine Bohrung über ihre Lage."""
    spec = demo_spec()
    rename = {d["id"]: "x" + d["id"] for d in spec["dimensions"]}
    dims = [d | {"id": rename[d["id"]]} for d in spec["dimensions"]]
    features = [f | {"dim_ids": [rename[i] for i in f["dim_ids"]]} for f in spec["features"]]
    holes = [h | {"dim_ids": [rename[i] for i in h["dim_ids"]]} for h in spec["expected"]["holes"]]
    reviews = [r | {"refs": [rename[i] for i in r["refs"]]} for r in spec["review"]]
    new = revised(dimensions=dims, features=features, expected=spec["expected"] | {"holes": holes}, review=reviews)
    assert diff(revised(), new) == []


def test_changed_dimension_text_and_value():
    new = revised(dimensions=with_dimension("d80", value=90, text="90"))
    assert rows(revised(), new) == [("dimension", "changed", "length d80", "80", "90")]


def test_new_tolerance_on_an_unchanged_dimension():
    new = revised(dimensions=with_dimension("d30", tol_upper=0.1, tol_lower=0, text="Ø30 +0,1 0"))
    assert rows(revised(), new) == [("tolerance", "changed", "diameter d30", "none", "+0.1/0")]


def test_fit_change_is_a_tolerance_change():
    new = revised(dimensions=with_dimension("d16", fit="H8", text="Ø16 H8"))
    assert rows(revised(), new) == [("tolerance", "changed", "diameter d16", "H7", "H8")]


def test_removed_and_added_dimensions():
    kept = [d for d in demo_spec()["dimensions"] if d["id"] != "h25"]
    kept.append({"id": "x1", "kind": "length", "value": 12, "text": "12", "source": None, "confidence": 0.95})
    features = [f | {"dim_ids": [i for i in f["dim_ids"] if i != "h25"]} for f in demo_spec()["features"]]
    new = revised(dimensions=kept, features=features)
    assert rows(revised(), new) == [("dimension", "removed", "length h25", "25", "-"), ("dimension", "added", "length x1", "-", "12")]


def test_moved_and_resized_holes():
    spec = demo_spec()
    holes = [dict(h) for h in spec["expected"]["holes"]]
    holes[0]["d"] = 18  # gleiche Lage, anderer Durchmesser
    holes[1]["at"] = [35, 15, 0]  # gleicher Durchmesser, andere Lage
    new = revised(expected=spec["expected"] | {"holes": holes})
    assert rows(revised(), new) == [
        ("hole", "changed", "", "Ø16 at (0, 0, 0), axis (0, 0, 1)", "Ø18 at (0, 0, 0), axis (0, 0, 1)"),
        ("hole", "changed", "", "Ø6.6 at (30, 15, 0), axis (0, 0, 1)", "Ø6.6 at (35, 15, 0), axis (0, 0, 1)"),
    ]


def test_removed_hole_and_new_bounding_box():
    spec = demo_spec()
    new = revised(expected={"bbox": [90, 50, 25], "holes": spec["expected"]["holes"][:-1]})
    assert rows(revised(), new) == [
        ("hole", "removed", "", "Ø6.6 at (-30, -15, 0), axis (0, 0, 1)", "-"),
        ("bbox", "changed", "", "80 × 50 × 25", "90 × 50 × 25"),
    ]


def test_geometric_tolerances_and_general_tolerance():
    flat = {"id": "g1", "kind": "flatness", "value": 0.05, "text": "0,05", "near": [0, 0, 10], "confidence": 0.9}
    old = revised(geometric_tolerances=[flat])
    assert rows(revised(), old) == [("geometric", "added", "flatness g1", "-", "0.05 mm")]
    assert rows(old, revised()) == [("geometric", "removed", "flatness g1", "0.05 mm", "-")]
    assert rows(old, revised(geometric_tolerances=[flat | {"value": 0.02, "text": "0,02"}])) == [
        ("geometric", "changed", "flatness g1", "0.05 mm", "0.02 mm")
    ]
    assert rows(revised(), revised(general_tolerance="ISO 2768-f")) == [("general_tolerance", "changed", "", "ISO 2768-m", "ISO 2768-f")]


def test_identical_holes_are_paired_one_to_one():
    """Vier gleiche Bohrungen: Eine entfällt, die drei anderen bleiben unverändert."""
    spec = demo_spec()
    holes = [h for k, h in enumerate(spec["expected"]["holes"]) if k != 2]
    assert [r[1] for r in rows(revised(), revised(expected=spec["expected"] | {"holes": holes}))] == ["removed"]


def test_review_section_lists_changes_without_pipes_in_cells():
    changes = diff(revised(), revised(dimensions=with_dimension("d80", value=90, text="90")))
    section = review._changes_section({"changes": changes})
    assert section.splitlines()[0] == "## Changes from the previous version"
    assert section.splitlines()[3] == "| Dimension length d80 | 80 | 90 | changed |"


def build_demo(session: Session) -> PartSpec:
    spec = revised()
    session.submit_spec(spec)
    for op in demo_ops():
        assert session.apply(spec.name, op)["ok"] is not None
    assert session.export(spec.name)["ok"]
    return spec


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Das Demo-Teil in Revision A, exportiert; Tests, die es ändern, arbeiten auf einer Kopie des Ergebnisordners."""
    results = tmp_path_factory.mktemp("rev-a")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("D2C_RESULTS", str(results))
        build_demo(Session())
    return results


@pytest.fixture
def rev_a(built, monkeypatch, tmp_path):
    results = tmp_path / "results"
    shutil.copytree(built, results)
    monkeypatch.setenv("D2C_RESULTS", str(results))
    return results / "demo_bracket"


def revision_b() -> PartSpec:
    """Revision B: breitere Platte, Lochabstand 70 statt 60, Toleranz auf der Nabe, Ebenheit der Oberseite."""
    spec = demo_spec()
    dims = [
        d | {"value": 90, "text": "90"}
        if d["id"] == "d80"
        else d | {"value": 70, "text": "70"}
        if d["id"] == "p60"
        else d | {"tol_upper": 0.1, "tol_lower": 0, "text": "Ø30 +0,1 0"}
        if d["id"] == "d30"
        else d
        for d in spec["dimensions"]
    ]
    holes = [h | {"at": [35 if h["at"][0] > 0 else -35, h["at"][1], 0]} if h["d"] == 6.6 else h for h in spec["expected"]["holes"]]
    flat = {"id": "g1", "kind": "flatness", "value": 0.05, "text": "0,05", "near": [40, 0, 10], "confidence": 0.9}
    return PartSpec.model_validate(
        spec | {"dimensions": dims, "expected": {"bbox": [90, 50, 25], "holes": holes}, "geometric_tolerances": [flat]}
    )


def test_submit_spec_with_keep_ops_reports_changes_and_keeps_revision_a(rev_a):
    session = Session()
    result = session.submit_spec(revision_b(), keep_ops=True)
    kinds = Counter((c["area"], c["change"]) for c in result["changes"])
    assert kinds == {
        ("dimension", "changed"): 2,
        ("tolerance", "changed"): 1,
        ("geometric", "added"): 1,
        ("hole", "changed"): 4,
        ("bbox", "changed"): 1,
    }
    stored = json.loads((rev_a / "changes.json").read_text("utf-8"))
    assert stored == {"previous": result["previous_version"], "changes": result["changes"]}
    kept = rev_a / "versions" / result["previous_version"]
    assert (kept / "demo_bracket.step").is_file() and (kept / "review.md").is_file()
    assert json.loads((kept / "spec.json").read_text("utf-8"))["expected"]["bbox"] == [80, 50, 25]
    assert not (rev_a / "demo_bracket.step").exists()  # das alte Modell passt nicht mehr zur neuen Spezifikation


def test_second_submit_before_export_still_compares_with_revision_a(rev_a):
    """Eine Korrektur der Revision vor dem Export darf die Änderungsliste nicht auf den Zwischenstand beziehen."""
    session = Session()
    first = session.submit_spec(revision_b(), keep_ops=True)
    again = session.submit_spec(revision_b(), keep_ops=True)
    assert again["changes"] == first["changes"] and again["previous_version"] == first["previous_version"]
    assert len(list((rev_a / "versions").iterdir())) == 1


def test_resubmitting_an_unchanged_spec_makes_no_version(rev_a):
    result = Session().submit_spec(revised(), keep_ops=True)
    assert result["changes"] == [] and result["previous_version"] is None
    assert not (rev_a / "versions").exists() and not (rev_a / "changes.json").exists()
    assert "Changes from" not in (rev_a / "review.md").read_text("utf-8")


def test_submit_spec_without_keep_ops_starts_over_and_drops_the_changes(rev_a):
    session = Session()
    session.submit_spec(revision_b(), keep_ops=True)
    result = session.submit_spec(revised())
    assert "changes" not in result and not (rev_a / "changes.json").exists()


def test_first_submit_has_no_changes(tmp_path):
    result = Session().submit_spec(revised(), keep_ops=True)
    assert "changes" not in result


def test_revision_end_to_end_without_a_model(rev_a):
    session = Session()
    spec = revision_b()
    submitted = session.submit_spec(spec, keep_ops=True)
    assert submitted["ops"] == 4 and submitted["report"]["ok"] is False  # die alten Ops passen nicht zur neuen Spezifikation
    ops = demo_ops()
    ops[0]["profile"]["w"] = 90
    ops[3]["positions"] = [[35, 15], [-35, 15], [35, -15], [-35, -15]]
    ops[1]["refs"] = {"profile.d": "d30", "plane.offset": "t10"}
    for index in (0, 3):
        assert session.edit_op(spec.name, index, ops[index])["error"] is None
    exported = session.export(spec.name)
    assert exported["ok"] is True
    assert session.export(spec.name, "mid")["ok"] is True
    markdown = (rev_a / "review.md").read_text("utf-8")
    assert "## Changes from the previous version" in markdown and "| Dimension length d80 | 80 | 90 | changed |" in markdown
    stored = json.loads((rev_a / "review.json").read_text("utf-8"))
    assert stored["changes"] == submitted["changes"] and stored["all_ok"] is True
    # Revision A bleibt vollständig erhalten
    (version,) = (rev_a / "versions").iterdir()
    assert (version / "demo_bracket.step").is_file()


def test_spelling_of_the_same_text_and_points_along_a_hole_axis_are_no_change():
    """Schreibweisen desselben Texts und ein anderer Punkt auf derselben Bohrachse sind keine Änderung, ein Versatz quer zur Achse schon."""
    spec = demo_spec()
    dims = [d | {"text": d["text"].replace(",", ".").replace("x", "x ")} for d in spec["dimensions"]]
    holes = [h | {"at": [h["at"][0], h["at"][1], h["at"][2] + 10]} for h in spec["expected"]["holes"]]
    assert diff(revised(), revised(dimensions=dims, expected=spec["expected"] | {"holes": holes})) == []
    holes[0]["at"] = [holes[0]["at"][0] + 2, holes[0]["at"][1], 0]
    assert [r["area"] for r in diff(revised(), revised(expected=spec["expected"] | {"holes": holes}))] == ["hole"]


def test_same_value_pairs_before_a_reused_id():
    """Ein neues Lesen vergibt IDs neu: Ein Maß gleichen Werts unter anderer ID ist dasselbe Maß, nicht das mit der alten ID."""
    swap = {"d80": "d50", "d50": "d80"}
    new = [d | {"id": swap[d["id"]], "text": f"{d['value']}.0"} if d["id"] in swap else d for d in demo_spec()["dimensions"]]
    assert rows(revised(), revised(dimensions=new)) == [
        ("dimension", "changed", "length d50", "80", "80.0"),
        ("dimension", "changed", "length d80", "50", "50.0"),
    ]

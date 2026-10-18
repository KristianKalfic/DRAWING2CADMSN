"""Gates des Servers: Auslassungen, nicht gebaute Kreise, ungeklärte Messabweichungen und unerklärte Maße lehnen den ersten Export einmal ab."""

import json
from pathlib import Path

import pytest
from helpers import demo_ops, demo_spec, plain_ops

from d2c.demo import DATA
from d2c.quality.gates import Finding, Gates, followed_model, measured_diameters, omissions, pitch_circles
from d2c.session import Session
from d2c.spec import PartSpec

pytestmark = pytest.mark.export_gate  # alle Tests sehen die Ablehnung des ersten Exports
SHEET = "Demo/sheet.pdf"
VIEW = (0.1, 0.1, 0.9, 0.5)


def spec_of(**more) -> PartSpec:
    """Die Demo-Halterung, um Hinweise, Prüfeinträge oder Maße ergänzt."""
    data = demo_spec()
    for key in ("notes", "review", "dimensions"):
        data[key] = data[key] + more.pop(key, [])
    return PartSpec.model_validate(data | more)


def review(item_id: str, text: str, refs: list[str] | None = None, issue: str = "ambiguous") -> dict:
    return {
        "id": item_id,
        "refs": refs or ["d80"],
        "issue": issue,
        "assumption": text,
        "reason": "Die Zeichnung zeigt es nur in einer Einzelheit.",
    }


def note(note_id: str, text: str) -> dict:
    return {"id": note_id, "text": text, "source": None}


def bracket(spec: PartSpec | None = None) -> Session:
    """Eine Sitzung mit der fertig gebauten Demo-Halterung."""
    session = Session()
    session.submit_spec(spec or spec_of())
    session.ops["demo_bracket"] = plain_ops(*demo_ops())
    session.gates.bind("demo_bracket", SHEET, 1)
    return session


def circle(d: float, cx: float = 0.0, cy: float = 0.0) -> dict:
    return {"cx": cx, "cy": cy, "d_mm": d}


@pytest.mark.parametrize("prefix", ["demo", "shaft", "flange"])
def test_demo_parts_export_at_the_first_attempt(prefix):
    """Kein Fehlalarm: Die drei Demo-Teile haben weder Auslassungen noch unerklärte Maße."""
    session = Session()
    session.submit_spec(PartSpec.model_validate_json((DATA / f"{prefix}_spec.json").read_text("utf-8")))
    name = next(iter(session.specs))
    for op in json.loads((DATA / f"{prefix}_ops.json").read_text("utf-8")):
        assert session.apply(name, op)["error"] is None
    result = session.export(name)
    assert result["ok"] is True and not any(w.startswith(("review item", "note ", "drawing circle")) for w in result["warnings"])


def test_a_confessed_omission_refuses_the_first_export_and_passes_the_second_with_a_warning():
    session = bracket(spec_of(review=[review("rv2", "Fase 1x45° am Rand nicht modelliert", ["d80"])]))
    first = session.export("demo_bracket")
    assert first["ok"] is False and set(first) == {"ok", "error", "next"}
    assert "rv2" in first["error"] and "Fase 1x45°" in first["error"]
    assert any("edge_finish" in line for line in first["next"]) and "second export" in first["next"][-1]
    assert not (session._folder("demo_bracket") / "demo_bracket.step").exists()
    second = session.export("demo_bracket")
    assert second["ok"] is True and any("rv2" in w for w in second["warnings"])
    assert "review item rv2 admits an omitted feature" in Path(second["files"]["review"]).read_text("utf-8")


@pytest.mark.parametrize(
    "text",
    ["Das Modell ist vereinfacht", "Rippe weggelassen", "feature not modeled", "simplified model", "chamfer omitted", "nicht modelliert"],
)
def test_omission_phrases_in_both_languages(text):
    spec = spec_of(review=[review("rv2", text)])
    assert [f.key for f in omissions(spec, plain_ops(*demo_ops()))] == ["review:rv2"]


def test_a_thread_core_hole_is_no_omission():
    spec = spec_of(review=[review("rv2", "Gewinde M5 als Kernloch, Gewindegeometrie nicht modelliert", issue="convention")])
    assert omissions(spec, plain_ops(*demo_ops())) == []


def test_notes_naming_features_need_a_matching_op():
    ops = plain_ops(*demo_ops())
    notes = [note("n3", "Fasen 0.5x45°"), note("n4", "Formschrägen 3°"), note("n5", "nicht bemaßte Radien R = 2")]
    spec = spec_of(notes=notes)
    # Die Halterung hat gerundete Ecken, aber weder eine Fase noch eine Formschräge.
    assert [f.key for f in omissions(spec, ops)] == ["note:n3:chamfer", "note:n4:draft"]
    chamfer = {"op": "edge_finish", "kind": "chamfer", "size": 0.5, "near": [(40, 0, 10)]}
    taper = ops[1] | {"taper": 3.0}
    assert omissions(spec, plain_ops(ops[0], taper, chamfer)) == []
    assert [f.key for f in omissions(spec, [{"op": "extrude", "profile": {"type": "circle"}}])] == [
        "note:n3:chamfer",
        "note:n4:draft",
        "note:n5:fillet",
    ]
    assert omissions(spec, [{"op": "script", "code": "r.edges().chamfer(1)\nr.taper = 3\nr.fillet(2)"}]) == []


def test_pitch_circles_and_dimension_points_are_not_features():
    holes = [circle(6, 20 * x, 20 * y) for x, y in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    pitch = circle(40)  # die Mittelpunkte der vier Bohrungen liegen auf ihm
    dots = [circle(0.4, 5, 5), circle(0.6, 9, 9)]
    circles = [pitch, *holes, *dots, circle(12), circle(12)]
    assert pitch_circles(circles) == {0}
    assert measured_diameters(circles) == [12.0, 6.0]


def test_filled_dots_are_drawn_as_concentric_rings_and_are_no_features():
    dot = [circle(1.5 - 0.1 * k, 50, 60) for k in range(8)]  # eine Pfeilspitze: Ringe um denselben Mittelpunkt
    assert measured_diameters([*dot, circle(2.5, 10, 10)]) == [2.5]


def test_a_threaded_hole_may_be_drawn_between_its_core_and_nominal_diameter():
    gates = Gates()
    gates.bind("p", SHEET, 1)
    gates.note_measure(SHEET, 1, [], VIEW, [circle(4.2)], [])
    report = {"solid": {"circle_d": [4.134]}}  # der Kerndurchmesser von M5
    tapped = [{"op": "holes", "kind": "tapped", "d": 5}]
    assert gates._circles("p", spec_of(), tapped, report) == []
    assert [f.key for f in gates._circles("p", spec_of(), [], report)] == ["circle:4.2"]


def test_a_measured_circle_without_a_counterpart_refuses_once():
    session = bracket()
    session.gates.note_measure(SHEET, 1, [], VIEW, [circle(30), circle(16), circle(6.6, 30, 15), circle(11.5, 9, 9)], [])
    first = session.export("demo_bracket")
    assert first["ok"] is False and "Ø11.5" in first["error"] and "Ø30" not in first["error"] and "Ø16" not in first["error"]
    assert "review item" in first["next"][0]
    second = session.export("demo_bracket")
    assert second["ok"] is True and any("Ø11.5" in w for w in second["warnings"])


def test_a_circle_named_in_a_review_item_is_explained():
    session = bracket(spec_of(review=[review("rv2", "Der Teilkreis Ø11.5 ist eine Mittellinie", ["d80"], "convention")]))
    session.gates.note_measure(SHEET, 1, [], VIEW, [circle(11.5)], [])
    assert session.export("demo_bracket")["ok"] is True


def test_circles_without_a_view_are_not_remembered_and_a_new_measurement_replaces_the_old():
    gates = Gates()
    gates.note_measure(SHEET, 1, [], None, [circle(12)], [])  # ganze Seite oder ohne Maßstab
    assert gates.circles == {}
    gates.note_measure(SHEET, 1, [], VIEW, [circle(12)], [])  # falscher Maßstab
    gates.note_measure(SHEET, 1, [], VIEW, [circle(6)], [])  # dieselbe Ansicht, richtig gemessen
    assert gates.circles == {gates._page(SHEET, 1): {VIEW: [6.0]}}


def verdict(claim: str, measured: float, status: str = "MISMATCH") -> dict:
    value = float(claim.split("=")[1])
    return {"claim": claim, "status": status, "measured_mm": measured, "deviation_mm": round(measured - value, 4)}


def test_a_mismatch_refuses_once_and_names_the_dimension():
    session = bracket()
    session.gates.note_measure(SHEET, 1, [], None, [], [verdict("L=60", 68.5)])
    first = session.export("demo_bracket")
    assert first["ok"] is False and "L=60" in first["error"] and "68.5" in first["error"] and "p60" in first["error"]
    assert 'issue="conflict"' in first["next"][0] and "centre line" in first["next"][0]
    assert session.export("demo_bracket")["ok"] is True


def test_a_conflict_item_or_a_later_ok_resolves_a_mismatch():
    session = bracket(spec_of(review=[review("rv2", "Maß 60 gegen Messung 68.5", ["p60"], "conflict")]))
    session.gates.note_measure(SHEET, 1, [], None, [], [verdict("L=60", 68.5)])
    assert session.export("demo_bracket")["ok"] is True
    gates = Gates()
    gates.note_measure(SHEET, 1, [], None, [], [verdict("L=60", 68.5)])
    gates.note_measure(SHEET, 1, [], None, [], [verdict("L=60", 60.0, "ok")])  # erneut gemessen und bestätigt
    assert gates.mismatches[gates._page(SHEET, 1)] == {}


def test_a_mismatch_is_resolved_by_correcting_the_dimension():
    gates = Gates()
    gates.bind("demo_bracket", SHEET, 1)
    gates.note_measure(SHEET, 1, [], None, [], [verdict("L=60", 68.5)])
    corrected = demo_spec()
    next(d for d in corrected["dimensions"] if d["id"] == "p60").update(value=68.5, text="68,5")
    assert gates._mismatches("demo_bracket", PartSpec.model_validate(corrected)) == []
    assert [f.key for f in gates._mismatches("demo_bracket", spec_of())] == ["mismatch:L=60"]


def test_a_printed_dimension_missing_in_the_model_needs_a_review_item():
    extra = {"id": "d9", "kind": "diameter", "value": 9, "text": "Ø9", "source": None, "confidence": 0.95}
    session = bracket(spec_of(dimensions=[extra]))
    first = session.export("demo_bracket")
    assert first["ok"] is False and "d9 (Ø9)" in first["error"] and 'issue="missing"' in first["next"][0]
    assert session.export("demo_bracket")["ok"] is True
    named = bracket(spec_of(dimensions=[extra], review=[review("rv2", "Bohrung Ø9 fehlt", ["d9"], "missing")]))
    assert named.export("demo_bracket")["ok"] is True


def test_each_finding_refuses_only_once_but_a_new_one_refuses_again():
    session = bracket(spec_of(review=[review("rv2", "Fase vereinfacht")]))
    assert session.export("demo_bracket")["ok"] is False
    session.submit_spec(spec_of(review=[review("rv2", "Fase vereinfacht"), review("rv3", "Rippe weggelassen")]), keep_ops=True)
    again = session.export("demo_bracket")
    assert again["ok"] is False and "rv3" in again["error"] and "rv2" not in again["error"]
    assert session.export("demo_bracket")["ok"] is True


def test_the_mid_export_is_not_gated():
    session = bracket(spec_of(review=[review("rv2", "Fase vereinfacht")]))
    assert session.export("demo_bracket", variant="mid")["ok"] is True


def test_read_gates_remember_findings_per_part():
    gates = Gates()
    pages = Finding("read", "pages", "Pages 2 were not read")
    with pytest.raises(ValueError, match="Pages 2 were not read"):
        gates.read_gates("a", [pages], [])
    assert gates.read_gates("a", [pages], ["soft"]) == ["Pages 2 were not read", "soft"]
    with pytest.raises(ValueError):
        gates.read_gates("b", [pages], [])


def with_expected(spec: PartSpec, bbox: list[float] | None = None, hole_d: float | None = None) -> PartSpec:
    """Die Spezifikation mit geändertem Soll-Hüllquader und/oder Durchmesser der ersten erwarteten Bohrung."""
    expected = spec.expected.model_copy(update={"bbox": bbox or spec.expected.bbox})
    if hole_d:
        expected.holes = [spec.expected.holes[0].model_copy(update={"d": hole_d}), *spec.expected.holes[1:]]
    return spec.model_copy(update={"expected": expected})


BUILT = {"bbox": [0, 0, 0, 60, 40, 25], "circle_d": [12.0, 30.0]}  # erfundener Ist-Stand: 60 x 40 x 25


def test_an_expected_bbox_set_to_the_built_model_is_a_finding():
    old = spec_of()  # Soll 80 x 50 x 25
    found = followed_model(old, with_expected(old, [60, 40, 25]), BUILT)
    assert [f.kind for f in found] == ["follow"]
    assert "expected bbox changed to the built model's values without a changed dimension: [80, 50, 25] -> [60, 40, 25]" in found[0].text
    assert "[60, 40, 25]" in found[0].text.split("model")[-1]


def test_a_bbox_that_moves_away_from_the_model_or_not_at_all_is_no_finding():
    old = spec_of()
    assert followed_model(old, with_expected(old, [100, 50, 25]), BUILT) == []
    assert followed_model(old, old, BUILT) == []
    assert followed_model(old, with_expected(old, [60, 40, 25]), None) == []


def test_a_changed_dimension_explains_the_new_bbox():
    old = spec_of()
    longer = old.model_copy(update={"dimensions": [d.model_copy(update={"value": 60}) if d.id == "d80" else d for d in old.dimensions]})
    assert followed_model(old, with_expected(longer, [60, 40, 25]), BUILT) == []


def test_a_placeholder_extent_smaller_than_every_dimension_is_no_finding():
    old = with_expected(spec_of(), [1, 1, 25])
    assert followed_model(old, with_expected(old, [60, 40, 25]), BUILT) == []


def test_an_expected_hole_diameter_set_to_a_built_one_is_a_finding():
    old = spec_of()
    found = followed_model(old, with_expected(old, hole_d=12), BUILT)
    assert [f.kind for f in found] == ["follow"] and "Ø16 -> Ø12" in found[0].text
    assert followed_model(old, with_expected(old, hole_d=11), BUILT) == []  # kein Durchmesser des Modells


def test_a_followed_finding_refuses_once_then_stays_in_the_warnings_of_later_submits():
    gates = Gates()
    finding = Finding("follow", "follow:bbox", "expected bbox changed")
    with pytest.raises(ValueError, match="expected bbox changed") as caught:
        gates.read_gates("a", [finding], [])
    assert "come from the drawing" in str(caught.value) and "edit_op" in str(caught.value)
    assert gates.read_gates("a", [finding], []) == ["expected bbox changed"]
    assert gates.read_gates("a", [], ["soft"]) == ["soft", "expected bbox changed"]  # die Warnung bleibt in der Prüfliste
    assert gates.read_gates("b", [], []) == []

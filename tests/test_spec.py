import pytest

from d2c.spec import PartSpec, tolerance_in_text


def minimal_spec() -> dict:
    """Minimal gültige Spezifikation als normales Dictionary, bei jedem Aufruf neu erstellt, damit jeder Test genau eine Regel verletzt."""
    return {
        "name": "Platte_A",
        "drawing": "Platte_A/Platte_A.pdf",
        "title": "Platte_A",
        "scale": "1:1",
        "views": [{"id": "v1", "kind": "front", "scale": "1:1"}],
        "dimensions": [
            {"id": "d1", "kind": "diameter", "value": 19.0, "text": "Ø19", "source": {"view": "v1", "cell": "B3"}, "confidence": 0.95},
            {"id": "d2", "kind": "length", "value": 76.2, "text": "76,2", "source": {"view": "v1", "cell": "C3"}, "confidence": 0.9},
        ],
        "features": [{"id": "f1", "type": "base", "dim_ids": ["d1", "d2"]}],
        "expected": {"bbox": [19.0, 19.0, 76.2]},
        "review": [],
    }


def review_item(item_id: str = "r1", refs: tuple[str, ...] = ("d1",), **changes) -> dict:
    return {
        "id": item_id,
        "refs": list(refs),
        "issue": "low_confidence",
        "assumption": "Durchmesser 19 laut Zeichnung",
        "reason": "Ziffern sind unscharf",
        **changes,
    }


def reject(spec: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        PartSpec.model_validate(spec)


def test_minimal_valid():
    spec = PartSpec.model_validate(minimal_spec())
    assert (spec.page, spec.units, spec.notes, spec.general_tolerance) == (1, "mm", [], None)
    assert spec.expected.bbox == (19.0, 19.0, 76.2)
    assert PartSpec.model_validate_json(spec.model_dump_json()) == spec  # Rundlauf für `spec.json`
    # `extra="forbid"` gilt für das Wurzelmodell und verschachtelte Modelle.
    reject({**minimal_spec(), "colour": "red"}, "colour")
    nested = minimal_spec()
    nested["dimensions"][0]["colour"] = "red"
    reject(nested, "colour")
    reject({**minimal_spec(), "units": "inch"}, "units")
    # Maße müssen positiv und endlich sein: NaN, inf, null oder negative Werte könnten spätere Größenvergleiche fälschlich bestehen lassen.
    for bbox in ([float("nan"), 19.0, 76.2], [19.0, float("inf"), 76.2], [0, 19.0, 76.2], [19.0, 19.0, -76.2]):
        bad = minimal_spec()
        bad["expected"]["bbox"] = bbox
        reject(bad, "bbox")


def test_duplicate_ids_rejected():
    # Je Gruppe ein Element, das eine ID aus einer anderen oder derselben Gruppe wiederverwendet.
    clashes = [
        ("views", {"id": "d1", "kind": "top", "scale": "1:1"}),  # Ansicht und Maß
        ("dimensions", minimal_spec()["dimensions"][0]),  # Maß und Maß
        ("features", {"id": "v1", "type": "other", "dim_ids": []}),  # Konstruktionsmerkmal und Ansicht
        ("notes", {"id": "f1", "text": "R1", "source": None}),  # Hinweis und Konstruktionsmerkmal
        ("review", review_item("d2")),  # Prüfeintrag und Maß
    ]
    for group, element in clashes:
        spec = minimal_spec()
        spec.setdefault(group, []).append(element)
        reject(spec, element["id"])


def test_unknown_ref_rejected():
    spec = minimal_spec()
    spec["review"] = [review_item(refs=("d1", "x9"))]
    reject(spec, "x9")
    spec = minimal_spec()
    spec["features"][0]["dim_ids"] = ["d1", "x9"]
    reject(spec, "x9")
    spec = minimal_spec()
    spec["expected"]["holes"] = [{"d": 19.0, "at": [0, 0, 0], "axis": [0, 0, 1], "dim_ids": ["x9"]}]
    reject(spec, "x9")
    spec = minimal_spec()
    spec["dimensions"][0]["source"]["view"] = "v9"
    reject(spec, "v9")
    # `refs` darf auf ein Maß, Konstruktionsmerkmal oder einen Hinweis verweisen, aber nicht auf eine Ansicht oder einen anderen Prüfeintrag.
    spec = minimal_spec()
    spec["notes"] = [{"id": "n1", "text": "Radien R1", "source": {"view": "v1", "cell": None}}]
    spec["review"] = [review_item(refs=("d1", "f1", "n1"), issue="ambiguous")]
    PartSpec.model_validate(spec)
    spec["review"] = [review_item(refs=("v1",))]
    reject(spec, "v1")
    spec["review"] = [review_item("r1"), review_item("r2", refs=("r1",))]
    reject(spec, "r2")


def test_low_confidence_requires_review():
    spec = minimal_spec()
    spec["dimensions"][0]["confidence"] = 0.7
    reject(spec, r"Dimension 'd1': `confidence` 0\.7 < 0\.8; a review item")  # nennt die tatsächliche Ursache
    spec["review"] = [review_item()]
    PartSpec.model_validate(spec)
    # Die Schwelle liegt strikt unter 0,8.
    spec["dimensions"][0]["confidence"] = 0.8
    spec["review"] = []
    PartSpec.model_validate(spec)
    spec["review"] = [review_item()]  # Ein Prüfvermerk liegt vor; diese Fälle scheitern daher nur bei einem Wert außerhalb von 0 bis 1.
    for confidence in (-0.1, 1.1):
        spec["dimensions"][0]["confidence"] = confidence
        reject(spec, "confidence")


def test_assumed_requires_basis_and_review():
    spec = minimal_spec()
    # Nirgends abgedruckt: Der Text bleibt leer und wird nicht mit dem Wert verglichen.
    spec["dimensions"].append({"id": "d3", "kind": "length", "value": 20.0, "text": "", "source": None, "confidence": 0.9, "assumed": True})
    reject(spec, "d3")  # Weder Begründungsgrundlage noch Prüfeintrag
    spec["review"] = [review_item(refs=("d3",), issue="missing")]
    reject(spec, "d3")  # Prüfeintrag ohne Begründungsgrundlage
    spec["dimensions"][2]["assumption_basis"] = "  "
    reject(spec, "d3")  # leere Begründungsgrundlage
    spec["dimensions"][2]["assumption_basis"] = "Breite entspricht dem Abstand der Bohrungen von 20 mm"
    PartSpec.model_validate(spec)
    spec["review"] = []
    reject(
        spec, r"Dimension 'd3': `assumed=true`; a review item"
    )  # Begründungsgrundlage vorhanden, aber kein Prüfeintrag; die Sicherheit reicht aus.
    # Prüfeinträge benötigen eine konkrete Annahme und Begründung.
    for field in ("assumption", "reason"):
        spec["review"] = [review_item(refs=("d3",), issue="missing", **{field: " "})]
        reject(spec, "r1")


def test_text_value_mismatch_rejected():
    spec = minimal_spec()
    spec["dimensions"][1]["value"] = 762.0  # „76,2“ wird als 762 gelesen.
    reject(spec, "d2")
    for text, value in [("76,2", 76.0), ("Ø", 6.0), ("", 6.0), ("2x Ø6,6", 66.0), ("M8", 6.8)]:
        spec = minimal_spec()
        spec["dimensions"][1].update(text=text, value=value)
        reject(spec, "d2")
    # Jede gedruckte Schreibweise muss einer der Zahlen entsprechen.
    for text, value in [
        ("76,2", 76.2),
        ("76.2", 76.2),
        ("2x Ø6,6", 6.6),
        ("2x Ø6,6", 2.0),
        ("Ø6 h10", 6.0),
        ("31 -0,1", 31.0),
        ("M8", 8.0),
        ("45°", 45.0),
    ]:
        spec = minimal_spec()
        spec["dimensions"][1].update(text=text, value=value)
        PartSpec.model_validate(spec)


def test_chamfer_first_number():
    spec = minimal_spec()
    spec["dimensions"].append(
        {"id": "d3", "kind": "chamfer", "value": 5.0, "text": "5x45°", "source": {"view": "v1", "cell": "D4"}, "confidence": 0.9}
    )
    PartSpec.model_validate(spec)
    spec["dimensions"][2]["value"] = 4.0
    reject(spec, "d3")


def test_bad_cell_rejected():
    for cell in ("K9", "A9", "A0", "G1", "a1", "B33", "B3\n", ""):
        spec = minimal_spec()
        spec["dimensions"][0]["source"]["cell"] = cell
        reject(spec, "cell")
    for cell in (None, "A1", "F8"):  # `None`: Zeichnungen ohne Raster
        spec = minimal_spec()
        spec["dimensions"][0]["source"]["cell"] = cell
        PartSpec.model_validate(spec)


def test_umlaut_name_ok():
    for name in ("Gehäuse_Ü", "Platte-B", "Teil_2"):
        spec = minimal_spec()
        spec["name"] = name
        assert PartSpec.model_validate(spec).name == name
    for name in ("", "a/b", "../x", "a b", "a.b", "Platte_A\n"):  # Der Name wird als Ordnername verwendet.
        spec = minimal_spec()
        spec["name"] = name
        reject(spec, "name")


def test_hole_feature_requires_expected_hole():
    spec = minimal_spec()
    spec["features"].append({"id": "f2", "type": "hole", "dim_ids": ["d1"]})
    reject(spec, "f2")
    hole = {"d": 19.0, "at": [0, 0, 0], "axis": [0, 0, 1], "dim_ids": ["d1"]}
    spec["expected"]["holes"] = [hole]
    PartSpec.model_validate(spec)
    # Eine Bohrung benötigt einen positiven Durchmesser, endliche Koordinaten und eine Achsrichtung.
    for changes, match in [({"d": 0}, r"holes\.0\.d"), ({"d": -6.6}, r"holes\.0\.d"), ({"at": [float("nan"), 0, 0]}, r"holes\.0\.at")]:
        spec["expected"]["holes"] = [{**hole, **changes}]
        reject(spec, match)
    spec["expected"]["holes"] = [hole, {**hole, "axis": [0, 0, 0]}]
    reject(spec, r"`expected\.holes\[1\]`: `axis`")


def test_tolerance_fields_validated():
    def with_dim(**changes) -> dict:
        spec = minimal_spec()
        spec["dimensions"][1].update(changes)
        return spec

    # „31 -0,1“: Die obere Abweichung 0 ist angegeben, nicht fehlend.
    PartSpec.model_validate(with_dim(tol_upper=0, tol_lower=-0.1))
    PartSpec.model_validate(with_dim(tol_upper=0.1, tol_lower=0))
    PartSpec.model_validate(with_dim(tol_upper=0.05, tol_lower=0.05))
    reject(with_dim(tol_lower=0.1, tol_upper=-0.1), "d2")
    reject(with_dim(tol_upper=0.1), "d2")
    reject(with_dim(tol_lower=-0.1), "d2")

    for fit in ("h10", "H7", "js6"):
        assert PartSpec.model_validate(with_dim(fit=fit)).dimensions[1].fit == fit
    for fit in ("10h", "h", "h100", "hhh7", "h 10", ""):
        reject(with_dim(fit=fit), "fit")

    for grade in "fmcv":
        spec = minimal_spec()
        spec["general_tolerance"] = f"ISO 2768-{grade}"
        PartSpec.model_validate(spec)
    for text in ("DIN ISO 2768-m", "ISO 2768-mK", "ISO 2768 - m", "DIN EN ISO 2768-mH", " ISO 2768-m\n"):
        spec = minimal_spec()
        spec["general_tolerance"] = text
        assert PartSpec.model_validate(spec).general_tolerance == "ISO 2768-m"
    for text in ("ISO 2768-x", "2768-m", "Allg.-Toleranz"):
        spec = minimal_spec()
        spec["general_tolerance"] = text
        reject(spec, "general_tolerance")


@pytest.mark.parametrize(
    ("kind", "value", "text", "expected"),
    [
        ("length", 77, "77 ±0,1", (0.1, -0.1, None)),
        ("length", 35, "35±0,1", (0.1, -0.1, None)),
        ("length", 57, "57 -0,1", (0.0, -0.1, None)),
        ("diameter", 14, "Ø14 +0,1", (0.1, 0.0, None)),
        ("length", 12, "12 +0,4 -0,1", (0.4, -0.1, None)),
        ("radius", 30.75, "R 30,75 +0,05", (0.05, 0.0, None)),
        ("diameter", 30, "Ø30 H10L", (None, None, "H10")),
        ("diameter", 6, "4x Ø6 h10", (None, None, "h10")),
        # Fasen und Gewinde tragen keine Abmaße.
        ("chamfer", 2, "2 x 45°", (None, None, None)),
        ("thread", 12, "M12", (None, None, None)),
        ("length", 113, "113", (None, None, None)),
    ],
)
def test_tolerance_is_taken_from_the_printed_text(kind, value, text, expected):
    spec = minimal_spec()
    spec["dimensions"][1].update(kind=kind, value=value, text=text)
    dim = PartSpec.model_validate(spec).dimensions[1]
    assert (dim.tol_upper, dim.tol_lower, dim.fit) == expected


def test_entered_tolerance_wins_over_the_text():
    spec = minimal_spec()
    spec["dimensions"][1].update(kind="length", value=77, text="77 ±0,1", fit="js9")
    dim = PartSpec.model_validate(spec).dimensions[1]
    assert (dim.tol_upper, dim.tol_lower, dim.fit) == (None, None, "js9")


def test_deviation_of_another_number_is_not_taken():
    # "50 -0,1" als halbes Maß für den Wert 100: Die Abweichung gehört zur 50.
    assert tolerance_in_text("50 -0,1", 100.0) == {}
    assert tolerance_in_text("39 / 42 -0,2", 81.0) == {}


def test_source_defaults_to_none():
    spec = minimal_spec()
    del spec["dimensions"][0]["source"]
    assert PartSpec.model_validate(spec).dimensions[0].source is None


def test_computed_value_message_names_the_way_out():
    spec = minimal_spec()
    spec["dimensions"][1].update(text="50 + 50", value=100.0)
    with pytest.raises(ValueError) as caught:
        PartSpec.model_validate(spec)
    message = str(caught.value)
    assert "d2" in message and "assumed=true" in message and "assumption_basis" in message and "calculated" in message


def test_collects_all_inconsistencies():
    """Charakterisierung: Verstöße aus verschiedenen Prüfungen erscheinen gemeinsam in einer Meldung, in fester Reihenfolge."""
    spec = minimal_spec()
    spec["views"].append({"id": "d1", "kind": "top", "scale": "1:1"})  # doppelte ID
    spec["features"][0]["dim_ids"] = ["x9"]  # unbekannte Referenz
    spec["review"] = [review_item(reason=" ")]  # leere Begründung
    spec["dimensions"][0]["confidence"] = 0.5  # niedrige Konfidenz; `r1` verweist darauf, daher geprüft
    spec["dimensions"][1]["value"] = 762.0  # Wert nicht im gedruckten Text
    spec["features"].append({"id": "f2", "type": "hole", "dim_ids": ["d1"]})  # Bohrung ohne `expected.holes`
    with pytest.raises(ValueError) as caught:
        PartSpec.model_validate(spec)
    message = str(caught.value)
    expected = ["Duplicate ID 'd1'", "unknown `dim_id` 'x9'", "`reason` is empty", "Dimension 'd2': `text`", "Hole features ['f2']"]
    positions = [message.find(part) for part in expected]
    assert -1 not in positions and positions == sorted(positions)


def test_mark_near_accepts_a_list_of_points_and_wraps_a_flat_triple():
    """Die in den Läufen häufigste Fehlform, eine flache Dreierliste, ist ein einzelner Punkt und wird verpackt."""
    for given, want in (([[0, 1, 2], [3, 4, 5]], [(0, 1, 2), (3, 4, 5)]), ([0, -19.5, 175], [(0, -19.5, 175)]), ([], [])):
        spec = minimal_spec()
        spec["review"] = [review_item(mark_near=given)]
        assert PartSpec.model_validate(spec).review[0].mark_near == want


def test_mark_near_rejects_other_forms_with_the_right_one():
    for bad in ({"x": 0, "y": 1, "z": 2}, [0, 1], [[0, 1, 2], 3], [0, 1, 2, 3], "0,1,2"):
        spec = minimal_spec()
        spec["review"] = [review_item(mark_near=bad)]
        with pytest.raises(ValueError) as e:
            PartSpec.model_validate(spec)
        message = str(e.value)
        assert "mark_near" in message
        if not isinstance(bad, str):  # ein String kommt bei der Typprüfung heraus, nicht beim Validator
            assert "[[0, -19.5, 175]]" in message


def test_scale_is_normalized_to_a_colon_ratio():
    for given, want in (
        ("1/2", "1:2"),
        (" 2 : 1 ", "2:1"),
        ("1,5/1", "1,5:1"),
        ("1:1", "1:1"),
        ("M 1:2", "M 1:2"),
        ("wie gezeigt", "wie gezeigt"),
    ):
        spec = minimal_spec() | {"scale": given}
        spec["views"][0]["scale"] = given
        parsed = PartSpec.model_validate(spec)
        assert (parsed.scale, parsed.views[0].scale) == (want, want)


def with_geometric(**tolerance) -> dict:
    """Minimalspezifikation mit Bezug A auf der Unterseite und einer Parallelität der Oberseite zu A; tolerance ändert die Toleranz."""
    spec = minimal_spec()
    spec["datums"] = [{"id": "g1", "label": "A", "near": [0, 0, 0]}]
    spec["geometric_tolerances"] = [
        {"id": "g2", "kind": "parallelism", "value": 0.05, "text": "// 0.05 A", "datums": ["A"], "near": [0, 0, 76.2], "confidence": 0.9}
        | tolerance
    ]
    return spec


def test_geometric_tolerance_with_datum_is_valid():
    spec = PartSpec.model_validate(with_geometric())
    assert spec.geometric_tolerances[0].datums == ["A"] and spec.datums[0].label == "A"


@pytest.mark.parametrize(
    "change, match",
    [
        ({"datums": ["B"]}, "unknown datum 'B'"),
        ({"datums": []}, "parallelism needs at least one datum"),
        ({"kind": "flatness"}, "form tolerance flatness has no datum"),
        ({"text": "// 0.5 A"}, "contains no number matching `value` 0.05"),
        ({"confidence": 0.5}, "a review item must list it in `refs`"),
        ({"id": "d1"}, "Duplicate ID 'd1'"),
        ({"kind": "wobble"}, "kind"),
    ],
)
def test_geometric_tolerance_rules(change, match):
    reject(with_geometric(**change), match)


def test_low_confidence_geometric_tolerance_can_be_reviewed():
    spec = with_geometric(confidence=0.5)
    spec["review"] = [review_item(refs=("g2",))]
    PartSpec.model_validate(spec)


def test_datum_label_once_and_review_may_name_it():
    spec = with_geometric()
    spec["datums"].append({"id": "g3", "label": "A", "near": [1, 1, 1]})
    reject(spec, "Datum 'A' is defined more than once")
    spec = with_geometric()
    spec["review"] = [review_item(refs=("g1",))]
    PartSpec.model_validate(spec)


@pytest.mark.parametrize(
    "dim, points",
    [(0, [[0, 0, 0], [0, 0, 76.2]]), (1, [[0, 0, 0]]), (1, [[0, 0, 0], [0, 0, 1], [0, 0, 2]])],
)
def test_faces_near_only_for_lengths_with_two_points(dim, points):
    spec = minimal_spec()
    spec["dimensions"][dim]["faces_near"] = points
    reject(spec, "`faces_near` exists only for lengths")


def test_faces_near_on_a_length():
    spec = minimal_spec()
    spec["dimensions"][1]["faces_near"] = [[0, 0, 0], [0, 0, 76.2]]
    assert PartSpec.model_validate(spec).dimensions[1].faces_near == [(0, 0, 0), (0, 0, 76.2)]


def test_note_from_the_title_block_needs_no_view():
    spec = minimal_spec()
    spec["notes"] = [{"id": "n1", "text": "Rz 50", "source": {"view": "title", "cell": None}}]
    assert PartSpec.model_validate(spec).notes[0].source.view == "title"

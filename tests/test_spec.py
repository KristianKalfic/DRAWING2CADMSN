import pytest

from d2c.spec import PartSpec


def minimal_spec() -> dict:
    """Smallest valid spec as a plain dict, fresh on every call, so a test breaks exactly one rule."""
    return {
        "name": "Flansch",
        "drawing": "Flansch/Flansch.pdf",
        "title": "Flansch",
        "scale": "1:1",
        "views": [{"id": "v1", "kind": "front", "scale": "1:1"}],
        "dimensions": [
            {"id": "d1", "kind": "diameter", "value": 19.0, "text": "Ø19",
             "source": {"view": "v1", "cell": "B3"}, "confidence": 0.95},
            {"id": "d2", "kind": "length", "value": 76.2, "text": "76,2",
             "source": {"view": "v1", "cell": "C3"}, "confidence": 0.9},
        ],
        "features": [{"id": "f1", "type": "base", "dim_ids": ["d1", "d2"]}],
        "expected": {"bbox": [19.0, 19.0, 76.2]},
        "review": [],
    }


def review_item(item_id: str = "r1", refs: tuple[str, ...] = ("d1",), **changes) -> dict:
    return {"id": item_id, "refs": list(refs), "issue": "low_confidence",
            "assumption": "diameter 19 as printed", "reason": "digits are blurry", **changes}


def reject(spec: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        PartSpec.model_validate(spec)


def test_minimal_valid():
    spec = PartSpec.model_validate(minimal_spec())
    assert (spec.page, spec.units, spec.notes, spec.general_tolerance) == (1, "mm", [], None)
    assert spec.expected.bbox == (19.0, 19.0, 76.2)
    assert PartSpec.model_validate_json(spec.model_dump_json()) == spec  # spec.json round trip
    # extra="forbid" on the root and on nested models
    reject({**minimal_spec(), "colour": "red"}, "colour")
    nested = minimal_spec()
    nested["dimensions"][0]["colour"] = "red"
    reject(nested, "colour")
    reject({**minimal_spec(), "units": "inch"}, "units")
    # sizes are positive and finite: NaN, inf, zero or negative would let later size comparisons pass silently
    for bbox in ([float("nan"), 19.0, 76.2], [19.0, float("inf"), 76.2], [0, 19.0, 76.2], [19.0, 19.0, -76.2]):
        bad = minimal_spec()
        bad["expected"]["bbox"] = bbox
        reject(bad, "bbox")


def test_duplicate_ids_rejected():
    # one element per group that reuses an id of another group (or of its own group)
    clashes = [
        ("views", {"id": "d1", "kind": "top", "scale": "1:1"}),  # view vs dimension
        ("dimensions", minimal_spec()["dimensions"][0]),  # dimension vs dimension
        ("features", {"id": "v1", "type": "other", "dim_ids": []}),  # feature vs view
        ("notes", {"id": "f1", "text": "R1", "source": None}),  # note vs feature
        ("review", review_item("d2")),  # review item vs dimension
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
    spec = minimal_spec()
    spec["notes"] = [{"id": "n1", "text": "Radien R1", "source": {"view": "v9", "cell": None}}]
    reject(spec, "v9")
    # a review ref may name a dimension, a feature or a note, but not a view or another review item
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
    reject(spec, r"Maß 'd1': `confidence` 0\.7 < 0\.8; ein Prüfeintrag")  # names the real cause
    spec["review"] = [review_item()]
    PartSpec.model_validate(spec)
    # the threshold is strictly below 0.8
    spec["dimensions"][0]["confidence"] = 0.8
    spec["review"] = []
    PartSpec.model_validate(spec)
    spec["review"] = [review_item()]  # reviewed, so only the 0..1 range can reject these
    for confidence in (-0.1, 1.1):
        spec["dimensions"][0]["confidence"] = confidence
        reject(spec, "confidence")


def test_assumed_requires_basis_and_review():
    spec = minimal_spec()
    # printed nowhere, so the text is empty and is not compared with the value
    spec["dimensions"].append({"id": "d3", "kind": "length", "value": 20.0, "text": "",
                               "source": None, "confidence": 0.9, "assumed": True})
    reject(spec, "d3")  # neither basis nor review
    spec["review"] = [review_item(refs=("d3",), issue="missing")]
    reject(spec, "d3")  # review but no basis
    spec["dimensions"][2]["assumption_basis"] = "  "
    reject(spec, "d3")  # blank basis
    spec["dimensions"][2]["assumption_basis"] = "width equals the 20 mm bore pitch"
    PartSpec.model_validate(spec)
    spec["review"] = []
    reject(spec, r"Maß 'd3': `assumed=true`; ein Prüfeintrag")  # basis but no review, confidence is fine
    # review entries need a real assumption and reason
    for field in ("assumption", "reason"):
        spec["review"] = [review_item(refs=("d3",), issue="missing", **{field: " "})]
        reject(spec, "r1")


def test_text_value_mismatch_rejected():
    spec = minimal_spec()
    spec["dimensions"][1]["value"] = 762.0  # "76,2" read as 762
    reject(spec, "d2")
    for text, value in [("76,2", 76.0), ("Ø", 6.0), ("", 6.0), ("2x Ø6,6", 66.0), ("M8", 6.8)]:
        spec = minimal_spec()
        spec["dimensions"][1].update(text=text, value=value)
        reject(spec, "d2")
    # every printed form must match one of its numbers
    for text, value in [("76,2", 76.2), ("76.2", 76.2), ("2x Ø6,6", 6.6), ("2x Ø6,6", 2.0),
                        ("Ø6 h10", 6.0), ("31 -0,1", 31.0), ("M8", 8.0), ("45°", 45.0)]:
        spec = minimal_spec()
        spec["dimensions"][1].update(text=text, value=value)
        PartSpec.model_validate(spec)


def test_chamfer_first_number():
    spec = minimal_spec()
    spec["dimensions"].append({"id": "d3", "kind": "chamfer", "value": 5.0, "text": "5x45°",
                               "source": {"view": "v1", "cell": "D4"}, "confidence": 0.9})
    PartSpec.model_validate(spec)
    spec["dimensions"][2]["value"] = 4.0
    reject(spec, "d3")


def test_bad_cell_rejected():
    for cell in ("K9", "A9", "A0", "G1", "a1", "B33", "B3\n", ""):
        spec = minimal_spec()
        spec["dimensions"][0]["source"]["cell"] = cell
        reject(spec, "cell")
    for cell in (None, "A1", "F8"):  # None: drawings without a grid
        spec = minimal_spec()
        spec["dimensions"][0]["source"]["cell"] = cell
        PartSpec.model_validate(spec)


def test_umlaut_name_ok():
    for name in ("Trägerplatte", "3-Loch-Hebel", "Teil_2"):
        spec = minimal_spec()
        spec["name"] = name
        assert PartSpec.model_validate(spec).name == name
    for name in ("", "a/b", "../x", "a b", "a.b", "Flansch\n"):  # the name becomes a folder name
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
    # a hole needs a positive diameter, finite coordinates and an axis direction
    for changes, match in [({"d": 0}, r"holes\.0\.d"), ({"d": -6.6}, r"holes\.0\.d"),
                           ({"at": [float("nan"), 0, 0]}, r"holes\.0\.at")]:
        spec["expected"]["holes"] = [{**hole, **changes}]
        reject(spec, match)
    spec["expected"]["holes"] = [hole, {**hole, "axis": [0, 0, 0]}]
    reject(spec, r"`expected\.holes\[1\]`: `axis`")


def test_tolerance_fields_validated():
    def with_dim(**changes) -> dict:
        spec = minimal_spec()
        spec["dimensions"][1].update(changes)
        return spec

    # "31 -0,1": an upper deviation of 0 is set, not missing
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
    for text in ("ISO 2768-x", "ISO 2768-mK", "2768-m", "ISO 2768-m\n"):
        spec = minimal_spec()
        spec["general_tolerance"] = text
        reject(spec, "general_tolerance")

"""Maßstabslogik: Schriftfeld-Verhältnis, Hinweise zu measure_drawing und Maßstabsprüfung ohne Vektorgeometrie."""

import pytest

from d2c.drawing import scale
from d2c.spec import PartSpec


def spec_with(scale_text: str, values: list[float]) -> PartSpec:
    dims = [
        {"id": f"d{k}", "kind": "length", "value": v, "text": f"{v:g}", "source": None, "confidence": 0.95} for k, v in enumerate(values)
    ]
    return PartSpec.model_validate(
        {
            "name": "sheet",
            "drawing": "gibt-es-nicht.pdf",
            "title": "Sheet",
            "scale": scale_text,
            "views": [],
            "features": [],
            "dimensions": dims,
            "expected": {"bbox": [80, 50, 10]},
            "review": [],
        }
    )


@pytest.mark.parametrize(
    ("text", "ratio"),
    [
        ("1:1", 1.0),
        ("2:1", 2.0),
        ("1:2", 0.5),
        ("2.5:1", 2.5),
        ("1 : 2,5", 0.4),
        ("Maßstab 5:1", 5.0),
        ("1/2", 0.5),
        ("2/1", 2.0),
        ("2 : 1", 2.0),
        ("M 1/2", 0.5),
        ("1,5 / 1", 1.5),
        ("as shown", None),
        ("0:1", None),
        ("1:0", None),
        ("", None),
    ],
)
def test_title_scale(text, ratio):
    assert scale.title_scale(text) == ratio


def test_scale_notes_without_scale_and_with_few_values():
    notes = scale.scale_notes(None, [{"scale": 1.0, "votes": 2}], 2)
    assert len(notes) == 2
    assert "Scale not given" in notes[0] and "unreliable" in notes[1]


def test_scale_notes_deviating_title_scale():
    notes = scale.scale_notes(2.0, [{"scale": 1.0, "votes": 6}], 6)
    assert len(notes) == 1 and "given 2, geometry gives 1 (6 of 6" in notes[0]


def test_scale_notes_agreeing_scale_and_no_match():
    assert scale.scale_notes(1.0, [{"scale": 1.003, "votes": 6}], 6) == []
    assert scale.scale_notes(1.0, [], 4) == ["No scale matches `printed_dims`."]
    assert scale.scale_notes(1.0, [], 0) == []


def test_scale_check_without_a_ratio():
    check = scale.scale_check(spec_with("as shown", [80, 50, 60, 30, 16, 10]))
    assert check["title_block"] is None and check["conflict"] is False
    assert "not a ratio" in check["note"]


def test_scale_check_needs_six_distinct_values():
    check = scale.scale_check(spec_with("1:1", [80, 80, 50, 50, 30, 16]))  # sechs Werte, aber nur vier verschiedene
    assert (check["title_block"], check["best"], check["votes"], check["conflict"]) == (1.0, None, 0, False)
    assert "only 4 distinct" in check["note"] and f"{scale.MIN_PRINTED_DIMS} are needed" in check["note"]


def test_title_block_ratio_prefers_the_title_block():
    items = [{"text": "Detail 5:1", "y0": 0.3}, {"text": "Maßstab 2:1", "y0": 0.9}, {"text": "80", "y0": 0.5}]
    assert scale.title_block_ratio(items) == "2:1"
    assert scale.title_block_ratio(items[:1]) == "5:1"
    assert scale.title_block_ratio(items[2:]) is None

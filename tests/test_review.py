"""Prüfliste ohne Aufbau: Abschnitte, die aus Daten gerendert werden."""

from helpers import demo_spec

from d2c.quality import review
from d2c.quality.review import _dims_found_section, _script_numbers, _tolerances_section
from d2c.spec import PartSpec


def tolerance(source: str, nominal: float, lower: float, upper: float) -> dict:
    return {
        "dim_id": "d1",
        "text": "Ø68 +0,2 0",
        "nominal": nominal,
        "lower": lower,
        "upper": upper,
        "mid": nominal + (lower + upper) / 2,
        "source": source,
        "in_tolerance_nominal": True,
        "in_tolerance_mid": None,
    }


def test_tolerance_table_shows_limit_sizes_not_deviations():
    """Mindest- und Höchstmaß sind Nennmaß + Abmaß (68 und 68,2); die Abmaße stehen in eigenen Spalten."""
    rows = {"tolerances": [tolerance("explicit", 68, 0.0, 0.2), tolerance("fit", 10, -0.009, 0.0), tolerance("general", 80, -0.3, 0.3)]}
    table = _tolerances_section(rows).splitlines()
    assert table[3] == "| d1 | Ø68 +0,2 0 | 68 | 68 | 68.2 | 68.1 | on dimension | 0 | 0.2 | yes | not checked |"
    assert "| 10 | 9.991 | 10 |" in table[4] and "| fit ISO 286 | -0.009 | 0 |" in table[4]
    assert "| 79.7 | 80.3 |" in table[5] and "general tolerance ISO 2768" in table[5]
    assert "explicit" not in "\n".join(table)


def test_dims_found_section_lists_missing_with_id_text_value():
    found = {"found": [{"id": "a", "text": "5", "value": 5, "nearest": 5}], "missing": [{"id": "b", "text": "68,5", "value": 68.5}]}
    section = _dims_found_section({"dims_found": found})
    assert "Found in model: 1 of 2" in section
    assert section.endswith("| Dimension | Printed text | Value |\n|---|---|---|\n| b | 68,5 | 68.5 |")
    assert "Not determined" in _dims_found_section({"dims_found": None})


def test_script_numbers_by_line_and_sign():
    code = "a = 3.5\nb = [1, -2.5, True, 0]\nresult = 'x' * 4\n"
    assert _script_numbers(code) == [(1, 3.5), (2, 1), (2, -2.5), (3, 4)]
    assert _script_numbers("def (") == []


def test_unreferenced_script_ignores_dimension_values():
    """Ein Skript wird nicht über Maßwerte entschuldigt: auch ein Literal mit dem Wert eines Maßes steht in der Liste."""
    spec = PartSpec.model_validate(demo_spec())
    found = review.unreferenced(spec, [{"op": "script", "code": "result = 20 + 7"}])
    assert [(u["path"], u["value"]) for u in found] == [("Line 1", 20), ("Line 1", 7)]


def test_geometric_section_tells_which_tolerances_are_in_the_step_file():
    spec = PartSpec.model_validate(
        demo_spec()
        | {
            "datums": [{"id": "ga", "label": "A", "near": (0, 0, 0)}],
            "geometric_tolerances": [
                {"id": "gf", "kind": "flatness", "value": 0.02, "text": "0,02", "near": (0, 0, 10), "confidence": 0.9},
                {
                    "id": "gp",
                    "kind": "parallelism",
                    "value": 0.05,
                    "text": "0,05 A",
                    "datums": ["A"],
                    "near": (0, 0, 25),
                    "confidence": 0.9,
                },
            ],
        }
    )
    rows = review.geometric(spec, {"roundtrip": {"pmi": {"gdt_ids": ["gp"]}}})
    assert [(r["id"], r["datum_ids"], r["in_step"]) for r in rows] == [("gf", [], False), ("gp", ["ga"], True)]
    section = review._geometric_section({"geometric_tolerances": rows})
    assert "| gp | parallelism | 0.05 | A | 0,05 A | yes |" in section
    assert review._geometric_section({"geometric_tolerances": []}).endswith("None.")

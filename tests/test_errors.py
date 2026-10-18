"""Verständliche Fehlertexte aus pydantic-Fehlern (d2c.errors): erhaltener Wert, Beispiel und Reparaturhinweis."""

import pytest
from helpers import OPS, demo_spec
from pydantic import ValidationError

from d2c.cad.ops import EdgeFinishOp, ExtrudeOp, HolesOp
from d2c.errors import validation_error_message
from d2c.spec import PartSpec


def box(**changes) -> dict:
    """Eine Extrude-Operation: Quader 20 × 10 auf XY von z=0 bis z=5."""
    return {
        "op": "extrude",
        "profile": {"type": "rect", "center": (0, 0), "w": 20.0, "h": 10.0},
        "plane": {"name": "XY"},
        "distance": 5.0,
    } | changes


@pytest.mark.parametrize(
    "changes, details",
    [
        ({"profile": {"type": "path", "start": [0, 0], "segments": []}}, ("segments", "at least 1", "too_short")),
        ({"profile": {"type": "hull", "circles": [[0, 0, 2]]}}, ("circles", "at least 2", "too_short")),
        ({"profile": {"type": "circle", "center": [0, 0, 0], "d": 5}}, ("center", "at most 2", "too_long")),
        ({"profile": {"type": "triangle"}}, ("triangle", "type", "rect", "circle", "slot", "path", "hull", "union_tag_invalid")),
        ({"profile": {}}, ("profile", "type field", "type", "union_tag_not_found")),
        ({"both": "vielleicht"}, ("both", "true", "false", "bool_parsing")),
    ],
)
def test_validation_errors_keep_repair_details(changes, details):
    with pytest.raises(ValidationError) as caught:
        OPS.validate_python([box() | changes])
    message = validation_error_message(caught.value)
    assert all(detail in message for detail in details), message


def message_for(model, data):
    with pytest.raises(ValidationError) as caught:
        model.model_validate(data)
    return validation_error_message(caught.value)


@pytest.mark.parametrize(
    "model, changes, hint, expected",
    [
        (HolesOp, {"op": "holes", "d": 6.6, "positions": [[0, 0]]}, "plane", '{"name": "XY", "offset": '),
        (HolesOp, {"op": "holes", "plane": {"name": "XY"}, "d": "Ø6.6", "positions": [[0, 0]]}, "d", "the number only"),
        (
            HolesOp,
            {"op": "holes", "plane": {"name": "XY"}, "d": 6.6, "positions": [[0, 0]], "depth": 0},
            "depth",
            "omit `depth` for a through hole",
        ),
        (ExtrudeOp, box(distance=0), "distance", "negative = against the normal"),
        (EdgeFinishOp, {"op": "edge_finish", "kind": "fillet", "size": 1, "near": [1, 2, 3]}, "near", "[[x, y, z]]"),
    ],
)
def test_validation_message_has_received_value_and_example(model, changes, hint, expected):
    base = demo_spec() if model is PartSpec else {}
    message = message_for(model, base | changes)
    assert "received" in message and "Example" in message and expected in message, message


def test_validation_message_shows_received_value_shortened():
    message = message_for(HolesOp, {"op": "holes", "plane": {"name": "XY"}, "d": "x" * 200, "positions": [[0, 0]]})
    received = message.split("received: ")[1].split(")")[0]
    assert received.startswith("'xxx") and len(received) <= 60


def test_validation_message_limits_rows():
    holes = [{"d": 5, "at": [0, 0], "axis": [0, 0], "dim_ids": []} for _ in range(7)]  # je Bohrung zwei Fehler: at und axis
    spec = demo_spec() | {"expected": {"bbox": [1, 1, 1], "holes": holes}}
    message = message_for(PartSpec, spec)
    assert message.count("tuple_type") <= 12 and "more errors" in message

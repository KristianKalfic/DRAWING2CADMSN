"""Tolerante Eingabeschicht: Umdeutung von Formfehlern, Leerlauf bei gültigen Eingaben."""

import copy
import json

import pytest
from helpers import demo_ops, demo_spec
from pydantic import ValidationError

from d2c.cad.ops import EdgeFinishOp, ExtrudeOp, HolesOp, RevolveOp, ScriptOp
from d2c.errors import validation_error_message
from d2c.server import coerce
from d2c.spec import PartSpec

BBOX = [-40, -25, 0, 40, 25, 25]


def spec() -> dict:
    return demo_spec()


def holes(**changes) -> dict:
    return {"part": "demo_agent", "plane": {"name": "XY"}, "d": 16, "positions": [[0, 0]]} | changes


def ignored(notes: list[str]) -> list[str]:
    return [n for n in notes if "ignored" in n]


# --- Rote Fälle aus den Protokollen ---


def test_printed_dims_text_without_json_is_split():
    args, notes = coerce.normalize("measure_drawing", {"pdf": "x.pdf", "printed_dims": "[25 30 50 60 80 16 30 6.6]"})
    assert args["printed_dims"] == [25, 30, 50, 60, 80, 16, 30, 6.6]
    assert len(notes) == 1 and "printed_dims" in notes[0]


@pytest.mark.parametrize(
    "printed",
    [
        [80, 30, 50, 60, 25, "R5", "Ø30", "Ø16H7", "4xØ6.6"],
        "[80 30 50 60 25 R5 Ø30 Ø16H7 4xØ6.6]",
        '[80, 30, 50, 60, 25, "R5", "Ø30", "Ø16H7", "4xØ6.6"]',
    ],
)
def test_printed_dims_dimension_texts_become_numbers(printed):
    args, notes = coerce.normalize("measure_drawing", {"pdf": "x.pdf", "printed_dims": printed})
    assert args["printed_dims"] == [80, 30, 50, 60, 25, 5, 30, 16, 6.6]
    assert notes


def test_flat_mark_near_becomes_point_list():
    data = spec() | {
        "review": [
            {"id": "r1", "refs": ["d1"], "issue": "convention", "assumption": "a", "reason": "b", "mark_near": [0, -24, 10]},
            {"id": "r2", "refs": ["d1"], "issue": "convention", "assumption": "a", "reason": "b", "mark_near": [30, 20, 4]},
        ]
    }
    args, notes = coerce.normalize("submit_spec", {"spec": data})
    assert [r["mark_near"] for r in args["spec"]["review"]] == [[[0, -24, 10]], [[30, 20, 4]]]
    assert any("mark_near" in n for n in notes)


def test_datum_and_tolerance_near_stay_single_points():
    data = spec() | {
        "datums": [{"id": "A", "label": "A", "near": [110, 105, 8.5]}],
        "geometric_tolerances": [
            {"id": "g1", "kind": "flatness", "value": 0.03, "text": "0.03", "near": "[75, 45, 9]", "confidence": 0.9},
            {
                "id": "g2",
                "kind": "parallelism",
                "value": 0.05,
                "text": "0.05 A",
                "datums": ["A"],
                "near": [[75, 45, 15]],
                "confidence": 0.9,
            },
        ],
    }
    args, notes = coerce.normalize("submit_spec", {"spec": data})
    assert args["spec"]["datums"][0]["near"] == [110, 105, 8.5]
    assert [t["near"] for t in args["spec"]["geometric_tolerances"]] == [[75, 45, 9], [75, 45, 15]]
    assert any("geometric_tolerances.1.near" in n for n in notes)
    PartSpec.model_validate(args["spec"])


def test_flat_list_is_split_into_points():
    args, notes = coerce.normalize("add_holes", holes(positions=[1, 2, 3, 4, 5, 6]))
    assert args["positions"] == [[1, 2], [3, 4], [5, 6]]
    assert notes


def test_flat_list_with_wrong_length_stays():
    args, _ = coerce.normalize("add_holes", holes(positions=[1, 2, 3]))
    assert args["positions"] == [1, 2, 3]


def test_mixed_flat_and_paired_points_are_grouped():
    """Aus dem Protokoll eines lokalen Modells: `revolve` mit einem flachen ersten Punkt vor Punktpaaren."""
    args, notes = coerce.normalize("revolve", {"part": "p", "points": [0, 0, [5, 0], [0, 25]]})
    assert args["points"] == [[0, 0], [5, 0], [0, 25]]
    assert len(notes) == 1 and "points" in notes[0]


def test_mixed_points_inside_edit_op_and_runs_in_the_middle():
    args, _ = coerce.normalize("edit_op", {"part": "p", "index": 0, "op": {"op": "revolve", "points": [[0, 0], 5, 0, 5, 30]}})
    assert args["op"]["points"] == [[0, 0], [5, 0], [5, 30]]
    args, _ = coerce.normalize("edge_finish", {"part": "p", "kind": "fillet", "size": 1, "near": [1, 2, 3, [4, 5, 6]]})
    assert args["near"] == [[1, 2, 3], [4, 5, 6]]


@pytest.mark.parametrize("points", [[0, 0, 1, [5, 0]], [0, 0, [5, 0, 1]], [0, 0, [5, "x"]], [0, 0, {"a": 1}]])
def test_mixed_points_with_wrong_groups_stay(points):
    args, notes = coerce.normalize("revolve", {"part": "p", "points": points})
    assert args["points"] == points and notes == []


def test_issue_alias():
    data = spec()
    data["review"] = [{"id": "r1", "refs": ["d1"], "issue": "assumed", "assumption": "a", "reason": "b"}]
    args, notes = coerce.normalize("check_spec", {"spec": data})
    assert args["spec"]["review"][0]["issue"] == "missing"
    assert "issue" in notes[0] and "missing" in notes[0]


def test_add_holes_demo_call_from_the_log():
    call = {"part": "demo_agent", "plane": {"name": "XY"}, "d": 16, "points": [[0, 0]], "through": True, "dim_ids": ["d_bore"]}
    args, notes = coerce.normalize("add_holes", call)
    assert args == {"part": "demo_agent", "plane": {"name": "XY"}, "d": 16, "positions": [[0, 0]]}
    assert any("through" in n for n in notes)
    assert any("dim_ids" in n and "refs" in n for n in ignored(notes))


@pytest.mark.parametrize("depth", [0, "0", 0.0])
def test_depth_zero_is_dropped(depth):
    args, notes = coerce.normalize("add_holes", holes(depth=depth))
    assert "depth" not in args and any("depth" in n for n in notes)


def test_positive_depth_stays():
    args, notes = coerce.normalize("add_holes", holes(depth=4))
    assert args["depth"] == 4 and notes == []


def test_add_holes_without_plane_uses_top_face():
    call = holes()
    del call["plane"]
    args, notes = coerce.normalize("add_holes", call, BBOX)
    assert args["plane"] == {"name": "XY", "offset": 25}
    assert any("plane" in n for n in notes)


def test_add_holes_without_plane_and_bbox_stays_invalid():
    call = holes()
    del call["plane"]
    args, notes = coerce.normalize("add_holes", call)
    assert "plane" not in args and notes == []


@pytest.mark.parametrize("text, scale", [("2:1", 2.0), ("1:2", 0.5), ("1:2,5", 0.4), ("2", 2.0), ("0,5", 0.5)])
def test_measure_drawing_scale_text(text, scale):
    args, notes = coerce.normalize("measure_drawing", {"pdf": "x.pdf", "scale": text})
    assert args["scale"] == scale and notes


# --- Zahlen, JSON-Text, Aliasse ---


def test_number_texts_use_comma_and_degree_sign():
    call = holes(d="6,6", plane={"name": "XY", "offset": "25"}, csk_angle="90°")
    args, _ = coerce.normalize("add_holes", call)
    assert (args["d"], args["plane"]["offset"], args["csk_angle"]) == (6.6, 25, 90)


def test_dimension_text_and_text_fields_are_kept():
    data = spec()
    data["dimensions"][0] |= {"value": "Ø16", "text": "Ø16 H7"}
    args, notes = coerce.normalize("submit_spec", {"spec": data})
    assert args["spec"]["dimensions"][0]["value"] == 16 and args["spec"]["dimensions"][0]["text"] == "Ø16 H7"
    assert len(notes) == 1


def test_non_numeric_text_stays_for_validation():
    args, notes = coerce.normalize("add_holes", holes(d="groß"))
    assert args["d"] == "groß" and notes == []


def test_nested_json_text_is_parsed():
    call = holes(positions="[[0, 0], [5, 5]]", plane='{"name": "XY", "offset": 3}')
    args, notes = coerce.normalize("add_holes", call)
    assert args["positions"] == [[0, 0], [5, 5]] and args["plane"] == {"name": "XY", "offset": 3}
    assert len(notes) == 2


def test_whole_spec_as_json_text():
    args, notes = coerce.normalize("submit_spec", {"spec": json.dumps(spec())})
    assert args["spec"] == spec() and notes


def test_code_text_is_never_parsed():
    call = {"part": "p", "code": "[1, 2]"}
    assert coerce.normalize("run_script", call) == (call, [])


def test_refs_ids_are_never_converted():
    call = holes(refs={"d": "5", "positions.0.0": "d1"})
    assert coerce.normalize("add_holes", call) == (call, [])


def test_add_holes_aliases():
    args, notes = coerce.normalize("add_holes", {"part": "p", "plane": "xy", "diameter": 6.6, "points": [[1, 2]]})
    assert args == {"part": "p", "plane": {"name": "XY"}, "d": 6.6, "positions": [[1, 2]]}
    assert len(notes) == 3


def test_custom_plane_name_stays_lowercase():
    plane = {"name": "custom", "origin": [0, 0, 0], "normal": [0, 0, 1]}
    assert coerce.normalize("add_holes", holes(plane=plane)) == (holes(plane=plane), [])


def test_extrude_aliases():
    call = {
        "part": "p",
        "profile": {"type": "rectangle", "center": [0, 0], "width": 80, "height": 50},
        "plane": "XY",
        "height": 10,
        "mode": "subtract",
    }
    args, _ = coerce.normalize("extrude", call)
    assert args == {
        "part": "p",
        "profile": {"type": "rect", "center": [0, 0], "w": 80, "h": 50},
        "plane": {"name": "XY"},
        "distance": 10,
        "mode": "cut",
    }


def test_thickness_and_union_aliases():
    args, _ = coerce.normalize(
        "extrude",
        {
            "part": "p",
            "profile": {"type": "circle", "center": [0, 0], "radius": 5},
            "plane": {"name": "XY"},
            "thickness": 3,
            "mode": "union",
        },
    )
    assert (args["distance"], args["mode"], args["profile"]) == (3, "add", {"type": "circle", "center": [0, 0], "d": 10})


def test_slot_width_is_not_renamed():
    profile = {"type": "slot", "center": [0, 0], "center_distance": 10, "width": 4}
    call = {"part": "p", "profile": profile, "plane": {"name": "XY"}, "distance": 3}
    assert coerce.normalize("extrude", call) == (call, [])


def test_edge_finish_radius_becomes_size():
    args, _ = coerce.normalize("edge_finish", {"part": "p", "kind": "fillet", "radius": 2, "near": [40, 0, 5]})
    assert args["size"] == 2 and args["near"] == [[40, 0, 5]] and "radius" not in args


def test_edit_op_is_normalized_inside_the_op():
    op = {"op": "holes", "plane": "XY", "points": [[0, 0]], "diameter": 5, "through": True}
    args, _ = coerce.normalize("edit_op", {"part": "p", "index": 2, "op": op})
    assert args["op"] == {"op": "holes", "plane": {"name": "XY"}, "positions": [[0, 0]], "d": 5}
    assert args["index"] == 2


def test_unknown_top_level_arguments_are_ignored_with_a_note():
    args, notes = coerce.normalize("edge_finish", {"part": "p", "kind": "fillet", "size": 2, "near": [[0, 0, 0]], "color": "red"})
    assert "color" not in args and len(ignored(notes)) == 1


# --- submit_spec und check_spec ---


def test_spec_enums_and_sources():
    data = spec()
    data["views"][0]["kind"] = "Draufsicht"
    data["dimensions"][0]["kind"] = "Durchmesser"
    data["dimensions"][1]["source"] = "v1"
    data["dimensions"][2]["source"] = {"view": "v1", "cell": "b3"}
    data["notes"] = [{"id": "n1", "text": "t", "source": {"view": "v1", "cell": ""}}]
    args, _ = coerce.normalize("submit_spec", {"spec": data})
    got = args["spec"]
    assert got["views"][0]["kind"] == "top" and got["dimensions"][0]["kind"] == "diameter"
    assert got["dimensions"][1]["source"] == {"view": "v1", "cell": None}
    assert got["dimensions"][2]["source"]["cell"] == "B3"
    assert got["notes"][0]["source"]["cell"] is None


@pytest.mark.parametrize("scale, text", [(2, "2:1"), (0.5, "1:2"), (1, "1:1"), (2.5, "2.5:1")])
def test_numeric_scale_becomes_text(scale, text):
    data = spec() | {"scale": scale}
    args, notes = coerce.normalize("submit_spec", {"spec": data})
    assert args["spec"]["scale"] == text and notes


@pytest.mark.parametrize("raw", ["2768-m", "ISO2768-m", "ISO 2768 M", "iso 2768-m"])
def test_general_tolerance_is_normalized(raw):
    args, _ = coerce.normalize("submit_spec", {"spec": spec() | {"general_tolerance": raw}})
    assert args["spec"]["general_tolerance"] == "ISO 2768-m"


def test_axis_letter_becomes_vector():
    data = spec()
    data["expected"]["holes"][0]["axis"] = "Z"
    args, _ = coerce.normalize("submit_spec", {"spec": data})
    assert args["spec"]["expected"]["holes"][0]["axis"] == [0, 0, 1]


def test_missing_review_id_is_filled():
    data = spec() | {"review": [{"refs": ["d1"], "issue": "convention", "assumption": "a", "reason": "b"}]}
    args, notes = coerce.normalize("submit_spec", {"spec": data})
    assert args["spec"]["review"][0]["id"] == "r1" and notes


def test_confidence_text_and_expected_are_not_invented():
    data = spec()
    del data["dimensions"][0]["confidence"]
    del data["dimensions"][1]["text"]
    del data["expected"]
    args, notes = coerce.normalize("submit_spec", {"spec": data})
    assert "confidence" not in args["spec"]["dimensions"][0] and "text" not in args["spec"]["dimensions"][1]
    assert "expected" not in args["spec"] and notes == []


# --- Leerlauf ---

VALID_CALLS = [
    (
        "extrude",
        {
            "part": "p",
            "profile": {"type": "rect", "center": [0, 0], "w": 80, "h": 50},
            "plane": {"name": "XY"},
            "distance": 10,
            "mode": "cut",
            "both": False,
            "taper": 0.0,
            "refs": {"profile.w": "d1", "distance": "d2"},
        },
    ),
    ("revolve", {"part": "p", "points": [[0, 0], [5, 0], [5, 30]], "angle": 360.0, "mode": "add"}),
    ("add_holes", holes(depth=5, kind="cbore", cbore_d=10, cbore_depth=3, flip=True, refs={"d": "d3"})),
    ("add_holes", {"part": "p", "plane": {"name": "XY", "offset": 4}, "d": 3, "polar": {"center": [0, 0], "pitch_d": 20, "count": 4}}),
    ("edge_finish", {"part": "p", "kind": "chamfer", "size": 1, "near": [[1, 2, 3], [4, 5, 6]], "tol": 0.5}),
    ("run_script", {"part": "p", "code": "result = result"}),
    (
        "edit_op",
        {
            "part": "p",
            "index": 0,
            "op": {
                "op": "extrude",
                "profile": {"type": "circle", "center": [0, 0], "d": 5},
                "plane": {"name": "XZ", "offset": 2},
                "distance": -3,
            },
        },
    ),
    ("delete_op", {"part": "p", "index": 1}),
    ("move_op", {"part": "p", "index": 1, "new_index": 0}),
    ("get_state", {"part": "p"}),
    ("export_step", {"part": "p", "variant": "mid"}),
    ("render_drawing", {"pdf": "a/b.pdf", "page": 1, "dpi": 100, "crop": [0, 0, 0.5, 0.5]}),
    ("read_drawing_text", {"pdf": "a/b.pdf", "page": 1, "region": [0.5, 0.8, 1, 1]}),
    (
        "measure_drawing",
        {
            "pdf": "a/b.pdf",
            "region": [0.1, 0.1, 0.9, 0.5],
            "scale": 2,
            "printed_dims": [80, 50, 6.6],
            "expect": ["d=19"],
            "edge_width": "auto",
        },
    ),
    ("submit_spec", {"spec": spec(), "keep_ops": True}),
    ("check_spec", {"spec": spec(), "second": spec()}),
]


@pytest.mark.parametrize("tool, call", VALID_CALLS, ids=[f"{i}-{t}" for i, (t, _) in enumerate(VALID_CALLS)])
def test_valid_calls_are_untouched(tool, call):
    before = copy.deepcopy(call)
    assert coerce.normalize(tool, call, BBOX) == (before, [])
    assert call == before, "normalize darf die Eingabe nicht verändern"


def test_idle_for_demo_ops():
    for op in demo_ops():
        tool = {"holes": "add_holes"}.get(op["op"], op["op"])
        call = {"part": "demo"} | {k: v for k, v in op.items() if k != "op"}
        assert coerce.normalize(tool, call, BBOX) == (call, [])


def test_demo_spec_is_idle_and_valid():
    data = spec()
    PartSpec.model_validate(data)
    assert coerce.normalize("submit_spec", {"spec": data}) == ({"spec": data}, [])


# --- Zulässige Felder bleiben mit den Op-Modellen im Gleichlauf ---


@pytest.mark.parametrize(
    "tool, model",
    [("extrude", ExtrudeOp), ("revolve", RevolveOp), ("add_holes", HolesOp), ("edge_finish", EdgeFinishOp), ("run_script", ScriptOp)],
)
def test_allowed_fields_match_the_models(tool, model):
    assert coerce.TOOL_FIELDS[tool] == (set(model.model_fields) - {"op"}) | {"part"}


def test_swapped_refs_are_mapped_by_value_or_dropped():
    call = {
        "part": "p",
        "profile": {"type": "rect", "center": [0, 0], "w": 115, "h": 115},
        "plane": {"name": "XY"},
        "distance": 76.2,
        "refs": {"d6": "76.2", "d8": "R9.5", "distance": "d9"},
    }
    out, notes = coerce.normalize("extrude", call)
    assert out["refs"] == {"distance": "d9"}  # der Pfad ist schon belegt
    out, notes = coerce.normalize("extrude", call | {"refs": {"d6": "76.2", "d8": "R9.5"}})
    assert out["refs"] == {"distance": "d6"} and len(notes) == 2 and "removed" in notes[1]
    ExtrudeOp.model_validate({"op": "extrude"} | {k: v for k, v in out.items() if k != "part"})
    out, notes = coerce.normalize("add_holes", holes(refs={"d_bore": "d"}))
    assert out["refs"] == {"d": "d_bore"} and "swapped" in notes[0]


def test_swap_keeps_a_valid_binding_and_steps_are_grouped():
    call = {
        "part": "p",
        "profile": {"type": "rect", "center": [0, 0], "w": 9, "h": 9},
        "plane": {"name": "XY"},
        "distance": 5,
        "refs": {"distance": "d1", "d6": "distance"},
    }
    out, notes = coerce.normalize("extrude", call)
    assert out["refs"] == {"distance": "d1"} and "removed" in notes[0]
    out, notes = coerce.normalize("revolve", {"part": "p", "steps": [30, 50, "Ø45", 20]})
    assert out["steps"] == [[30, 50], [45, 20]]


def test_tolerance_near_error_names_a_single_point():
    data = spec() | {"datums": [{"id": "A", "label": "A", "near": [[1, 2]]}]}
    with pytest.raises(ValidationError) as caught:
        PartSpec.model_validate(data)
    assert '"near": [x, y, z]' in validation_error_message(caught.value)

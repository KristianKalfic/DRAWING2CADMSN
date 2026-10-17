"""The MCP tools, called in-process through server.call_tool."""

import asyncio
import json
import shutil
from pathlib import Path

import pymupdf
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from d2c import server as srv
from d2c.ops import EdgeFinishOp, ExtrudeOp, HolesOp, RevolveOp
from d2c.session import Session

FIXTURES = Path(__file__).parent / "fixtures"
DRAWING = "Demo Teil (A)/demo drawing.pdf"
FRONT_VIEW = [0.21, 0.202, 0.781, 0.333]
TOP_VIEW = [0.21, 0.404, 0.781, 0.69]
TITLE_BLOCK = [0.467, 0.838, 0.962, 0.973]
TOOLS = {
    "render_drawing", "read_drawing_text", "measure_drawing", "submit_spec", "extrude", "revolve", "add_holes",
    "edge_finish", "run_script", "edit_op", "delete_op", "move_op", "get_state", "export_step",
}
TOOL_OF_OP = {"extrude": "extrude", "revolve": "revolve", "holes": "add_holes", "edge_finish": "edge_finish",
              "script": "run_script"}
BUILD_TOOLS = {"extrude": ExtrudeOp, "revolve": RevolveOp, "add_holes": HolesOp, "edge_finish": EdgeFinishOp}


@pytest.fixture(autouse=True)
def fresh_session(monkeypatch):
    monkeypatch.setattr(srv, "session", Session())


@pytest.fixture
def data(tmp_path, monkeypatch) -> Path:
    """A data folder holding the demo drawing under a name with spaces and parentheses."""
    root = tmp_path / "data"
    (root / "Demo Teil (A)").mkdir(parents=True)
    shutil.copy(FIXTURES / "demo_drawing.pdf", root / DRAWING)
    monkeypatch.setenv("D2C_DATA", str(root))
    return root


def call(name: str, **args):
    return asyncio.run(srv.server.call_tool(name, args))


def call_json(name: str, **args) -> dict:
    return json.loads(call(name, **args).content[0].text)


def test_lists_14_tools():
    assert {t.name for t in asyncio.run(srv.server.list_tools())} == TOOLS


def test_tool_params_match_op_models():
    tools = {t.name: t.input_schema for t in asyncio.run(srv.server.list_tools())}
    for name, model in BUILD_TOOLS.items():
        schema = tools[name]
        fields = {k: f for k, f in model.model_fields.items() if k != "op"}
        assert set(schema["properties"]) == {"part", *fields}, name
        assert set(schema["required"]) == {"part", *(k for k, f in fields.items() if f.is_required())}, name
        for key, field in fields.items():
            if not field.is_required():
                assert schema["properties"][key]["default"] == field.default, (name, key)


def test_tool_docstrings_state_conventions():
    docs = {t.name: t.description for t in asyncio.run(srv.server.list_tools())}
    assert all("Example" in doc for doc in docs.values())
    assert "TOTAL thickness" in docs["extrude"]
    assert "-normal" in docs["add_holes"] and "flip" in docs["add_holes"]
    assert "not even `_`" in docs["run_script"]


def test_render_returns_png_and_info(data):
    image, info = call("render_drawing", pdf=DRAWING, page=1, dpi=100).content
    assert image.mime_type == "image/png"
    assert json.loads(info.text)["pages"] == 2
    assert '"pages": 2' in info.text


def test_render_crop(data):
    image, info = call("render_drawing", pdf=DRAWING, dpi=300, crop=TOP_VIEW).content
    assert image.mime_type == "image/png"
    assert json.loads(info.text)["dpi"] == 300


def test_bad_args_tool_error():
    with pytest.raises(ToolError):
        call("extrude", part="p", plane={"name": "XY"}, distance=1)


def test_invalid_op_message_reaches_client():
    with pytest.raises(ToolError, match="distance must not be 0"):
        call("extrude", part="p", profile={"type": "circle", "center": [0, 0], "d": 5}, plane={"name": "XY"},
             distance=0)


def test_unknown_part_message():
    with pytest.raises(ToolError, match="call submit_spec first"):
        call("get_state", part="nope")


def test_demo_end_to_end(data):
    spec = json.loads((FIXTURES / "demo_spec.json").read_text("utf-8"))
    assert call_json("submit_spec", spec=spec)["ok"] is True
    for op in json.loads((FIXTURES / "demo_ops.json").read_text("utf-8")):
        fields = {k: v for k, v in op.items() if k != "op"}
        report = call_json(TOOL_OF_OP[op["op"]], part="demo_bracket", **fields)
        assert not report["error"]
    result = call_json("export_step", part="demo_bracket")
    assert result["ok"] is True
    assert "COLOUR_RGB" in Path(result["files"]["step"]).read_text("utf-8", errors="replace")
    review = Path(result["files"]["review"]).read_text("utf-8")
    assert "All checks passed: yes" in review
    open_issues = review.split("## Assumptions and open issues")[1].split("##")[0]
    assert any("t10" in line for line in open_issues.splitlines())
    unreferenced = review.split("## Values without dimension reference")[1].split("##")[0]
    assert "15" in unreferenced


def test_edit_move_delete_and_state(data):
    call_json("submit_spec", spec=json.loads((FIXTURES / "demo_spec.json").read_text("utf-8")))
    ops = json.loads((FIXTURES / "demo_ops.json").read_text("utf-8"))
    for op in ops:
        call_json(TOOL_OF_OP[op["op"]], part="demo_bracket", **{k: v for k, v in op.items() if k != "op"})
    assert call_json("move_op", part="demo_bracket", index=3, new_index=2)["op_index"] == 2
    assert call_json("delete_op", part="demo_bracket", index=2)["op_index"] == 2
    assert call_json("edit_op", part="demo_bracket", index=1, op=ops[1] | {"distance": 20})["op_index"] == 1
    state = call_json("get_state", part="demo_bracket")
    assert [op["op"] for op in state["ops"]] == ["extrude", "extrude", "holes"]
    assert state["ops"][1]["distance"] == 20
    with pytest.raises(ToolError, match="out of range"):
        call("delete_op", part="demo_bracket", index=9)


def test_run_script_applies_op(data):
    call_json("submit_spec", spec=json.loads((FIXTURES / "demo_spec.json").read_text("utf-8")))
    code = "result = cq.Workplane('XY').box(80, 50, 10)"
    assert not call_json("run_script", part="demo_bracket", code=code)["error"]
    assert call_json("get_state", part="demo_bracket")["ops"] == [{"op": "script", "code": code, "refs": {}}]


def test_text_tool_demo(data):
    items = json.loads(call("read_drawing_text", pdf=DRAWING, region=TITLE_BLOCK).content[0].text)
    assert "Scale 1:1" in [i["text"] for i in items]


def test_text_tool_no_text_layer(tmp_path, monkeypatch):
    doc = pymupdf.open()
    doc.new_page()
    doc.save(tmp_path / "blank.pdf")
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    assert "no text layer" in call("read_drawing_text", pdf="blank.pdf").content[0].text


def test_text_tool_too_many_items(tmp_path, monkeypatch):
    doc = pymupdf.open()
    page = doc.new_page()
    for k in range(srv.MAX_TEXT_ITEMS + 10):
        page.insert_text((20, 20 + 2.4 * k), f"item {k}", fontsize=2)
    doc.save(tmp_path / "busy.pdf")
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    assert "smaller region" in call("read_drawing_text", pdf="busy.pdf").content[0].text


def test_text_tool_rejects_outside_data(data):
    with pytest.raises(ToolError, match="inside the data folder"):
        call("read_drawing_text", pdf="../../etc/passwd")


def test_measure_tool_demo(data):
    res = call_json("measure_drawing", pdf=DRAWING, region=FRONT_VIEW, scale=1, expect=["L=10", "L=80", "L=25"])
    assert [v["status"] for v in res["verify"]] == ["ok"] * 3
    assert res["scale_used"] == 1
    top = call_json("measure_drawing", pdf=DRAWING, region=TOP_VIEW, scale=1)
    assert sorted(c["d_mm"] for c in top["circles"]) == pytest.approx([6.6] * 4 + [16, 30], abs=0.01)
    assert "scale_suggestion" not in top and top["notes"] == []


def test_measure_tool_scale_note(data):
    printed = [80, 50, 60, 30, 30, 16]
    ok = call_json("measure_drawing", pdf=DRAWING, region=TOP_VIEW, scale=1, printed_dims=printed)
    assert ok["scale_suggestion"][0]["scale"] == pytest.approx(1.0, abs=0.01)
    assert ok["notes"] == []
    wrong = call_json("measure_drawing", pdf=DRAWING, region=TOP_VIEW, scale=2, printed_dims=printed)
    assert any("title-block scale disagrees with geometry" in note for note in wrong["notes"])


def test_measure_tool_notes_missing_scale_and_few_values(data):
    res = call_json("measure_drawing", pdf=DRAWING, region=TOP_VIEW, printed_dims=[80, 50])
    assert any("scale not given" in note for note in res["notes"])
    assert any("unreliable" in note for note in res["notes"])


def test_measure_tool_edge_width_as_text(data):
    assert call_json("measure_drawing", pdf=DRAWING, region=TOP_VIEW, edge_width="0.5")["circles"]
    with pytest.raises(ToolError, match="could not convert"):
        call("measure_drawing", pdf=DRAWING, edge_width="thick")


def test_measure_tool_rejects_outside_data(data):
    with pytest.raises(ToolError, match="inside the data folder"):
        call("measure_drawing", pdf="../../etc/passwd")

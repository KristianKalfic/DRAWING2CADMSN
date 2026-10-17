"""Gegenproben zu einer Spezifikation: zwei Lesungen verglichen, Plausibilitätsregeln, das check_spec-Werkzeug, das FreeCAD-Skript."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from d2c import server as srv
from d2c.crosscheck import compare_specs, plausibility
from d2c.session import Session
from d2c.spec import PartSpec

ROOT = Path(__file__).parents[1]
DEMO_SPEC = ROOT / "tests" / "fixtures" / "demo_spec.json"
FREECAD_CHECK = ROOT / "scripts" / "freecad_check.py"


@pytest.fixture(autouse=True)
def fresh_session(monkeypatch):
    monkeypatch.setattr(srv, "session", Session())


def demo() -> dict:
    """Die Demo-Spezifikation als schlichtes Diktat, bei jedem Aufruf frisch, damit ein Test genau eine Sache ändert."""
    return json.loads(DEMO_SPEC.read_text("utf-8"))


def spec_of(raw: dict) -> PartSpec:
    return PartSpec.model_validate(raw)


def codes(findings: list[dict]) -> list[str]:
    return [f["code"] for f in findings]


def add_fillet(raw: dict, radius: float) -> dict:
    raw["dimensions"].append({"id": "rf", "kind": "radius", "value": radius, "text": f"R{radius:g}",
                              "source": None, "confidence": 0.9})
    raw["features"].append({"id": "f_fillet", "type": "fillet", "dim_ids": ["rf"]})
    return raw


# ---------------------------------------------------------------- compare_specs

def test_compare_identical_specs_no_findings():
    assert compare_specs(spec_of(demo()), spec_of(demo())) == []


def test_compare_ignores_noise_between_readings():
    """Andere Kennungen, Reihenfolge, Ankerpunkt und Vorzeichen der Achse, gedruckter Text und Rundungsrauschen sind kein Befund."""
    raw = json.loads(json.dumps(demo()).replace('"d80"', '"q80"').replace('"d50"', '"q50"'))
    raw["dimensions"].reverse()
    next(d for d in raw["dimensions"] if d["id"] == "d16")["text"] = "Ø 16 H7"
    holes = raw["expected"]["holes"]
    holes.reverse()
    for hole in holes:
        hole["at"][2] += 7  # another point on the same axis
    holes[0]["axis"] = [0, 0, -1]  # the same line, the other direction
    holes[1]["at"][0] += 0.01
    raw["expected"]["bbox"][0] += 0.02
    assert compare_specs(spec_of(demo()), spec_of(raw)) == []


def test_compare_pairs_stepped_holes_despite_float_noise():
    """Eine Senkung teilt die Achse ihrer Bohrung: der Durchmesser unterscheidet sie, nicht ein Rauschen in den Koordinaten."""
    def with_counterbore(x_hole: float, x_bore: float) -> PartSpec:
        raw = demo()
        raw["expected"]["holes"][1]["at"][0] = x_hole
        raw["expected"]["holes"].append({"d": 11, "at": [x_bore, 15, 5], "axis": [0, 0, -1], "dim_ids": ["d66"]})
        return spec_of(raw)

    # by axis distance alone each counterbore would take the wrong partner
    assert compare_specs(with_counterbore(30, 30 + 2e-12), with_counterbore(30 + 3e-12, 30 + 0.5e-12)) == []


def test_compare_detects_changed_hole_position():
    raw = demo()
    raw["expected"]["holes"][1]["at"] = [32, 15, 0]  # printed at 30
    findings = compare_specs(spec_of(demo()), spec_of(raw))
    assert [(f["code"], f["where"]) for f in findings] == [("EXTRACTION_MISMATCH", "expected.holes[1].at")]
    review = findings[0]["suggested_review"]
    assert review["issue"] == "ambiguous"
    assert review["refs"] == ["d66"]
    assert review["assumption"].strip() and review["reason"].strip()
    assert "(30, 15, 0)" in findings[0]["message"] and "(32, 15, 0)" in findings[0]["message"]


def test_compare_detects_hole_diameter_axis_and_count():
    raw = demo()
    holes = raw["expected"]["holes"]
    holes[2]["d"] = 6.0
    holes[3].update(axis=[1, 0, 0], at=[30, -15, 5])  # a slanted hole is no longer on the line of the other one
    del holes[4]
    findings = compare_specs(spec_of(demo()), spec_of(raw))
    assert sorted((f["code"], f["where"]) for f in findings) == [
        ("EXTRACTION_MISMATCH", "expected.holes[2].d"),
        ("EXTRACTION_MISMATCH", "expected.holes[3].axis"),  # the position of a slanted hole is not compared
        ("HOLE_COUNT", "expected.holes"),
    ]
    count = next(f for f in findings if f["code"] == "HOLE_COUNT")
    assert "A=5" in count["message"] and "B=4" in count["message"] and "A[4]" in count["message"]


def test_compare_detects_changed_bbox():
    raw = demo()
    raw["expected"]["bbox"][2] = 30
    findings = compare_specs(spec_of(demo()), spec_of(raw))
    assert [(f["code"], f["where"]) for f in findings] == [("EXTRACTION_MISMATCH", "expected.bbox.z")]


def test_compare_detects_dimension_only_in_one():
    raw = demo()
    raw["dimensions"].append({"id": "x1", "kind": "length", "value": 12, "text": "12", "source": None,
                              "confidence": 0.9})
    a, b = spec_of(demo()), spec_of(raw)
    (only_b,) = compare_specs(a, b)
    assert (only_b["code"], only_b["where"]) == ("ONLY_IN_B", "second.dimensions.x1")
    assert only_b["suggested_review"]["refs"] == []  # the id does not exist in A
    (only_a,) = compare_specs(b, a)
    assert (only_a["code"], only_a["where"]) == ("ONLY_IN_A", "dimensions.x1")
    assert only_a["suggested_review"]["refs"] == ["x1"]
    assert "12" in only_a["message"]


def test_compare_tolerance_decides_pairing():
    raw = demo()
    raw["dimensions"].append({"id": "x1", "kind": "length", "value": 12, "text": "12", "source": None,
                              "confidence": 0.9})
    other = demo()
    other["dimensions"].append({"id": "y1", "kind": "length", "value": 12.04, "text": "12,04", "source": None,
                                "confidence": 0.9})
    assert compare_specs(spec_of(raw), spec_of(other)) == []
    assert codes(compare_specs(spec_of(raw), spec_of(other), tol=0.01)) == ["ONLY_IN_A", "ONLY_IN_B"]


def test_compare_pairs_leftover_dimensions_by_printed_text():
    raw = demo()
    d66 = next(d for d in raw["dimensions"] if d["id"] == "d66")
    d66.update(kind="count", value=4)  # "4x Ø6,6" read as the count 4 instead of the diameter 6.6
    findings = compare_specs(spec_of(demo()), spec_of(raw))
    assert [(f["code"], f["where"]) for f in findings] == [("EXTRACTION_MISMATCH", "dimensions.d66")]
    assert "diameter 6.6" in findings[0]["message"] and "count 4" in findings[0]["message"]


def test_compare_detects_changed_fit_and_deviation():
    raw = demo()
    d16, d80 = (next(d for d in raw["dimensions"] if d["id"] == i) for i in ("d16", "d80"))
    d16["fit"] = None
    d80.update(tol_upper=0.1, tol_lower=-0.1)
    findings = compare_specs(spec_of(demo()), spec_of(raw))
    assert sorted(f["where"] for f in findings) == ["dimensions.d16.fit", "dimensions.d80.tol"]
    assert set(codes(findings)) == {"EXTRACTION_MISMATCH"}


# ---------------------------------------------------------------- plausibility

def test_plausibility_demo_spec_no_findings():
    assert plausibility(spec_of(demo())) == []


def test_plausibility_hole_outside_bbox():
    raw = demo()
    raw["expected"]["holes"][1]["at"] = [70, 15, 0]  # the holes now span 106.6 mm along x, the bbox has 80
    (finding,) = plausibility(spec_of(raw))
    assert (finding["code"], finding["where"]) == ("HOLE_OUTSIDE_BBOX", "expected.bbox.x")
    assert "106.6" in finding["message"] and "80" in finding["message"]
    assert finding["suggested_review"]["issue"] == "conflict"
    assert finding["suggested_review"]["refs"] == ["d66"]
    # eine einzelne Bohrung größer als das Teil: zu breit entlang x und y
    raw = demo()
    raw["expected"]["holes"] = [{"d": 90, "at": [0, 0, 0], "axis": [0, 0, 1], "dim_ids": ["d16"]}]
    assert [f["where"] for f in plausibility(spec_of(raw))] == ["expected.bbox.x", "expected.bbox.y"]
    # holes along another axis count across their own plane: a hole along x spans y and z
    raw = demo()
    raw["expected"]["holes"] = [{"d": 30, "at": [0, 0, 10], "axis": [1, 0, 0], "dim_ids": ["d16"]}]
    assert [f["where"] for f in plausibility(spec_of(raw))] == ["expected.bbox.z"]  # 30 > 25, but 30 <= 50


def test_plausibility_overlapping_holes():
    raw = demo()
    raw["expected"]["holes"][2]["at"] = [30, 12, 0]  # 3 mm von der Bohrung bei (30, 15) entfernt, Durchmesser 6,6
    (finding,) = plausibility(spec_of(raw))
    assert (finding["code"], finding["where"]) == ("HOLES_OVERLAP", "expected.holes")
    assert "1 and 2" in finding["message"]
    assert finding["suggested_review"]["issue"] == "conflict"
    assert finding["suggested_review"]["refs"] == ["d66"]


def test_plausibility_overlap_ignores_stepped_and_cross_holes():
    raw = demo()
    raw["expected"]["holes"] += [
        {"d": 11, "at": [30, 15, 5], "axis": [0, 0, -1], "dim_ids": ["d66"]},  # Senkung auf der Achse von Bohrung 1
        {"d": 4, "at": [0, 0, 5], "axis": [1, 0, 0], "dim_ids": ["d16"]},  # Kreuzloch durch die Grundbohrung
    ]
    assert plausibility(spec_of(raw)) == []


def test_plausibility_fillet_too_big():
    # bbox 80 x 50 x 25: die mittlere Kante ist 50, also ist R25 der größte Radius
    assert plausibility(spec_of(add_fillet(demo(), 25))) == []
    (finding,) = plausibility(spec_of(add_fillet(demo(), 30)))
    assert (finding["code"], finding["where"]) == ("FILLET_TOO_BIG", "features.f_fillet")
    assert finding["suggested_review"]["refs"] == ["rf"]
    # eine dünne Platte darf Ecken haben, die weit über die halbe Stärke hinaus gerundet sind
    raw = add_fillet(demo(), 10)
    raw["expected"]["bbox"] = [80, 50, 3]
    assert plausibility(spec_of(raw)) == []
    # nur Rundungsmerkmale werden geprüft: ein großer Bogenradius woanders ist in Ordnung
    raw = demo()
    next(d for d in raw["dimensions"] if d["id"] == "r5").update(value=60, text="R60")
    assert plausibility(spec_of(raw)) == []


# ---------------------------------------------------------------- check_spec tool

def check(**args) -> dict:
    return json.loads(asyncio.run(srv.server.call_tool("check_spec", args)).content[0].text)


def test_check_spec_tool_returns_findings():
    clean = check(spec=demo())
    assert clean == {"findings": [], "count": 0}  # a dict, not an empty list, which would arrive as no answer
    raw = demo()
    raw["expected"]["holes"][1]["at"] = [70, 15, 0]
    one = check(spec=raw)
    assert one["count"] == len(one["findings"]) == 1
    assert one["findings"][0]["code"] == "HOLE_OUTSIDE_BBOX"
    second = json.loads(json.dumps(raw))
    second["expected"]["bbox"][2] = 30  # the only difference between the two readings
    both = check(spec=raw, second=second)
    assert both["count"] == 2
    assert codes(both["findings"]) == ["HOLE_OUTSIDE_BBOX", "EXTRACTION_MISMATCH"]
    assert all(f["suggested_review"]["reason"] for f in both["findings"])


def test_check_spec_tool_registers_nothing():
    check(spec=demo())
    assert srv.session.specs == {}
    assert not (Path(os.environ["D2C_RESULTS"]) / "demo_bracket").exists()
    with pytest.raises(ToolError, match="rufe zuerst `submit_spec` auf"):
        asyncio.run(srv.server.call_tool("get_state", {"part": "demo_bracket"}))


def test_check_spec_tool_rejects_invalid_spec():
    raw = demo()
    raw["dimensions"][0]["id"] = "d50"  # duplicate id
    with pytest.raises(ToolError, match="Doppelte ID"):
        check(spec=raw)


def test_instructions_name_check_spec():
    text = srv.server.instructions
    assert "check_spec" in text and text.index("check_spec") < text.index("`submit_spec`")


# ---------------------------------------------------------------- scripts/freecad_check.py

def stub_freecad(tmp_path: Path, measured: dict) -> Path:
    """Ein Ersatz für freecadcmd, der eine feste Messung schreibt, wo das Skript sie erwartet."""
    exe = tmp_path / "freecadcmd"
    exe.write_text(f"#!{sys.executable}\nimport json, os\njson.dump({measured!r}, open(os.environ['D2C_FC_OUT'], 'w'))\n")
    exe.chmod(0o755)
    return exe


def measured(**changes) -> dict:
    """Was FreeCAD über das exportierte Demoteil berichtet: fünf konkave Zylinder und eine gerundete Ecke."""
    holes = [(16, (0, 0)), (6.6, (30, 15)), (6.6, (-30, 15)), (6.6, (30, -15)), (6.6, (-30, -15))]
    cylinders = [{"d": d, "axis": [0, 0, -1], "point": [x, y, 10], "concave": True} for d, (x, y) in holes]
    cylinders.append({"d": 10, "axis": [0, 0, -1], "point": [-35, -20, 0], "concave": False})
    return {"version": "1.1.4", "valid": True, "solids": 1, "volume": 43993.2, "bbox": [80, 50, 25],
            "cylinders": cylinders} | changes


def run_check(tmp_path: Path, report: dict, *extra: str) -> subprocess.CompletedProcess:
    step = tmp_path / "part.step"
    step.write_text("dummy")
    cmd = [sys.executable, str(FREECAD_CHECK), str(step), "--freecad", str(stub_freecad(tmp_path, report)), *extra]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_freecad_check_without_freecad_exits_2(tmp_path):
    step = tmp_path / "part.step"
    step.write_text("dummy")
    done = subprocess.run([sys.executable, str(FREECAD_CHECK), str(step), "--freecad", str(tmp_path / "no" / "freecadcmd")],
                          capture_output=True, text=True)
    assert done.returncode == 2
    assert "FreeCAD not found" in done.stderr and "--freecad" in done.stderr


def test_freecad_check_passes_matching_part(tmp_path):
    done = run_check(tmp_path, measured(), "--spec", str(DEMO_SPEC))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "FreeCAD 1.1.4" in done.stdout and "FAILED" not in done.stdout
    assert run_check(tmp_path, measured()).returncode == 0  # without --spec: only the solid is checked


@pytest.mark.parametrize("change, shown", [
    ({"bbox": [80, 50, 24]}, "bbox"),
    ({"cylinders": measured()["cylinders"][1:]}, "hole d=16"),  # the bore is missing
    ({"cylinders": [{**c, "concave": False} if c["d"] == 16 else c for c in measured()["cylinders"]]}, "hole d=16"),
    ({"cylinders": [{**c, "axis": [1, 0, 0]} if c["d"] == 16 else c for c in measured()["cylinders"]]}, "hole d=16"),
    ({"cylinders": [{**c, "point": [1, 0, 10]} if c["d"] == 16 else c for c in measured()["cylinders"]]}, "hole d=16"),
    ({"valid": False}, "valid=False"),
    ({"solids": 2}, "solids=2"),
])
def test_freecad_check_fails_wrong_part(tmp_path, change, shown):
    done = run_check(tmp_path, measured(**change), "--spec", str(DEMO_SPEC))
    assert done.returncode == 1, done.stdout + done.stderr
    assert shown in done.stdout and "FAILED" in done.stdout


def test_freecad_check_without_result_exits_1(tmp_path):
    exe = tmp_path / "freecadcmd"
    exe.write_text(f"#!{sys.executable}\nprint('cannot read the file')\n")
    exe.chmod(0o755)
    step = tmp_path / "part.step"
    step.write_text("dummy")
    done = subprocess.run([sys.executable, str(FREECAD_CHECK), str(step), "--freecad", str(exe)],
                          capture_output=True, text=True)
    assert done.returncode == 1 and "cannot read the file" in done.stderr

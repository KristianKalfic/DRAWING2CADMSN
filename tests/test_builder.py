"""Tests fuer build_from_json + export_step (direkte Funktionsaufrufe, kein MCP-Transport)."""
import json
import math
import os

import cadquery as cq
import pytest

import server as S
from json_builder import generate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOL_TOL = 1e-6  # relativ; analytische Sollvolumina


@pytest.fixture(autouse=True)
def tmp_out(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "OUT", str(tmp_path))
    return tmp_path


def build(spec):
    r = S.build_from_json(spec)
    assert r["ok"], r.get("error")
    return r


def build_and_export(spec):
    r = build(spec)
    e = S.export_step(r["part_id"])
    return r, e


def assert_good(r, e, volume):
    v = e["validation"]
    failed = [c for c in v["checks"] if not c["pass"]]
    assert failed == [], failed
    assert v["is_valid"] and v["solids"] == 1
    assert v["all_pass"] is True
    assert v["volume"] == pytest.approx(volume, rel=VOL_TOL)
    # Re-Import: Datei existiert, genau 1 gueltiger Solid, gleiches Volumen
    assert os.path.isfile(e["path"])
    assert v["reimport"]["ok"] and v["reimport"]["solids"] == 1
    assert v["volume_match"]
    re = cq.importers.importStep(e["path"])
    assert re.val().Volume() == pytest.approx(volume, rel=VOL_TOL)


def box(L, W, H, features=(), **kw):
    return {"part_name": kw.get("name", "t"),
            "base": {"shape": "box", "length": L, "width": W, "height": H}, "features": list(features)}


def cyl_vol(d, h):
    return math.pi * (d / 2) ** 2 * h


# ---------------------------------------------------------------- Gutfaelle

def test_beispiel_v2():
    spec = json.load(open(os.path.join(ROOT, "examples", "beispiel_v2.json"), encoding="utf-8"))
    r, e = build_and_export(spec)
    vol = 100 * 60 * 20 - (4 - math.pi) * 5 ** 2 * 20 - 30 * 20 * 5 - 4 * cyl_vol(8.5, 20)
    assert_good(r, e, vol)
    v = e["validation"]
    assert v["bbox"] == {"x": 100, "y": 60, "z": 20}
    holes = sorted((h["x"], h["y"], h["d"]) for h in v["holes_found"])
    assert holes == [(-35, -20, 8.5), (-35, 20, 8.5), (35, -20, 8.5), (35, 20, 8.5)]
    assert r["warnings"] == [] and r["unsupported"] == []


def test_box_lochmuster_rect():
    spec = box(80, 60, 10, [{"type": "hole", "x": 0, "y": 0, "diameter": 6, "through": True,
                             "pattern": {"type": "rect", "nx": 3, "ny": 2, "dx": 25, "dy": 40}}])
    r, e = build_and_export(spec)
    assert_good(r, e, 80 * 60 * 10 - 6 * cyl_vol(6, 10))
    assert len(e["validation"]["holes_found"]) == 6
    xs = sorted({h["x"] for h in e["validation"]["holes_found"]})
    assert xs == [-25, 0, 25]


def test_box_lochmuster_circle():
    spec = box(100, 100, 10, [{"type": "hole", "x": 0, "y": 0, "diameter": 8, "through": True,
                               "pattern": {"type": "circle", "n": 6, "radius": 30, "start_angle": 0}}])
    r, e = build_and_export(spec)
    assert_good(r, e, 100 * 100 * 10 - 6 * cyl_vol(8, 10))
    for h in e["validation"]["holes_found"]:
        assert math.hypot(h["x"], h["y"]) == pytest.approx(30, abs=1e-3)


def test_zylinder_mit_senkbohrung():
    spec = {"part_name": "zyl", "base": {"shape": "cylinder", "diameter": 60, "height": 20},
            "features": [{"type": "hole", "x": 0, "y": 0, "diameter": 6.6, "through": True,
                          "counterbore_diameter": 11, "counterbore_depth": 6}]}
    r, e = build_and_export(spec)
    vol = cyl_vol(60, 20) - cyl_vol(6.6, 14) - cyl_vol(11, 6)
    assert_good(r, e, vol)
    assert {h["d"] for h in e["validation"]["holes_found"]} == {6.6, 11}
    # Aussenmantel (konvex) darf nicht als Bohrung zaehlen
    assert 60 not in {h["d"] for h in e["validation"]["holes_found"]}


def test_profil_extrusion():
    # L-Profil, um den Nullpunkt zentriert (Bounding Box 80 x 60)
    pts = [[-40, -30], [40, -30], [40, -10], [-20, -10], [-20, 30], [-40, 30]]
    spec = {"part_name": "profil", "base": {"shape": "profile", "points": pts, "height": 15},
            "features": [{"type": "hole", "x": 20, "y": -20, "diameter": 8, "through": True}]}
    r, e = build_and_export(spec)
    area = 80 * 20 + 20 * 40
    assert_good(r, e, area * 15 - cyl_vol(8, 15))
    assert r["warnings"] == []


def test_bohrung_von_unten():
    spec = box(50, 50, 20, [{"type": "hole", "x": 10, "y": 5, "diameter": 10, "depth": 8, "face": "bottom"}])
    r, e = build_and_export(spec)
    assert_good(r, e, 50 * 50 * 20 - cyl_vol(10, 8))
    wp = S.PARTS[r["part_id"]]["wp"]
    cyl = [f for f in wp.val().Faces() if f.geomType() == "CYLINDER"]
    assert len(cyl) == 1
    bb = cyl[0].BoundingBox()
    # Blindbohrung von unten: Z 0..8, nicht gespiegelt
    assert (bb.zmin, bb.zmax) == (pytest.approx(0, abs=1e-6), pytest.approx(8, abs=1e-6))
    assert cyl[0].Center().x == pytest.approx(10) and cyl[0].Center().y == pytest.approx(5)


def test_tasche_von_unten_und_oben():
    spec = box(60, 40, 12, [{"type": "pocket", "x": 10, "y": 0, "length": 20, "width": 10, "depth": 4},
                            {"type": "pocket", "x": -10, "y": 5, "length": 10, "width": 10, "depth": 3,
                             "face": "bottom"}])
    r, e = build_and_export(spec)
    assert_good(r, e, 60 * 40 * 12 - 20 * 10 * 4 - 10 * 10 * 3)


@pytest.mark.parametrize("H", [0.5, 3, 20, 80])
def test_durchgangsbohrung_ohne_restfilm(H):
    """Durchgangsbohrung muss beide Deckflaechen durchbrechen (kein Restfilm)."""
    for face in ("top", "bottom"):
        spec = box(40, 40, H, [{"type": "hole", "x": 0, "y": 0, "diameter": 10, "through": True, "face": face}])
        r, e = build_and_export(spec)
        assert_good(r, e, 40 * 40 * H - cyl_vol(10, H))
        wp = S.PARTS[r["part_id"]]["wp"]
        for sel in (">Z", "<Z"):
            f = wp.faces(sel).val()
            assert len(f.innerWires()) == 1, f"{face}/{sel}: Bohrung bricht nicht durch"


def test_nullpunkt_konsistent():
    """Box, Zylinder und (zentriertes) Profil: Mitte der Unterseite = (0,0,0)."""
    specs = [box(30, 20, 5),
             {"base": {"shape": "cylinder", "diameter": 30, "height": 5}},
             {"base": {"shape": "profile", "points": [[-15, -10], [15, -10], [15, 10], [-15, 10]], "height": 5}}]
    for spec in specs:
        r = build(spec)
        bb = S.PARTS[r["part_id"]]["wp"].val().BoundingBox()
        assert (bb.xmin + bb.xmax) / 2 == pytest.approx(0, abs=1e-6)
        assert (bb.ymin + bb.ymax) / 2 == pytest.approx(0, abs=1e-6)
        assert bb.zmin == pytest.approx(0, abs=1e-6)
        assert r["validation"]["all_pass"]


def test_legacy_add_hole_unten_nicht_gespiegelt():
    pid = S.create_base_body("box", 100, 60, 20)["part_id"]
    S.add_hole(pid, 30, 15, 6, face="<Z")
    S.add_pocket(pid, -20, -10, 10, 8, 2, face="<Z")
    S.set_expected_dimensions(pid, {"holes": [{"x": 30, "y": 15, "d": 6}]})
    assert S.validate(pid)["all_pass"]
    # Taschenboden (Tiefe 2 von unten) liegt waagerecht bei Z=2 und muss bei (-20, -10) sitzen
    floor = [f for f in S.PARTS[pid]["wp"].faces("%PLANE").vals() if abs(f.Center().z - 2) < 1e-6]
    assert len(floor) == 1
    assert (floor[0].Center().x, floor[0].Center().y) == (pytest.approx(-20), pytest.approx(-10))


# ---------------------------------------------------------------- Fehlerfaelle

def test_fehler_bohrung_ausserhalb():
    spec = box(100, 60, 20, [{"type": "hole", "x": 200, "y": 0, "diameter": 10, "through": True}])
    r, e = build_and_export(spec)
    v = e["validation"]
    assert v["all_pass"] is False
    assert [c for c in v["checks"] if not c["pass"]][0]["check"] == "hole"


def test_fehler_unbekannter_feature_typ():
    spec = box(50, 50, 10, [{"type": "thread", "x": 0, "y": 0, "size": "M8"}])
    r = build(spec)
    assert len(r["unsupported"]) == 1 and "'thread'" in r["unsupported"][0]


def test_fehler_muster_nur_als_text():
    spec = box(80, 60, 10, [{"type": "hole", "x": 0, "y": 0, "diameter": 6, "through": True,
                             "pattern": "4x auf Teilkreis 40"}])
    r = build(spec)
    assert any("Muster nur als Text" in w for w in r["warnings"])
    assert r["cadquery_code"].count(".circle(") == 1


def test_fehler_unbekannter_mustertyp():
    spec = box(80, 60, 10, [{"type": "hole", "x": 0, "y": 0, "diameter": 6, "through": True,
                             "pattern": {"type": "hex"}}])
    assert any("Unbekannter Mustertyp" in w for w in build(spec)["warnings"])


def test_fehler_senkung_ohne_tiefe_wird_nicht_geraten():
    spec = box(50, 50, 10, [{"type": "hole", "x": 0, "y": 0, "diameter": 6, "through": True,
                             "counterbore_diameter": 11}])
    r = build(spec)
    assert any("ohne counterbore_depth" in u for u in r["unsupported"])
    assert r["cadquery_code"].count(".circle(") == 1


def test_fehler_blindbohrung_ohne_tiefe():
    r = S.build_from_json(box(50, 50, 10, [{"type": "hole", "x": 0, "y": 0, "diameter": 6, "through": False}]))
    assert r["ok"] is False and "keine depth" in r["error"]


def test_fehler_seitenflaeche_nicht_unterstuetzt():
    r = build(box(50, 50, 10, [{"type": "hole", "x": 0, "y": 0, "diameter": 6, "face": ">X"}]))
    assert any("nicht unterstuetzt" in u for u in r["unsupported"])


def test_fehler_profil_nicht_zentriert_warnt():
    spec = {"base": {"shape": "profile", "points": [[0, 0], [100, 0], [100, 40], [0, 40]], "height": 10}}
    assert any("nicht um den Nullpunkt zentriert" in w for w in build(spec)["warnings"])


def test_eckenrundung_ist_keine_bohrung():
    """Regression: Fillet-Zylinder R5 bei (45,25) darf eine fehlende Bohrung D10 nicht 'finden'."""
    pid = S.create_base_body("box", 100, 60, 20)["part_id"]
    S.add_fillet_or_chamfer(pid, "fillet", 5)
    S.set_expected_dimensions(pid, {"holes": [{"x": 45, "y": 25, "d": 10}]})
    assert S.validate(pid)["all_pass"] is False


# ---------------------------------------------------------------- Injection / Whitelist

@pytest.mark.parametrize("feat", [
    {"type": "fillet", "edges": '|Z").cut(__import__("os").system("x"))#', "size": 1},
    {"type": "hole", "x": 0, "y": 0, "diameter": 5, "face": 'top"); import os; ("'},
])
def test_injection_wird_abgewiesen(feat):
    code, info = generate(box(50, 50, 10, [feat]))
    assert "import os" not in code and "__import__" not in code
    assert info["unsupported"]


@pytest.mark.parametrize("bad", ["1); import os; (1", "nan", None, "inf"])
def test_injection_zahlenfelder(bad):
    r = S.build_from_json(box(50, 50, 10, [{"type": "hole", "x": bad, "y": 0, "diameter": 5}]))
    assert r["ok"] is False


def test_dateiname_kein_pfadausbruch(tmp_out):
    r = build(box(10, 10, 10, name="../../boese"))
    assert "/" not in r["part_id"] and not r["part_id"].startswith(".")
    e = S.export_step(r["part_id"], filename="../../x")
    assert os.path.dirname(e["path"]) == str(tmp_out)

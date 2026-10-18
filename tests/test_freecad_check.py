"""scripts/freecad_check.py: Schwelle (--tol) und Prüfung einer Toleranzmitten-STEP (--mid) mit einem Ersatz für freecadcmd."""

import json
import subprocess
import sys
from pathlib import Path

from d2c.demo import DATA

ROOT = Path(__file__).parents[1]
DEMO_SPEC = DATA / "demo_spec.json"
FREECAD_CHECK = ROOT / "scripts" / "freecad_check.py"


def stub_freecad(tmp_path: Path, measured: dict) -> Path:
    """Ein Ersatz für freecadcmd, der eine feste Messung schreibt, wo das Skript sie erwartet."""
    exe = tmp_path / "freecadcmd"
    exe.write_text(f"#!{sys.executable}\nimport json, os\njson.dump({measured!r}, open(os.environ['D2C_FC_OUT'], 'w'))\n")
    exe.chmod(0o755)
    return exe


def measured(bore: float = 16.0, bbox=(80, 50, 25)) -> dict:
    """Das Demoteil, dessen Bohrung Ø16 den Durchmesser `bore` hat."""
    holes = [(bore, (0, 0)), (6.6, (30, 15)), (6.6, (-30, 15)), (6.6, (30, -15)), (6.6, (-30, -15))]
    cylinders = [{"d": d, "axis": [0, 0, -1], "point": [x, y, 10], "concave": True} for d, (x, y) in holes]
    return {"version": "1.1.4", "valid": True, "solids": 1, "volume": 43993.2, "bbox": list(bbox), "cylinders": cylinders}


def mid_report(actual_bore: float, actual_x: float = 80.0) -> dict:
    """report_mid.json im Format des Exports: die vom Aufbau gemessenen Werte je Prüfung, Bohrungen in der Reihenfolge von expected."""
    spec = json.loads(DEMO_SPEC.read_text("utf-8"))["expected"]
    dims = [
        {"check": "bbox_x", "expected": 80.0, "actual": actual_x, "ok": True, "dim_ids": []},
        {"check": "bbox_y", "expected": 50.0, "actual": 50.0, "ok": True, "dim_ids": []},
        {"check": "bbox_z", "expected": 25.0, "actual": 25.0, "ok": True, "dim_ids": []},
    ]
    for hole in spec["holes"]:
        d = actual_bore if hole["d"] == 16 else hole["d"]
        dims.append(
            {
                "check": "hole",
                "expected": {k: hole[k] for k in ("d", "at", "axis")},
                "actual": {"d": d, "dist": 0.0, "concave": True},
                "ok": True,
                "dim_ids": hole["dim_ids"],
            }
        )
    return {"ok": True, "dims": dims}


def run_check(tmp_path: Path, report: dict, *extra: str, name: str = "part.step") -> subprocess.CompletedProcess:
    step = tmp_path / name
    step.write_text("dummy")
    cmd = [
        sys.executable,
        str(FREECAD_CHECK),
        str(step),
        "--freecad",
        str(stub_freecad(tmp_path, report)),
        "--spec",
        str(DEMO_SPEC),
        *extra,
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_tol_widens_the_threshold(tmp_path):
    assert run_check(tmp_path, measured(16.04)).returncode == 1  # Standard: 0,01 mm
    done = run_check(tmp_path, measured(16.04), "--tol", "0.05")
    assert done.returncode == 0, done.stdout + done.stderr


def test_mid_takes_expected_values_from_report_mid(tmp_path):
    (tmp_path / "report_mid.json").write_text(json.dumps(mid_report(16.009)))
    assert run_check(tmp_path, measured(16.009), "--tol", "0.001").returncode == 1  # Nenn-Spec: Ø16 ≠ 16,009 (Toleranz zu klein)
    done = run_check(tmp_path, measured(16.009), "--mid", "--tol", "0.001", name="part_mid.step")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "hole d=16.009" in done.stdout


def test_mid_still_fails_a_wrong_part(tmp_path):
    (tmp_path / "report_mid.json").write_text(json.dumps(mid_report(16.009)))
    done = run_check(tmp_path, measured(16.2), "--mid", name="part_mid.step")
    assert done.returncode == 1 and "FAILED" in done.stdout


def test_mid_uses_measured_bbox(tmp_path):
    (tmp_path / "report_mid.json").write_text(json.dumps(mid_report(16.0, actual_x=79.95)))
    done = run_check(tmp_path, measured(16.0, bbox=(79.95, 50, 25)), "--mid", name="part_mid.step")
    assert done.returncode == 0, done.stdout + done.stderr


def test_mid_without_report_exits_1(tmp_path):
    done = run_check(tmp_path, measured(), "--mid")
    assert done.returncode == 1 and "report_mid.json" in done.stderr

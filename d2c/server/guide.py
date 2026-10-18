"""Kompakte Aufbau-Berichte und Folgeschritte für das Modell: kurze Zeilen statt des vollen Worker-Berichts.

Reine Funktionen auf Wörterbüchern (Bericht, Spezifikation, Vorgangsliste), ohne CadQuery.
"""

import json
import re

from d2c.errors import NO_EFFECT
from d2c.spec import PartSpec

MAX_STEPS = 5  # höchstens so viele Zeilen in `next`
MAX_UNBOUND = 6  # höchstens so viele Maße ohne refs in einer Zeile
MAX_MISSING = 3  # höchstens so viele gedruckte Ø/R, die im Modell fehlen
# Ab diesem Anteil gilt eine Lochachse als parallel zu einer Koordinatenachse. Bewusst gröber als spec.AXIS_COS
# (0,9999, Abnahme): Die Lochachse stammt aus einer vom Modell gelesenen Zeichnung und dient nur der Wahl der Skizzenebene.
HOLE_AXIS_COS = 0.999
DIAMETER_TOL = 0.02  # mm: so genau muss ein gedruckter Ø in solid.cyl_d vorkommen
BOUND_KINDS = ("length", "diameter", "radius", "angle", "chamfer")  # Maße, die an einen Op-Wert gehören
EPS = 1e-3  # mm: Rand, ab dem ein Punkt außerhalb der bbox liegt
PLANES = {
    2: ("XY", (0, 1), "zmax"),
    0: ("YZ", (1, 2), "xmax"),
    1: ("XZ", (0, 2), "ymin"),
}  # Achse -> Ebene, Lagekoordinaten, Eintrittsfläche


def _n(x: float) -> float | int:
    """Zahl für die Ausgabe: auf drei Stellen gerundet, ganze Werte ohne Nachkomma, kein -0."""
    x = round(x, 3) + 0.0
    return int(x) if x == int(x) else x


def _js(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _is_toleranced(dim) -> bool:
    return dim.fit is not None or dim.tol_upper is not None or dim.tol_lower is not None


def _failed_line(check: dict) -> str:
    want = check["expected"]
    if check["check"] != "hole":
        return f"{check['check']}: expected {_n(want)}, actual {_n(check['actual'])}"
    line = f"Hole Ø{_n(want['d'])} at {_js([_n(v) for v in want['at']])} axis {_js([_n(v) for v in want['axis']])}: missing"
    near = check.get("actual")
    if near:
        kind = "" if near.get("concave") else ", convex"
        line += f" (nearest cylinder Ø{near['d']:.2f}, distance {near['dist']:.2f}{kind})"
    return line


def _warnings(report: dict) -> list[dict]:
    """Die Aufbauhinweise mit Warnung; die übrigen (drilled_d, holes, edges_selected …) sind keine Meldung."""
    return [n for n in report.get("build_notes", []) if n.get("warning")]


def _error_index(report: dict) -> int | None:
    found = re.search(r"\bop (\d+)", report.get("error") or "")
    return int(found[1]) if found else report.get("op_index")


def _hole_calls(failed: list[dict], bbox: list[float], part: str) -> list[str]:
    """Je (Ø, Achse) ein fertiger add_holes-Aufruf für die fehlenden Bohrungen aus expected.holes."""
    groups: dict[tuple[float, int], list[dict]] = {}
    for check in failed:
        want = check["expected"]
        axis = max(range(3), key=lambda k: abs(want["axis"][k]))
        norm = sum(c * c for c in want["axis"]) ** 0.5
        if abs(want["axis"][axis]) / norm >= HOLE_AXIS_COS:
            groups.setdefault((want["d"], axis), []).append(check)
    calls = []
    for (d, axis), checks in groups.items():
        plane, (u, v), face = PLANES[axis]
        offset = {"zmax": bbox[5], "xmax": bbox[3], "ymin": -bbox[1]}[face]
        positions = [[_n(c["expected"]["at"][u]), _n(c["expected"]["at"][v])] for c in checks]
        dim_ids = checks[0].get("dim_ids") or []
        refs = f", refs={_js({'d': dim_ids[0]})}" if dim_ids else ""
        calls.append(
            f'add_holes(part="{part}", plane={_js({"name": plane, "offset": _n(offset)})}, d={_n(d)}, '
            f"positions={_js(positions)}{refs})  # through hole assumed"
        )
    return calls


def _outside(check: dict, bbox: list[float]) -> bool:
    """Ob eine achsparallele Soll-Bohrung quer zu ihrer Achse um mehr als ihren Radius neben dem Körper liegt."""
    want = check["expected"]
    axis = max(range(3), key=lambda k: abs(want["axis"][k]))
    if abs(want["axis"][axis]) / sum(c * c for c in want["axis"]) ** 0.5 < HOLE_AXIS_COS:
        return False  # schräge Achse: `at` ist nur ein Punkt der Geraden
    margin = want["d"] / 2 + EPS
    return any(not bbox[k] - margin <= want["at"][k] <= bbox[k + 3] + margin for k in range(3) if k != axis)


def _outside_line(checks: list[dict], bbox: list[float]) -> str:
    holes = ", ".join(f"Ø{_n(c['expected']['d'])} at {_js([_n(v) for v in c['expected']['at']])}" for c in checks)
    extent = ", ".join(f"{a} {_n(bbox[k])}…{_n(bbox[k + 3])}" for k, a in enumerate("xyz"))
    return (
        f"Hole {holes} lies outside the body ({extent}): the base body is misplaced. "
        f"Shift its profile (edit_op index 0) until it encloses the hole positions; flip does not help here."
    )


def _unbound_line(spec: PartSpec, ops: list[dict]) -> str | None:
    bound = {dim_id for op in ops for dim_id in op.get("refs", {}).values()}
    open_dims = [d for d in spec.dimensions if d.kind in BOUND_KINDS and d.id not in bound]
    if not open_dims:
        return None
    open_dims.sort(key=lambda d: not _is_toleranced(d))  # tolerierte zuerst; die Reihenfolge der Spezifikation bleibt
    shown = ", ".join(f'{d.id} "{d.text or _n(d.value)}"' + (" (toleranced)" if _is_toleranced(d) else "") for d in open_dims[:MAX_UNBOUND])
    return f"Dimensions without refs: {shown}. Bind them to the op value, also as a half dimension or profile coordinate (Ø68 to 34)."


def _missing_diameters(spec: PartSpec, cyl_d: list[float]) -> str | None:
    missing = []
    for d in spec.dimensions:
        if d.assumed or d.kind not in ("diameter", "radius"):
            continue
        diameter = d.value * (2 if d.kind == "radius" else 1)
        if not any(abs(diameter - c) <= DIAMETER_TOL for c in cyl_d):
            missing.append(f"{'Ø' if d.kind == 'diameter' else 'R'}{_n(d.value)} ({d.id})")
    if not missing:
        return None
    return (
        f"{', '.join(missing[:MAX_MISSING])} does not occur in the model (cylinder Ø: "
        f"{', '.join(str(_n(c)) for c in cyl_d)}): is a feature missing? Pin: extrude with a circle profile and "
        f'plane.offset on the seating face, mode="add"; hole: add_holes; R: corner_radius in the profile. '
        f"Pitch circles do not count."
    )


def submit_next(spec: PartSpec) -> list[str]:
    """Der erste Schritt nach submit_spec."""
    x, y, z = (_n(v) for v in spec.expected.bbox)
    return [
        f"Base body: extrude (prismatic) or revolve (turned part, axis Z; stepped shaft with "
        f"steps=[[Ø, length], …]); expected bbox {x} x {y} x {z}."
    ]


def _error_steps(report: dict, ops: list[dict], part: str) -> list[str]:
    """Die Folgeschritte, wenn der Aufbau mit einem Fehler endete."""
    i = _error_index(report)
    kept = "Not applied, the operation list is unchanged. "
    if i is None:
        return [f"{kept}Build failed: {report['error']}"]
    if i >= len(ops):
        return [f"{kept}Correct the arguments of this operation and call the tool again."]
    return [
        f'{kept}Error in op {i}: change it with edit_op(part="{part}", index={i}, op=<corrected operation>) '
        f'or delete it with delete_op(part="{part}", index={i}).'
    ]


def _solid_steps(solid: dict, last: int, part: str) -> list[str]:
    """Die Folgeschritte zu einem zerfallenen oder ungültigen Körper; `last` ist der Index der letzten Op."""
    if (solid.get("solids") or 1) > 1 and last > 0:
        return [
            f"op {last} does not touch the existing body (solids={solid['solids']}). If it should replace the base "
            f'body: edit_op(part="{part}", index=0, op=<new base body>), then delete_op(part="{part}", index={last}).'
        ]
    if not solid.get("ok", True):
        return [
            f"Body invalid (valid={solid.get('valid')}, solids={solid.get('solids')}, "
            f"free_edges={solid.get('free_edges')}): change the last step with edit_op or delete_op."
        ]
    return []


def _dimension_steps(report: dict, solid: dict, part: str) -> list[str]:
    """Die Folgeschritte zu fehlgeschlagenen Maß- und Bohrungsprüfungen und zu Schnitten ohne Wirkung."""
    steps = []
    failed = [c for c in report.get("dims", []) if not c["ok"]]
    holes = [c for c in failed if c["check"] == "hole"]
    outside = [c for c in holes if _outside(c, solid["bbox"])] if solid.get("bbox") else []
    if outside:
        steps.append(_outside_line(outside, solid["bbox"]))
    else:
        steps += [
            f"{NO_EFFECT} (op {n['op_index']}): check plane and offset, possibly flip=true (edit_op)."
            for n in _warnings(report)
            if NO_EFFECT in n["warning"]
        ]
    if solid.get("bbox"):
        steps += _hole_calls([c for c in holes if c not in outside], solid["bbox"], part)
    for c in failed:
        if c["check"].startswith("bbox_"):
            reason = "material missing" if c["actual"] < c["expected"] else "too large"
            steps.append(f"{c['check']}: actual {_n(c['actual'])}, expected {_n(c['expected'])}: {reason}; check profile and distance.")
    return steps


def next_steps(spec: PartSpec, ops: list[dict], report: dict, part: str) -> list[str]:
    """Höchstens MAX_STEPS Zeilen, was als Nächstes zu tun ist, in der Rangfolge der Dringlichkeit."""
    if not ops:
        return submit_next(spec)
    if report.get("error"):
        return _error_steps(report, ops, part)
    solid = report.get("solid") or {}
    steps = _solid_steps(solid, len(ops) - 1, part) + _dimension_steps(report, solid, part)
    if not report.get("roundtrip", {}).get("ok", True):
        steps.append("STEP re-import or marking failed: check `mark_near` (submit_spec with keep_ops=true).")
    if line := _unbound_line(spec, ops):
        steps.append(line)
    if solid.get("cyl_d") is not None and (line := _missing_diameters(spec, solid["cyl_d"])):
        steps.append(line)
    if report.get("ok"):
        steps.append(f'export_step(part="{part}")')
        if any(_is_toleranced(d) for d in spec.dimensions):
            steps.append(f'export_step(part="{part}", variant="mid")  # mid-tolerance')
    return steps[:MAX_STEPS]


def compact(report: dict, spec: PartSpec, ops: list[dict], part: str) -> dict:
    """Der Bericht als kurze Antwort für das Modell: Ergebnis, fehlgeschlagene Prüfungen, Warnungen und `next`."""
    solid = report.get("solid")
    dims = report.get("dims") or []
    out = {"ok": report["ok"], "error": report.get("error"), "op_index": report.get("op_index")}
    if report.get("coerced"):
        out["coerced"] = report["coerced"]
    out |= {
        "failed": [_failed_line(c) for c in dims if not c["ok"]],
        "passed": sum(c["ok"] for c in dims),
        "next": next_steps(spec, ops, report, part),
        "notes": [f"op {n['op_index']}: {n['warning']}" for n in _warnings(report)],
        "volume": round(solid["volume"], 2) if solid else None,
        "bbox": [_n(v) for v in solid["bbox"]] if solid else None,
    }
    return out

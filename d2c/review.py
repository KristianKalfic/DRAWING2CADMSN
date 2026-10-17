"""The review list of one part: what was assumed, where every value came from, what the checks found.

Pure functions over the spec, the dumped ops and the worker report; the session writes the files.
Each section of the Markdown is one function, render() joins them.
"""

from collections.abc import Iterator

from d2c.checks import VOLUME_REL_TOL
from d2c.ops import get_path
from d2c.spec import PartSpec

# Op parameters that are sizes; revolve points count through the r of each (r, z) point.
SIZE_NAMES = frozenset(
    "d w h distance size corner_radius width center_distance depth pitch_d cbore_d cbore_depth csk_d".split())
VALUE_TOL = 0.01  # mm: an op value this close to a Dimension.value counts as that dimension


def _g(x: float) -> str:
    return f"{x:g}"


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[object]]) -> str:
    if not rows:
        return "None."
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _numbers(node: object, path: tuple[str, ...] = ()) -> Iterator[tuple[str, float]]:
    """Every number of a dumped op with its dotted path; the refs mapping is not a parameter."""
    if isinstance(node, dict):
        for key, child in node.items():
            if key != "refs":
                yield from _numbers(child, (*path, key))
    elif isinstance(node, (list, tuple)):
        for k, child in enumerate(node):
            yield from _numbers(child, (*path, str(k)))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield ".".join(path), node


def _is_size(op: dict, path: str) -> bool:
    keys = path.split(".")
    return keys[-1] in SIZE_NAMES or (op["op"] == "revolve" and keys[0] == "points" and keys[-1] == "0")


def assumptions(spec: PartSpec, report: dict) -> list[dict]:
    """One entry per review item, with the source cells of its refs and the number of faces marked in the STEP."""
    dims = {d.id: d for d in spec.dimensions}
    faces = {m["id"]: m["faces"] for m in (report.get("roundtrip") or {}).get("marks", [])}
    return [
        {
            "id": r.id,
            "refs": [{"id": ref, "text": dims[ref].text if ref in dims else ""} for ref in r.refs],
            "issue": r.issue,
            "assumption": r.assumption,
            "reason": r.reason,
            "sources": [f"{dims[ref].source.view}/{dims[ref].source.cell or '-'}"
                        for ref in r.refs if ref in dims and dims[ref].source],
            "marked_faces": faces.get(r.id) if r.mark_near else None,
        }
        for r in spec.review
    ]


def traceability(spec: PartSpec, ops: list[dict]) -> list[dict]:
    """One entry per refs mapping of an op: parameter path, its value and the dimension it came from."""
    dims = {d.id: d for d in spec.dimensions}
    return [
        {"op_index": i, "path": path, "value": get_path(op, path), "dimension": dim_id, "text": dims[dim_id].text}
        for i, op in enumerate(ops)
        for path, dim_id in op.get("refs", {}).items()
    ]


def unreferenced(spec: PartSpec, ops: list[dict]) -> list[dict]:
    """Size parameters with neither a refs entry nor a dimension of the same value: derived or invented values."""
    values = [d.value for d in spec.dimensions]
    return [
        {"op_index": i, "path": path, "value": value}
        for i, op in enumerate(ops)
        for path, value in _numbers(op)
        if _is_size(op, path) and path not in op.get("refs", {})
        and not any(abs(value - v) <= VALUE_TOL for v in values)
    ]


def _check_row(entry: dict) -> dict:
    expected, actual = entry["expected"], entry["actual"]
    if entry["check"] == "hole":
        want = f"Ø{_g(expected['d'])} at {expected['at']} axis {expected['axis']}"
        got = "no cylinder" if actual is None else f"Ø{_g(actual['d'])}, {_g(actual['dist'])} off axis"
    else:
        want, got = _g(expected), _g(actual)
    ids = f" ({', '.join(entry['dim_ids'])})" if entry["dim_ids"] else ""
    return {"check": entry["check"] + ids, "expected": want, "actual": got, "ok": entry["ok"]}


def validation(report: dict) -> list[dict]:
    """The checks of the worker report as rows of check, expected, actual and ok."""
    if report["error"]:
        return [{"check": "build", "expected": "no error", "actual": report["error"], "ok": False}]
    solid, trip = report["solid"], report["roundtrip"]
    rows = [
        ("valid solid", "True", solid["valid"], solid["valid"]),
        ("solid count", "1", solid["solids"], solid["solids"] == 1),
        ("free edges", "0", solid["free_edges"], solid["free_edges"] == 0),
        ("volume mm3", "> 0", _g(solid["volume"]), solid["volume"] > 0),
        *((r["check"], r["expected"], r["actual"], r["ok"]) for r in map(_check_row, report["dims"])),
        ("STEP re-import valid", "True", trip["reimport_valid"], trip["reimport_valid"]),
        ("STEP re-import solids", "1", trip["solids"], trip["solids"] == 1),
        ("STEP volume change", f"<= {VOLUME_REL_TOL:g}", f"{trip['volume_rel_diff']:.2e}",
         trip["volume_rel_diff"] <= VOLUME_REL_TOL),
        *((f"marked faces {m['id']}", "> 0", m["faces"], m["faces"] > 0) for m in trip["marks"]),
    ]
    return [{"check": c, "expected": str(e), "actual": str(a), "ok": bool(ok)} for c, e, a, ok in rows]


def to_json(spec: PartSpec, ops: list[dict], report: dict) -> dict:
    """The review list as data, for the web view and the evaluation."""
    rows = validation(report)
    return {
        "assumptions": assumptions(spec, report),
        "notes": [{"id": n.id, "text": n.text} for n in spec.notes],
        "traceability": traceability(spec, ops),
        "unreferenced": unreferenced(spec, ops),
        "validation": rows,
        "all_ok": all(r["ok"] for r in rows),
    }


def _header(spec: PartSpec) -> str:
    return "\n".join([
        f"# Review: {spec.title}",
        "",
        f"- Part: {spec.name}",
        f"- Drawing: {spec.drawing} (page {spec.page})",
        f"- Scale: {spec.scale}",
        f"- General tolerance: {spec.general_tolerance or 'not stated'}",
    ])


def _assumptions_section(data: dict) -> str:
    rows = [
        [a["id"], ", ".join(f"{r['id']} ({r['text']})" if r["text"] else r["id"] for r in a["refs"]) or "-",
         a["issue"], a["assumption"], a["reason"], ", ".join(a["sources"]) or "-",
         "-" if a["marked_faces"] is None else f"{a['marked_faces']} face(s)"]
        for a in data["assumptions"]
    ]
    headers = ["id", "Dimension / feature", "Issue", "Assumption", "Reason", "Source", "Marked faces in STEP"]
    return "## Assumptions and open issues\n" + _table(headers, rows)


def _notes_section(data: dict) -> str:
    return "## Drawing notes\n" + ("\n".join(f"- {n['id']}: {n['text']}" for n in data["notes"]) or "None.")


def _traceability_section(data: dict) -> str:
    rows = [[t["op_index"], t["path"], _g(t["value"]), t["dimension"], t["text"]] for t in data["traceability"]]
    return "## Traceability\n" + _table(["Op", "Parameter", "Value", "Dimension", "Printed text"], rows)


def _unreferenced_section(data: dict) -> str:
    rows = [[u["op_index"], u["path"], _g(u["value"])] for u in data["unreferenced"]]
    return "## Values without dimension reference\n" + _table(["Op", "Parameter", "Value"], rows)


def _validation_section(data: dict) -> str:
    rows = [[v["check"], v["expected"], v["actual"], "yes" if v["ok"] else "no"] for v in data["validation"]]
    return "## Validation\n" + _table(["Check", "Expected", "Actual", "ok"], rows)


def render(spec: PartSpec, ops: list[dict], report: dict) -> str:
    """The review list as English Markdown."""
    data = to_json(spec, ops, report)
    sections = [
        _header(spec),
        _assumptions_section(data),
        _notes_section(data),
        _traceability_section(data),
        _unreferenced_section(data),
        _validation_section(data),
        f"All checks passed: {'yes' if data['all_ok'] else 'no'}",
    ]
    return "\n\n".join(sections) + "\n"

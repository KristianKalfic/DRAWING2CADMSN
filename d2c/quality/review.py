"""Prüfliste eines Bauteils: getroffene Annahmen, Herkunft aller Werte und Ergebnisse der Prüfungen.

Reine Funktionen über Spezifikation, serialisierte Vorgänge, Worker-Bericht sowie die Toleranz- und PMI-Zeilen,
die die Sitzung übergibt; die Sitzung schreibt die Dateien. Jeder Abschnitt des Markdowns ist eine Funktion, render() fügt sie zusammen.

Die Prüfungen zeigen die Konsistenz des Modells mit der erfassten Spezifikation (`expected` und Maße, die der
Agent aus der Zeichnung übernommen hat), nicht die Übereinstimmung mit der Zeichnung selbst. Ob ein gedrucktes
Maß im Modell vorkommt, steht getrennt im Abschnitt "Dimensions without model reference".
"""

import ast
from collections.abc import Iterator, Sequence

from d2c.oppath import get_path
from d2c.spec import VOLUME_REL_TOL, PartSpec

# Vorgangsparameter mit Längenmaß; bei `revolve` zählt der r-Wert jedes (r, z)-Punkts.
SIZE_NAMES = frozenset("d w h distance size corner_radius width center_distance depth pitch_d cbore_d cbore_depth csk_d".split())
VALUE_TOL = 0.01  # mm: Ein Vorgangswert mit diesem Abstand zu Dimension.value gilt als dasselbe Maß.
BASIS = "Consistency with the captured specification"  # Worauf sich "All checks passed" bezieht
SCRIPT_NOTE = "Script operation"
SOURCES = {"explicit": "on dimension", "fit": "fit ISO 286", "general": "general tolerance ISO 2768"}


def _g(x: float) -> str:
    return f"{x:g}"


def _cell(text: object) -> str:
    return " ".join(str(text).replace("\\", "\\\\").replace("|", "\\|").splitlines())


def _table(headers: list[str], rows: list[list[object]]) -> str:
    if not rows:
        return "No entries."
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _numbers(node: object, path: tuple[str, ...] = ()) -> Iterator[tuple[str, float]]:
    """Alle Zahlen eines serialisierten Vorgangs mit ihrem punktgetrennten Pfad; `refs` ist kein Parameter."""
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


def _script_numbers(code: str) -> list[tuple[int, float]]:
    """(Zeile, Wert) aller Zahlenliterale eines Skripts in Quelltextreihenfolge; 0 ist keine Maßangabe."""
    try:
        tree = ast.parse(code)
    except SyntaxError:  # der Aufbau hat das Skript schon ausgeführt; nur ein veralteter Stand kann hier landen
        return []
    negated = {id(n.operand) for n in ast.walk(tree) if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub)}
    found = [
        (n.lineno, n.col_offset, -n.value if id(n) in negated else n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool) and n.value != 0
    ]
    return [(line, value) for line, _, value in sorted(found)]


def assumptions(spec: PartSpec, report: dict) -> list[dict]:
    """Ein Eintrag je Prüfeintrag mit Rasterzellen der refs und der Anzahl markierter Flächen in der STEP-Datei."""
    dims = {d.id: d for d in spec.dimensions}
    faces = {m["id"]: m["faces"] for m in (report.get("roundtrip") or {}).get("marks", [])}
    return [
        {
            "id": r.id,
            "refs": [{"id": ref, "text": dims[ref].text if ref in dims else ""} for ref in r.refs],
            "issue": r.issue,
            "assumption": r.assumption,
            "reason": r.reason,
            "sources": [f"{dims[ref].source.view}/{dims[ref].source.cell or '-'}" for ref in r.refs if ref in dims and dims[ref].source],
            "marked_faces": faces.get(r.id) if r.mark_near else None,
        }
        for r in spec.review
    ]


def traceability(spec: PartSpec, ops: list[dict]) -> list[dict]:
    """Ein Eintrag je `refs`-Zuordnung eines Vorgangs: Parameterpfad, Wert und zugehöriges Maß."""
    dims = {d.id: d for d in spec.dimensions}
    return [
        {"op_index": i, "path": path, "value": get_path(op, path), "dimension": dim_id, "text": dims[dim_id].text}
        for i, op in enumerate(ops)
        for path, dim_id in op.get("refs", {}).items()
    ]


def unreferenced(spec: PartSpec, ops: list[dict]) -> list[dict]:
    """Längenparameter ohne `refs`-Eintrag und ohne Maß mit demselben Wert: abgeleitete oder frei gewählte Werte.

    In einem `script` kann kein Wert an ein Maß gebunden werden: Jedes Zahlenliteral des Codes steht mit seiner Zeile
    in der Liste, mit dem Hinweis "Skript-Operation".
    """
    values = [d.value for d in spec.dimensions]
    found = []
    for i, op in enumerate(ops):
        if op["op"] == "script":
            found += [
                {"op_index": i, "path": f"Line {line}", "value": value, "note": SCRIPT_NOTE} for line, value in _script_numbers(op["code"])
            ]
            continue
        found += [
            {"op_index": i, "path": path, "value": value}
            for path, value in _numbers(op)
            if _is_size(op, path) and path not in op.get("refs", {}) and not any(abs(value - v) <= VALUE_TOL for v in values)
        ]
    return found


def _check_row(entry: dict) -> dict:
    expected, actual = entry["expected"], entry["actual"]
    if entry["check"] == "hole":
        want = f"Ø{_g(expected['d'])} at {expected['at']}, axis {expected['axis']}"
        got = "no cylinder" if actual is None else f"Ø{_g(actual['d'])}, {_g(actual['dist'])} from axis"
        if entry.get("entry") is False:
            got += ", no access from outside (closed cavity)"
        elif entry.get("through") is not None:
            got += ", through" if entry["through"] else ", not through (blind hole or closed)"
    else:
        want, got = _g(expected), _g(actual)
    ids = f" ({', '.join(entry['dim_ids'])})" if entry["dim_ids"] else ""
    return {"check": entry["check"] + ids, "expected": want, "actual": got, "ok": entry["ok"]}


def validation(report: dict) -> list[dict]:
    """Prüfungen aus dem Worker-Bericht als Zeilen mit Prüfung, Soll, Ist und Ergebnis."""
    if report["error"]:
        return [{"check": "Build", "expected": "no error", "actual": report["error"], "ok": False}]
    solid, trip = report["solid"], report["roundtrip"]
    pmi = trip["pmi"]
    rows = [
        ("Valid solid", True, solid["valid"], solid["valid"]),
        ("Number of solids", "1", solid["solids"], solid["solids"] == 1),
        ("Free edges", "0", solid["free_edges"], solid["free_edges"] == 0),
        ("Volume mm³", "> 0", _g(solid["volume"]), solid["volume"] > 0),
        *((r["check"], r["expected"], r["actual"], r["ok"]) for r in map(_check_row, report["dims"])),
        ("STEP re-import valid", True, trip["reimport_valid"], trip["reimport_valid"]),
        ("Solids in STEP re-import", "1", trip["solids"], trip["solids"] == 1),
        (
            "Volume change on STEP re-import",
            f"<= {VOLUME_REL_TOL:g}",
            f"{trip['volume_rel_diff']:.2e}",
            trip["volume_rel_diff"] <= VOLUME_REL_TOL,
        ),
        # 0 gegen 0 gilt nicht als erfüllt, solange angenommene Maße ein PMI bekommen sollten
        (
            "STEP PMI read back",
            pmi["written"],
            pmi["read_back"],
            pmi["read_back"] == pmi["written"] and not (pmi["written"] == 0 and pmi.get("assumed")),
        ),
        (
            "Geometric tolerances read back",
            pmi.get("gdt_written", 0),
            pmi.get("gdt_read_back", 0),
            pmi.get("gdt_read_back", 0) == pmi.get("gdt_written", 0),
        ),
        *((f"Marked faces {m['id']}", "> 0", m["faces"], m["faces"] > 0) for m in trip["marks"]),
    ]
    return [
        {
            "check": c,
            "expected": "yes" if e is True else "no" if e is False else str(e),
            "actual": "yes" if a is True else "no" if a is False else str(a),
            "ok": bool(ok),
        }
        for c, e, a, ok in rows
    ]


def geometric(spec: PartSpec, report: dict) -> list[dict]:
    """Die Form- und Lagetoleranzen der Spezifikation; in_step sagt, ob sie als semantisches PMI in der STEP-Datei stehen."""
    placed = set(((report.get("roundtrip") or {}).get("pmi") or {}).get("gdt_ids", []))
    datums = {d.label: d.id for d in spec.datums}
    return [
        {
            "id": t.id,
            "kind": t.kind,
            "value": t.value,
            "text": t.text,
            "datums": t.datums,
            "datum_ids": [datums[x] for x in t.datums],
            "in_step": t.id in placed,
        }
        for t in spec.geometric_tolerances
    ]


def to_json(
    spec: PartSpec,
    ops: list[dict],
    report: dict,
    tolerances: Sequence[dict] = (),
    pmi: Sequence[dict] = (),
    changes: Sequence[dict] = (),
) -> dict:
    """Die Prüfliste als Daten, für die Webansicht und die Auswertung.

    tolerances sind die Zeilen der Toleranztabelle: {"dim_id", "text", "nominal", "lower", "upper", "mid",
    "source", "in_tolerance_nominal", "in_tolerance_mid"}, wobei None als Urteil „nicht geprüft“ heißt. pmi sind die Maße, die read_pmi in der
    STEP-Datei findet. changes sind die Einträge der Änderungsliste gegenüber der Vorversion (quality/revision.py).

    dims_found ist die Antwort des Workers, welche gedruckten Maße das Modell enthält ({"found", "missing"}, None,
    wenn unbekannt). validation und all_ok nennen die Konsistenz des Modells mit der erfassten Spezifikation
    (validation_basis), nicht die Übereinstimmung mit der Zeichnung: Die Maße in dims_found["missing"] sind nicht belegt.
    """
    rows = validation(report)
    return {
        "assumptions": assumptions(spec, report),
        "notes": [{"id": n.id, "text": n.text} for n in spec.notes],
        "traceability": traceability(spec, ops),
        "unreferenced": unreferenced(spec, ops),
        "dims_found": report.get("dims_found"),
        "tolerances": list(tolerances),
        "pmi": list(pmi),
        "geometric_tolerances": geometric(spec, report),
        "changes": list(changes),
        "warnings": list(report.get("warnings") or []),
        "validation": rows,
        "validation_basis": BASIS,
        "all_ok": all(r["ok"] for r in rows),
    }


def _header(spec: PartSpec) -> str:
    return "\n".join(
        [
            f"# Review list: {_cell(spec.title)}",
            "",
            f"- Part: {_cell(spec.name)}",
            f"- Drawing: {_cell(spec.drawing)} (page {spec.page})",
            f"- Scale: {_cell(spec.scale)}",
            f"- General tolerance: {_cell(spec.general_tolerance or 'not specified')}",
        ]
    )


def _assumptions_section(data: dict) -> str:
    issues = {
        "missing": "missing",
        "ambiguous": "ambiguous",
        "low_confidence": "low confidence",
        "conflict": "conflict",
        "convention": "convention",
    }
    rows = [
        [
            a["id"],
            ", ".join(f"{r['id']} ({r['text']})" if r["text"] else r["id"] for r in a["refs"]) or "-",
            issues.get(a["issue"], a["issue"]),
            a["assumption"],
            a["reason"],
            ", ".join(a["sources"]) or "-",
            "-" if a["marked_faces"] is None else f"Faces: {a['marked_faces']}",
        ]
        for a in data["assumptions"]
    ]
    headers = ["ID", "Dimension / feature", "Issue", "Assumption", "Reason", "Source", "Marked faces in STEP"]
    return "## Assumptions and open items\n" + _table(headers, rows)


def _notes_section(data: dict) -> str:
    return "## Drawing notes\n" + ("\n".join(f"- {_cell(n['id'])}: {_cell(n['text'])}" for n in data["notes"]) or "None.")


def _traceability_section(data: dict) -> str:
    rows = [[t["op_index"], t["path"], _g(t["value"]), t["dimension"], t["text"]] for t in data["traceability"]]
    return "## Traceability\n" + _table(["Op", "Parameter", "Value", "Dimension", "Printed text"], rows)


def _unreferenced_section(data: dict) -> str:
    rows = [[u["op_index"], u["path"], _g(u["value"]), u.get("note", "-")] for u in data["unreferenced"]]
    return "## Values without dimension reference\n" + _table(["Op", "Parameter", "Value", "Note"], rows)


def _dims_found_section(data: dict) -> str:
    found = data["dims_found"]
    title = "## Dimensions without model reference\n"
    if found is None:
        return title + "Not determined (the build returned no dimension search)."
    total = len(found["found"]) + len(found["missing"])
    intro = (
        f"Found in model: {len(found['found'])} of {total} printed length, diameter and radius dimensions "
        "(tolerance 0.05 mm; assumed dimensions, angles and count dimensions excluded). The following dimensions appear "
        "nowhere in the body and are unconfirmed.\n\n"
    )
    rows = [[m["id"], m["text"], _g(m["value"])] for m in found["missing"]]
    return title + intro + _table(["Dimension", "Printed text", "Value"], rows)


def _verdict(ok: bool | None, geometry: bool = False) -> str:
    """yes/no/not checked; ein Urteil aus dem gemessenen Körper statt aus einem gebundenen Wert trägt den Zusatz „checked (geometry)“."""
    if ok is None:
        return "not checked"
    return ("yes" if ok else "no") + (", checked (geometry)" if geometry else "")


def _tolerances_section(data: dict) -> str:
    rows = [
        [
            t["dim_id"],
            t["text"],
            _g(t["nominal"]),
            _g(round(t["nominal"] + t["lower"], 6)),
            _g(round(t["nominal"] + t["upper"], 6)),
            _g(t["mid"]),
            SOURCES.get(t["source"], t["source"]),
            _g(t["lower"]),
            _g(t["upper"]),
            _verdict(t["in_tolerance_nominal"], t.get("geometry", False)),
            _verdict(t["in_tolerance_mid"]),
        ]
        for t in data["tolerances"]
    ]
    headers = [
        "Dimension",
        "Printed text",
        "Nominal",
        "Minimum",
        "Maximum",
        "Tolerance midpoint",
        "Source",
        "Lower deviation",
        "Upper deviation",
        "Within tolerance (nominal)",
        "Within tolerance (midpoint)",
    ]
    return "## Tolerances\n" + _table(headers, rows)


def _deviation(x: float) -> str:
    return "0" if x == 0 else f"{x:+g}"


def _pmi_tolerance(entry: dict) -> str:
    """Die Passung eines PMI-Maßes oder seine Abweichungen wie +0.2/0."""
    if entry["fit"]:
        return entry["fit"]
    if entry["tol_upper"] is not None:
        return f"{_deviation(entry['tol_upper'])}/{_deviation(entry['tol_lower'])}"
    return "-"


def _pmi_section(data: dict) -> str:
    rows = [[p["type"], _g(p["value"]), _pmi_tolerance(p), ", ".join(p["faces"]), "; ".join(p["descriptions"]) or "-"] for p in data["pmi"]]
    return "## PMI in the STEP file\n" + _table(["Type", "Value", "Tolerance", "Faces", "Description"], rows)


def _geometric_section(data: dict) -> str:
    rows = [
        [t["id"], t["kind"], _g(t["value"]), ", ".join(t["datums"]) or "-", t["text"], "yes" if t["in_step"] else "no"]
        for t in data["geometric_tolerances"]
    ]
    body = _table(["ID", "Type", "Value mm", "Datums", "Frame", "PMI in STEP"], rows) if rows else "None."
    return "## Geometric tolerances\n" + body


CHANGE_AREAS = {
    "dimension": "Dimension",
    "tolerance": "Tolerance",
    "general_tolerance": "General tolerance",
    "geometric": "Geometric tolerance",
    "hole": "Hole",
    "bbox": "Bounding box",
}
CHANGE_KINDS = {"added": "added", "removed": "removed", "changed": "changed"}


def _changes_section(data: dict) -> str:
    rows = [
        [
            f"{CHANGE_AREAS.get(c['area'], c['area'])} {c['feature']}".strip(),
            c["before"],
            c["after"],
            CHANGE_KINDS.get(c["change"], c["change"]),
        ]
        for c in data["changes"]
    ]
    return "## Changes from the previous version\n" + _table(["Feature", "Before", "After", "Change"], rows)


def _warnings_section(data: dict) -> str:
    return "## Export notes\n" + ("\n".join(f"- {_cell(w)}" for w in data["warnings"]) or "None.")


def _validation_section(data: dict) -> str:
    labels = {"hole": "Hole", "bbox_x": "Overall size X", "bbox_y": "Overall size Y", "bbox_z": "Overall size Z"}
    rows = []
    for v in data["validation"]:
        name, separator, refs = v["check"].partition(" ")
        rows.append([labels.get(name, name) + separator + refs, v["expected"], v["actual"], "yes" if v["ok"] else "no"])
    note = (
        f"{BASIS}: the model is compared with the captured expected values (`expected`), not with the drawing. "
        'Whether a printed dimension appears in the body is listed in the section "Dimensions without model reference".\n\n'
    )
    return "## Checks\n" + note + _table(["Check", "Expected", "Actual", "Passed"], rows)


def render(
    spec: PartSpec,
    ops: list[dict],
    report: dict,
    tolerances: Sequence[dict] = (),
    pmi: Sequence[dict] = (),
    changes: Sequence[dict] = (),
) -> str:
    """Die Prüfliste als englisches Markdown; die Argumente sind die von to_json. Der Abschnitt der Änderungen steht nur mit `changes`."""
    data = to_json(spec, ops, report, tolerances, pmi, changes)
    sections = [
        _header(spec),
        *([_changes_section(data)] if data["changes"] else []),
        _assumptions_section(data),
        _notes_section(data),
        _traceability_section(data),
        _unreferenced_section(data),
        _dims_found_section(data),
        _tolerances_section(data),
        _pmi_section(data),
        _geometric_section(data),
        _warnings_section(data),
        _validation_section(data),
        f"All checks passed: {'yes' if data['all_ok'] else 'no'} ({BASIS}, no comparison with the drawing)",
    ]
    return "\n\n".join(sections) + "\n"

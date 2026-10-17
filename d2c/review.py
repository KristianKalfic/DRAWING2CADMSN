"""Prüfliste eines Bauteils: getroffene Annahmen, Herkunft aller Werte und Ergebnisse der Prüfungen.

Reine Funktionen über Spezifikation, serialisierte ops und Worker-Bericht; die Session schreibt die Dateien.
Jeder Markdown-Abschnitt hat eine eigene Funktion; `render()` fügt sie zusammen.
"""

from collections.abc import Iterator

from d2c.checks import VOLUME_REL_TOL
from d2c.ops import get_path
from d2c.spec import PartSpec

# Vorgangsparameter mit Längenmaß; bei `revolve` zählt der r-Wert jedes (r, z)-Punkts.
SIZE_NAMES = frozenset(
    "d w h distance size corner_radius width center_distance depth pitch_d cbore_d cbore_depth csk_d".split())
VALUE_TOL = 0.01  # mm: Ein Vorgangswert mit diesem Abstand zu Dimension.value gilt als dasselbe Maß.


def _g(x: float) -> str:
    return f"{x:g}"


def _cell(text: object) -> str:
    return " ".join(str(text).replace("\\", "\\\\").replace("|", "\\|").splitlines())


def _table(headers: list[str], rows: list[list[object]]) -> str:
    if not rows:
        return "Keine Einträge."
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
            "sources": [f"{dims[ref].source.view}/{dims[ref].source.cell or '-'}"
                        for ref in r.refs if ref in dims and dims[ref].source],
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
    """Längenparameter ohne `refs`-Eintrag und ohne Maß mit demselben Wert: abgeleitete oder frei gewählte Werte."""
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
        want = f"Ø{_g(expected['d'])} bei {expected['at']}, Achse {expected['axis']}"
        got = "kein Zylinder" if actual is None else f"Ø{_g(actual['d'])}, {_g(actual['dist'])} Abstand zur Achse"
    else:
        want, got = _g(expected), _g(actual)
    ids = f" ({', '.join(entry['dim_ids'])})" if entry["dim_ids"] else ""
    return {"check": entry["check"] + ids, "expected": want, "actual": got, "ok": entry["ok"]}


def validation(report: dict) -> list[dict]:
    """Prüfungen aus dem Worker-Bericht als Zeilen mit Prüfung, Soll, Ist und Ergebnis."""
    if report["error"]:
        return [{"check": "Aufbau", "expected": "kein Fehler", "actual": report["error"], "ok": False}]
    solid, trip = report["solid"], report["roundtrip"]
    rows = [
        ("Gültiger Volumenkörper", True, solid["valid"], solid["valid"]),
        ("Anzahl Volumenkörper", "1", solid["solids"], solid["solids"] == 1),
        ("Freie Kanten", "0", solid["free_edges"], solid["free_edges"] == 0),
        ("Volumen mm³", "> 0", _g(solid["volume"]), solid["volume"] > 0),
        *((r["check"], r["expected"], r["actual"], r["ok"]) for r in map(_check_row, report["dims"])),
        ("STEP-Rückimport gültig", True, trip["reimport_valid"], trip["reimport_valid"]),
        ("Volumenkörper im STEP-Rückimport", "1", trip["solids"], trip["solids"] == 1),
        ("Volumenänderung beim STEP-Rückimport", f"<= {VOLUME_REL_TOL:g}", f"{trip['volume_rel_diff']:.2e}",
         trip["volume_rel_diff"] <= VOLUME_REL_TOL),
        *((f"Markierte Flächen {m['id']}", "> 0", m["faces"], m["faces"] > 0) for m in trip["marks"]),
    ]
    return [{"check": c, "expected": "ja" if e is True else "nein" if e is False else str(e),
             "actual": "ja" if a is True else "nein" if a is False else str(a), "ok": bool(ok)}
            for c, e, a, ok in rows]


def to_json(spec: PartSpec, ops: list[dict], report: dict) -> dict:
    """Die Prüfliste als Daten für Webansicht und Auswertung."""
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
        f"# Prüfliste: {_cell(spec.title)}",
        "",
        f"- Bauteil: {_cell(spec.name)}",
        f"- Zeichnung: {_cell(spec.drawing)} (Seite {spec.page})",
        f"- Maßstab: {_cell(spec.scale)}",
        f"- Allgemeintoleranz: {_cell(spec.general_tolerance or 'nicht angegeben')}",
    ])


def _assumptions_section(data: dict) -> str:
    issues = {"missing": "fehlt", "ambiguous": "mehrdeutig", "low_confidence": "geringe Sicherheit",
              "conflict": "Konflikt", "convention": "Konvention"}
    rows = [
        [a["id"], ", ".join(f"{r['id']} ({r['text']})" if r["text"] else r["id"] for r in a["refs"]) or "-",
         issues.get(a["issue"], a["issue"]), a["assumption"], a["reason"], ", ".join(a["sources"]) or "-",
         "-" if a["marked_faces"] is None else f"Flächen: {a['marked_faces']}"]
        for a in data["assumptions"]
    ]
    headers = ["ID", "Maß / Merkmal", "Problem", "Annahme", "Begründung", "Quelle", "Markierte Flächen in STEP"]
    return "## Annahmen und offene Punkte\n" + _table(headers, rows)


def _notes_section(data: dict) -> str:
    return "## Zeichnungshinweise\n" + ("\n".join(f"- {_cell(n['id'])}: {_cell(n['text'])}" for n in data["notes"]) or "Keine.")


def _traceability_section(data: dict) -> str:
    rows = [[t["op_index"], t["path"], _g(t["value"]), t["dimension"], t["text"]] for t in data["traceability"]]
    return "## Rückverfolgbarkeit\n" + _table(["Vorgang", "Parameter", "Wert", "Maß", "Gedruckter Text"], rows)


def _unreferenced_section(data: dict) -> str:
    rows = [[u["op_index"], u["path"], _g(u["value"])] for u in data["unreferenced"]]
    return "## Werte ohne Maßbezug\n" + _table(["Vorgang", "Parameter", "Wert"], rows)


def _validation_section(data: dict) -> str:
    labels = {"hole": "Bohrung", "bbox_x": "Außenmaß X", "bbox_y": "Außenmaß Y", "bbox_z": "Außenmaß Z"}
    rows = []
    for v in data["validation"]:
        name, separator, refs = v["check"].partition(" ")
        rows.append([labels.get(name, name) + separator + refs, v["expected"], v["actual"], "ja" if v["ok"] else "nein"])
    return "## Prüfungen\n" + _table(["Prüfung", "Soll", "Ist", "Bestanden"], rows)


def render(spec: PartSpec, ops: list[dict], report: dict) -> str:
    """Die Prüfliste als deutsches Markdown."""
    data = to_json(spec, ops, report)
    sections = [
        _header(spec),
        _assumptions_section(data),
        _notes_section(data),
        _traceability_section(data),
        _unreferenced_section(data),
        _validation_section(data),
        f"Alle Prüfungen bestanden: {'ja' if data['all_ok'] else 'nein'}",
    ]
    return "\n\n".join(sections) + "\n"

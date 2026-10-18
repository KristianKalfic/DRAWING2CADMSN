"""Tolerante Eingabeschicht: deutet Formfehler in Werkzeugargumenten um, bevor die Validierung sie ablehnt.

Umgedeutet wird nur, was sonst abgelehnt oder still verworfen würde. Gültige Eingaben bleiben unverändert,
und jede Umdeutung erscheint als Notiz im Format "pfad: alt → neu (Grund)". Das Modul importiert weder
CadQuery noch d2c.cad.ops, damit der Server es vor der Validierung aufrufen kann.
"""

import copy
import json
import math
import re

from d2c.drawing.scale import title_scale
from d2c.drawing.text import parse_dimension_text

# Felder, deren Text eine Zahl ergeben darf. "point" steht für die Elemente einer Punktliste.
NUMBER_KEYS = frozenset(
    "d depth distance size offset value w h r width center_distance angle pitch_d cbore_d cbore_depth csk_d "
    "csk_angle taper tol corner_radius tol_upper tol_lower confidence radius height thickness diameter dia "
    "point printed_dims region crop".split()
)
POINT_SIZE = {"positions": 2, "points": 2, "steps": 2, "near": 3, "mark_near": 3}  # Werte je Punkt
SINGLE_POINT_PARENTS = frozenset({"datums", "geometric_tolerances"})  # dort ist `near` ein einzelner Punkt
NUMBER_LISTS = frozenset({"printed_dims", "region", "crop"})  # Flache Zahlenlisten: Elemente erben den Feldnamen
LIST_KEYS = NUMBER_LISTS | POINT_SIZE.keys() | {"expect"}  # Ein ungültiger JSON-Text wird hier an Trennzeichen geteilt
# Freitext, Bezeichner und Pfade werden nie als JSON gelesen.
TEXT_KEYS = frozenset("text code note assumption assumption_basis reason title id name drawing pdf part".split())
ID_KEYS = frozenset({"refs", "dim_ids"})  # Enthalten Bezeichner, keine Zahlen

OP_FIELDS = {
    "extrude": {"refs", "profile", "plane", "distance", "mode", "both", "taper"},
    "revolve": {"refs", "points", "profile", "steps", "angle", "mode"},
    "holes": {
        "refs",
        "plane",
        "positions",
        "polar",
        "d",
        "depth",
        "kind",
        "cbore_d",
        "cbore_depth",
        "csk_d",
        "csk_angle",
        "thread",
        "thread_core",
        "flip",
    },
    "edge_finish": {"refs", "kind", "size", "near", "on_plane", "tol"},
    "script": {"refs", "code"},
}
OP_OF_TOOL = {"extrude": "extrude", "revolve": "revolve", "add_holes": "holes", "edge_finish": "edge_finish", "run_script": "script"}
TOOL_FIELDS = {tool: OP_FIELDS[op] | {"part"} for tool, op in OP_OF_TOOL.items()}  # Zulässige Argumente je Werkzeug
OP_ALIASES = {
    "holes": {"points": "positions", "diameter": "d", "dia": "d"},
    "extrude": {"height": "distance", "thickness": "distance"},
    "edge_finish": {"radius": "size"},
}
MODE_ALIASES = {"subtract": "cut", "remove": "cut", "union": "add", "join": "add"}
PLANES = ("XY", "XZ", "YZ")
DIM_ID_HINT = 'dimension IDs belong in `refs`, e.g. {"d": "d_bore"}'

ISSUES = {
    "assumed": "missing",
    "assumption": "missing",
    "annahme": "missing",
    "unclear": "ambiguous",
    "unklar": "ambiguous",
    "uncertain": "low_confidence",
    "unsicher": "low_confidence",
    "mismatch": "conflict",
    "konflikt": "conflict",
    "norm": "convention",
}
DIMENSION_KINDS = {
    "length": "linear distance dimension dim size länge laenge maß mass abstand breite höhe hoehe width height depth thickness",
    "diameter": "diam dia durchmesser ø bore",
    "radius": "rad radien r",
    "angle": "winkel",
    "chamfer": "fase",
    "thread": "gewinde",
    "count": "anzahl quantity qty",
}
VIEW_KINDS = {
    "front": "vorderansicht vorne frontansicht front-view",
    "top": "draufsicht oben aufsicht plan top-view",
    "side": "seitenansicht seite left right links rechts side-view",
    "section": "schnitt schnittansicht cut section-view",
    "detail": "einzelheit detailansicht ausschnitt detail-view",
    "iso": "isometric isometrie isometrisch perspective 3d axonometric",
}
AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
GENERAL_TOLERANCE = re.compile(r"(?:iso\s*)?2768\s*[-–]?\s*([fmcv])\w*", re.IGNORECASE)
SOURCE_TEXT = re.compile(r"\s*(\S+?)[\s:@,/]+([A-Fa-f][1-8])\s*")


def _short(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 60 else text[:59] + "…"


def _note(notes: list[str], path: str, old: object, new: object, reason: str) -> None:
    notes.append(f"{path}: {_short(old)} → {_short(new)} ({reason})")


def _join(path: str, key: object) -> str:
    return f"{path}.{key}" if path else str(key)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_number(text: str) -> int | float | None:
    """Zahl aus einem Text ("6,6", "90°", "Ø16H7", "4xØ6.6") oder None; ganze Zahlen werden int."""
    try:
        value = float(text.strip().replace(",", ".").replace("°", ""))
    except ValueError:
        parsed = parse_dimension_text(text.strip())
        value = parsed["nominal"] if parsed else math.nan
    if not math.isfinite(value):
        return None
    return int(value) if value.is_integer() else value


def _number(text: str, path: str, notes: list[str]) -> int | float | str:
    value = _parse_number(text)
    if value is None:
        return text
    _note(notes, path, text, value, "number read from text")
    return value


def _split(text: str, key: str) -> list:
    """Teilt einen Text wie "[25 30 6,6]" an Leerzeichen, ";" und ", "; reine Zahlen werden gleich gewandelt."""
    tokens = [t for t in re.split(r"\s*;\s*|,\s+|\s+", text.strip().strip("[]").strip()) if t]
    if key not in NUMBER_KEYS:
        return tokens
    return [v if isinstance(v := _parse_number(t), (int, float)) and re.fullmatch(r"[-+]?[\d.,]+", t) else t for t in tokens]


def _from_text(text: str, path: str, key: str | None, notes: list[str]) -> object:
    """Regel 1 und 2 für einen Text: JSON lesen, eine Liste aus Text teilen oder eine Zahl lesen."""
    if key not in TEXT_KEYS and text.lstrip().startswith(("[", "{")):
        try:
            value, reason = json.loads(text), "JSON text parsed"
        except ValueError:
            if key not in LIST_KEYS:
                return text
            value, reason = _split(text, key), "text split into list"
        _note(notes, path, text, value, reason)
        return _walk(value, path, key, notes)
    return _number(text, path, notes) if key in NUMBER_KEYS else text


def _points(items: list, path: str, key: str, notes: list[str]) -> list:
    """Regel 3: nackte Zahlen einer Punktliste werden paarweise (bzw. zu dritt) zu Punkten.

    Gilt für eine flache Liste und für Mischformen wie `[0, 0, [5, 0]]`. Die Reihenfolge bleibt erhalten; passt eine
    Zahlenfolge oder ein Eintrag nicht zur Punktgröße, bleibt die Liste unverändert und die Validierung lehnt sie ab.
    """
    size = POINT_SIZE[key]
    points, run = [], []
    for item in items:
        if _is_number(item):
            run.append(item)
            continue
        if len(run) % size or not (isinstance(item, list) and len(item) == size and all(map(_is_number, item))):
            return items
        points += [run[i : i + size] for i in range(0, len(run), size)] + [item]
        run = []
    if not run and not points or len(run) % size:
        return items
    grouped = points + [run[i : i + size] for i in range(0, len(run), size)]
    if len(grouped) == len(items) and all(isinstance(i, list) for i in items):
        return items
    reason = (
        ("single point as list" if len(grouped) == 1 else "flat list split into points")
        if not points
        else "numbers and points mixed: numbers grouped into points"
    )
    _note(notes, path, items, grouped, reason)
    return grouped


def _single_point(items: list, path: str, notes: list[str]) -> list:
    """`near` eines Bezugs oder einer Form- und Lagetoleranz ist ein Punkt: eine Liste mit genau einem Punkt wird entpackt."""
    if len(items) == 1 and isinstance(items[0], list) and len(items[0]) == 3 and all(map(_is_number, items[0])):
        _note(notes, path, items, items[0], "point list with one point as single point")
        return items[0]
    return items


def _walk(node: object, path: str, key: str | None, notes: list[str]) -> object:
    """Regeln 1 bis 3 rekursiv; gibt neue Listen und Objekte zurück, die Eingabe bleibt unverändert."""
    if isinstance(node, str):
        return _from_text(node, path, key, notes)
    if isinstance(node, dict):
        return {k: copy.deepcopy(v) if k in ID_KEYS else _walk(v, _join(path, k), k, notes) for k, v in node.items()}
    if isinstance(node, list):
        child = "point" if key in POINT_SIZE or key == "point" else key if key in NUMBER_LISTS else None
        items = [_walk(v, _join(path, i), child, notes) for i, v in enumerate(node)]
        if key == "near" and SINGLE_POINT_PARENTS.intersection(path.split(".")[-3:-2]):  # "<eltern>.<index>.near"
            return _single_point(items, path, notes)
        return _points(items, path, key, notes) if key in POINT_SIZE else items
    return node


def _rename(d: dict, old: str, new: str, path: str, notes: list[str]) -> None:
    if old in d and new not in d:
        d[new] = d.pop(old)
        _note(notes, _join(path, old), old, new, "Alias")


def _plane(d: dict, path: str, notes: list[str]) -> None:
    """Eine Ebene als Text wird zum Objekt, Kleinbuchstaben werden groß; `custom` bleibt klein."""
    plane = d.get("plane")
    if isinstance(plane, str):
        d["plane"] = {"name": plane.strip().upper() if plane.strip().upper() in PLANES else plane}
        _note(notes, _join(path, "plane"), plane, d["plane"], "plane as object")
    elif isinstance(plane, dict) and isinstance(plane.get("name"), str) and plane["name"].upper() in PLANES:
        if plane["name"] != plane["name"].upper():
            _note(notes, _join(path, "plane.name"), plane["name"], plane["name"].upper(), "uppercase")
            plane["name"] = plane["name"].upper()


def _profile(d: dict, path: str, notes: list[str]) -> None:
    """Rechteck- und Kreisprofil mit gebräuchlichen Feldnamen."""
    profile = d.get("profile")
    if not isinstance(profile, dict):
        return
    where = _join(path, "profile")
    if profile.get("type") == "rectangle":
        profile["type"] = "rect"
        _note(notes, _join(where, "type"), "rectangle", "rect", "Alias")
    if profile.get("type") == "rect":
        _rename(profile, "width", "w", where, notes)
        _rename(profile, "height", "h", where, notes)
    if profile.get("type") == "circle":
        _rename(profile, "diameter", "d", where, notes)
        _rename(profile, "dia", "d", where, notes)
        if _is_number(profile.get("radius")) and "d" not in profile:
            profile["d"] = 2 * profile.pop("radius")
            _note(notes, _join(where, "radius"), "radius", "d = 2r", "Alias")


def _holes(d: dict, path: str, notes: list[str]) -> None:
    """`through: true` und `depth: 0` heißen durchbohren, also fehlt `depth`."""
    if d.get("through") is True:
        del d["through"]
        _note(notes, _join(path, "through"), True, "removed", "without `depth` the hole is drilled through")
    if _is_number(d.get("depth")) and d["depth"] == 0:
        _note(notes, _join(path, "depth"), d.pop("depth"), "removed", "without `depth` the hole is drilled through")


def _leaves(node: object, path: str = "") -> list[tuple[str, object]]:
    """(Pfad, Zahl) für jede Zahl unter `node`, Pfade punktgetrennt wie in `refs`."""
    if isinstance(node, dict):
        return [leaf for k, v in node.items() if k != "refs" for leaf in _leaves(v, _join(path, k))]
    if isinstance(node, list):
        return [leaf for i, v in enumerate(node) for leaf in _leaves(v, _join(path, i))]
    return [(path, node)] if _is_number(node) else []


def _refs(d: dict, fields: set[str], path: str, notes: list[str]) -> None:
    """`refs` ordnet Pfaden Maß-IDs zu. Vertauschte Einträge wie {"d6": "76.2"} werden zu {"distance": "d6"}, wenn die
    Zahl genau unter einem Pfad steht; sonst wird der Eintrag entfernt, statt den ganzen Vorgang abzulehnen."""
    refs = d.get("refs")
    if not isinstance(refs, dict):
        return
    leaves = _leaves(d)
    paths = {p for p, _ in leaves}
    for key, value in list(refs.items()):
        if key.split(".")[0] in fields:  # gültige Form; Tippfehler im Pfad meldet die Validierung
            continue
        where = _join(path, f"refs.{key}")
        number = _parse_number(value) if isinstance(value, str) else value if _is_number(value) else None
        hits = [p for p, v in leaves if number is not None and abs(v - number) < 1e-9]
        del refs[key]
        if isinstance(value, str) and value in paths and value not in refs:
            refs[value] = key
            _note(notes, where, value, {value: key}, "path and dimension ID swapped")
        elif len(hits) == 1 and hits[0] not in refs:
            refs[hits[0]] = key
            _note(notes, where, value, {hits[0]: key}, "dimension ID with value instead of path: path found via the value")
        else:
            _note(
                notes,
                where,
                value,
                "removed",
                'binding lost, not a valid path; refs maps paths to dimension IDs, e.g. {"distance": "d6"}',
            )


def _drop_unknown(d: dict, allowed: set[str], path: str, notes: list[str]) -> None:
    """Regel 8: Unbekannte Argumente würden still verworfen; sie werden gemeldet."""
    for key in [k for k in d if k not in allowed]:
        hint = DIM_ID_HINT if key in ("dim_ids", "dim_id") else "unknown argument"
        _note(notes, _join(path, key), d.pop(key), "removed", f"ignored: {hint}")


def _op(name: str, d: dict, path: str, notes: list[str], extra: frozenset[str] = frozenset()) -> None:
    """Regeln 4 und 8 für die Felder eines Vorgangs; `extra` sind zulässige Felder außerhalb des Vorgangs."""
    for old, new in OP_ALIASES.get(name, {}).items():
        _rename(d, old, new, path, notes)
    _plane(d, path, notes)
    _profile(d, path, notes)
    if isinstance(d.get("mode"), str) and d["mode"].lower() in MODE_ALIASES:
        _note(notes, _join(path, "mode"), d["mode"], MODE_ALIASES[d["mode"].lower()], "Alias")
        d["mode"] = MODE_ALIASES[d["mode"].lower()]
    if name == "holes":
        _holes(d, path, notes)
    _refs(d, OP_FIELDS[name] - {"refs"}, path, notes)
    _drop_unknown(d, OP_FIELDS[name] | extra, path, notes)


def _top_face(args: dict, part_bbox: list[float] | None, notes: list[str]) -> None:
    """Regel 5: Bohrungen ohne Ebene beginnen an der Oberseite des bisherigen Teils."""
    if "plane" not in args and part_bbox is not None and len(part_bbox) == 6:
        args["plane"] = {"name": "XY", "offset": part_bbox[5]}
        _note(notes, "plane", "missing", args["plane"], "entry face assumed at the top of the part")


def _scale_argument(args: dict, notes: list[str]) -> None:
    """Regel 6: Der Maßstab von measure_drawing als Text ("2:1", "0,5") wird zur Zahl."""
    scale = args.get("scale")
    if not isinstance(scale, str):
        return
    value = title_scale(scale) if ":" in scale else _parse_number(scale)
    if value is not None:
        args["scale"] = float(value)
        _note(notes, "scale", scale, args["scale"], "scale as number (drawing:model)")


def _alias_value(d: dict, key: str, table: dict[str, str], path: str, notes: list[str]) -> None:
    """Ersetzt einen Textwert nach einer Wortliste; Großbuchstaben und der Zielwert selbst gelten als gültig."""
    value = d.get(key)
    if not isinstance(value, str):
        return
    word = value.strip().lower()
    new = table.get(word, word if word in table.values() else None)
    if new is not None and new != value:
        d[key] = new
        _note(notes, _join(path, key), value, new, "Alias")


def _word_table(groups: dict[str, str]) -> dict[str, str]:
    return {word: target for target, words in groups.items() for word in words.split()}


def _scale_text(d: dict, path: str, notes: list[str]) -> None:
    """Ein Maßstab als Zahl: 2 wird "2:1", 0,5 wird "1:2"."""
    scale = d.get("scale")
    if _is_number(scale) and scale > 0:
        text = f"{scale:g}:1" if scale >= 1 else f"1:{1 / scale:g}"
        d["scale"] = text
        _note(notes, _join(path, "scale"), scale, text, "scale as text")


def _source(d: dict, path: str, notes: list[str]) -> None:
    """Herkunft als Text ("v1", "v1 B3") oder mit kleinem oder leerem `cell`."""
    source = d.get("source")
    where = _join(path, "source")
    if isinstance(source, str):
        found = SOURCE_TEXT.fullmatch(source)
        d["source"] = {"view": found[1], "cell": found[2].upper()} if found else {"view": source.strip(), "cell": None}
        _note(notes, where, source, d["source"], "source as object")
    elif isinstance(source, dict):
        cell = source.get("cell", "")
        fixed = cell.strip().upper() or None if isinstance(cell, str) else cell
        if "cell" not in source or fixed != cell:
            _note(notes, _join(where, "cell"), cell if "cell" in source else "missing", fixed, "grid cell")
            source["cell"] = fixed


def _general_tolerance(spec: dict, notes: list[str]) -> None:
    value = spec.get("general_tolerance")
    found = GENERAL_TOLERANCE.fullmatch(value.strip()) if isinstance(value, str) else None
    if found and value != f"ISO 2768-{found[1].lower()}":
        spec["general_tolerance"] = f"ISO 2768-{found[1].lower()}"
        _note(notes, "general_tolerance", value, spec["general_tolerance"], "spelling")


def _axis(hole: dict, path: str, notes: list[str]) -> None:
    axis = hole.get("axis")
    found = re.fullmatch(r"\s*([+-]?)\s*([xyz])\s*", axis, re.IGNORECASE) if isinstance(axis, str) else None
    if found:
        sign = -1 if found[1] == "-" else 1
        hole["axis"] = [sign * v for v in AXES[found[2].lower()]]
        _note(notes, _join(path, "axis"), axis, hole["axis"], "axis as vector")


def _review_ids(spec: dict, notes: list[str]) -> None:
    """Ein fehlender Prüfeintrags-ID erhält `r{n}`."""
    groups = [g for key in ("views", "dimensions", "features", "notes", "review") if isinstance(g := spec.get(key), list)]
    used = {item["id"] for group in groups for item in group if isinstance(item, dict) and "id" in item}
    for index, item in enumerate(spec.get("review") or []):
        if isinstance(item, dict) and "id" not in item:
            number = index + 1
            while f"r{number}" in used:
                number += 1
            item["id"] = f"r{number}"
            used.add(item["id"])
            _note(notes, f"review.{index}.id", "fehlt", item["id"], "sequential ID")


def _items(spec: dict, key: str) -> list[tuple[str, dict]]:
    """Die Objekte einer Liste der Spezifikation mit ihrem Pfad."""
    value = spec.get(key)
    return [(f"{key}.{i}", v) for i, v in enumerate(value) if isinstance(v, dict)] if isinstance(value, list) else []


def _spec(spec: object, path: str, notes: list[str]) -> None:
    """Regel 7 für eine Spezifikation. `confidence`, `text` und `expected` werden nie ergänzt."""
    if not isinstance(spec, dict):
        return
    _scale_text(spec, path, notes)
    _general_tolerance(spec, notes)
    for where, view in _items(spec, "views"):
        _alias_value(view, "kind", _word_table(VIEW_KINDS), _join(path, where), notes)
        _scale_text(view, _join(path, where), notes)
    for where, dimension in _items(spec, "dimensions"):
        _alias_value(dimension, "kind", _word_table(DIMENSION_KINDS), _join(path, where), notes)
        _source(dimension, _join(path, where), notes)
    for where, note in _items(spec, "notes"):
        _source(note, _join(path, where), notes)
    for where, item in _items(spec, "review"):
        _alias_value(item, "issue", ISSUES, _join(path, where), notes)
    _review_ids(spec, notes)
    for where, hole in _items(spec.get("expected") or {}, "holes"):
        _axis(hole, _join(path, "expected." + where), notes)


def normalize(tool: str, args: dict, part_bbox: list[float] | None = None) -> tuple[dict, list[str]]:
    """Deutet Formfehler in den Argumenten von `tool` um und gibt (neue Argumente, Notizen) zurück.

    `args` bleibt unverändert. `part_bbox` ist [xmin, ymin, zmin, xmax, ymax, zmax] des bisherigen Teils (oder None).
    Eine Notiz hat das Format "pfad: alt → neu (Grund)".
    """
    notes: list[str] = []
    out = _walk(args, "", None, notes)
    if tool in OP_OF_TOOL:
        _op(OP_OF_TOOL[tool], out, "", notes, TOOL_FIELDS[tool] - OP_FIELDS[OP_OF_TOOL[tool]])
        if tool == "add_holes":
            _top_face(out, part_bbox, notes)
    elif tool == "edit_op" and isinstance(out.get("op"), dict) and out["op"].get("op") in OP_FIELDS:
        _op(out["op"]["op"], out["op"], "op", notes, frozenset({"op"}))
    elif tool == "measure_drawing":
        _scale_argument(out, notes)
    elif tool in ("submit_spec", "check_spec"):
        for key in ("spec", "second"):
            _spec(out.get(key), key, notes)
    return out, notes

"""Verständliche Fehlertexte für das Modell: Worker-Bericht ohne Modell und Meldungen aus pydantic-ValidationError."""

from pydantic import ValidationError

NO_EFFECT = "No material removed"  # Anfang der Aufbauwarnung eines Schnitts ohne Wirkung (ops) und der Anleitung dazu (guide)
MAX_ERROR_ROWS = 12  # Mehr Zeilen einer Fehlermeldung überfordern ein kleines Modell, die ersten zeigen die Ursache.
RECEIVED_LIMIT = 60  # Zeichen des erhaltenen Werts in einer Fehlerzeile.
# Reparaturhinweise nach (letztes Feld des Pfads, Fehlertyp), danach nach dem Fehlertyp allein.
POINT_HINT = '"{}": [[x, y, z]]'
SINGLE_POINT_HINT = '"near": [x, y, z], one point in the middle of the face of the datum or tolerance'
PLANE_HINT = 'plane={"name": "XY", "offset": <height of the entry face>}'
FIELD_HINTS = {
    ("mark_near", "tuple_type"): POINT_HINT.format("mark_near"),
    ("near", "tuple_type"): POINT_HINT.format("near"),
    ("near", "missing"): '"near": [[x, y, z]], one point on each edge to be filleted or chamfered. '
    "Rounded corners of a profile belong in the path profile as `fillets`",
    ("positions", "tuple_type"): '"positions": [[x, y]]',
    ("points", "tuple_type"): '"points": [[x, y]]',
    ("plane", "missing"): PLANE_HINT,
    ("plane", "model_type"): PLANE_HINT,
    ("depth", "greater_than"): "omit `depth` for a through hole",
    ("distance", "value_error"): "negative = against the normal",
}
TYPE_HINTS = {"float_parsing": 'the number only, e.g. 6.6 instead of "Ø6.6"'}


def error_report(error: str) -> dict:
    """Worker-Bericht ohne erzeugtes Modell: `error` ist gesetzt, alle übrigen Felder sind leer."""
    return {"ok": False, "error": error, "solid": None, "dims": [], "build_notes": [], "roundtrip": None, "dims_found": None}


def validation_error_message(error: ValidationError) -> str:
    """Gibt Pydantic-Feldfehler auf Englisch aus und erhält dabei Feldpfade sowie Fehlertypen."""
    messages = {
        "missing": "is missing",
        "extra_forbidden": "is not allowed",
        "greater_than": "must be greater than {gt}",
        "greater_than_equal": "must be at least {ge}",
        "less_than": "must be less than {lt}",
        "less_than_equal": "must be at most {le}",
        "string_pattern_mismatch": "must match the pattern {pattern!r}",
        "string_too_short": "text length must be at least {min_length} characters",
        "string_too_long": "text length must be at most {max_length} characters",
        "too_short": "number of entries must be at least {min_length}",
        "too_long": "number of entries must be at most {max_length}",
        "union_tag_invalid": "has the unknown type {tag!r} for {discriminator}; allowed: {expected_tags}",
        "union_tag_not_found": "requires the type field {discriminator}",
        "literal_error": "must be one of the allowed values: {expected}",
        "int_parsing": "must be an integer",
        "int_type": "must be an integer",
        "int_from_float": "must be an integer without decimal places",
        "float_parsing": "must be a number",
        "float_type": "must be a number",
        "string_type": "must be text",
        "list_type": "must be a list",
        "tuple_type": "must be a tuple",
        "dict_type": "must be an object",
        "bool_type": "must be a boolean (true or false)",
        "bool_parsing": "must be a boolean (true or false)",
        "finite_number": "must be finite",
    }
    rows, hints = [], []
    errors = error.errors(include_url=False)
    for item in errors[:MAX_ERROR_ROWS]:
        kind, context = item["type"], item.get("ctx") or {}
        if kind == "literal_error":
            context["expected"] = context["expected"].replace(" or ", ", ")
        message = (
            str(context["error"]) if kind == "value_error" and "error" in context else messages.get(kind, "is invalid").format(**context)
        )
        location = ".".join(map(str, item["loc"])) or "input"
        rows.append(f"{location}: {message} ({kind}) (received: {_received(item['input'])})")
        hint = _hint(item["loc"], kind)
        if hint and hint not in hints:
            hints.append(hint)
    if len(errors) > MAX_ERROR_ROWS:
        rows.append(f"… and {len(errors) - MAX_ERROR_ROWS} more errors")
    return "; ".join(rows) + "".join(f". Example: {hint}" for hint in hints)


def _received(value: object) -> str:
    text = repr(value)
    return text if len(text) <= RECEIVED_LIMIT else text[: RECEIVED_LIMIT - 1] + "…"


def _hint(loc: tuple, kind: str) -> str | None:
    """Reparaturhinweis zu einem Fehler; `near` eines Bezugs oder einer Form- und Lagetoleranz ist ein Einzelpunkt."""
    field = _last_field(loc)
    if field == "near" and {"datums", "geometric_tolerances"} & set(loc):
        return SINGLE_POINT_HINT
    return FIELD_HINTS.get((field, kind)) or TYPE_HINTS.get(kind)


def _last_field(loc: tuple) -> str:
    """Der letzte Feldname eines Fehlerpfads, ohne Listenindizes ("review.0.mark_near.2" ergibt "mark_near")."""
    return next((part for part in reversed(loc) if isinstance(part, str)), "")

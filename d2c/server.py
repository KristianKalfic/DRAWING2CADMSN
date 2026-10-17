"""The MCP server: render and read drawings, submit a spec, build the part op by op, export the STEP."""

import argparse
import functools
import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, get_args

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from d2c import crosscheck, drawing, vector
from d2c.ops import EdgeFinishOp, ExtrudeOp, HolesOp, Op, OpBase, RevolveOp, ScriptOp
from d2c.sandbox import validation_error_message
from d2c.session import Session
from d2c.spec import PartSpec
from d2c.text import read_text

MAX_TEXT_ITEMS = 300
MAX_SHAPES = 12  # circles and arcs measure_drawing lists
SCALE_TOL = 0.005  # relative difference at which two scales count as different
MIN_PRINTED_DIMS = 6  # fewer printed values cannot tell the real scale from chance hits

server = MCPServer("drawing2cad", instructions=Path(__file__).with_name("instructions.md").read_text("utf-8"))
session = Session()


def _guard(fn: Callable) -> Callable:
    """Re-raise the errors of a tool as ToolError: only its message reaches the client."""

    @functools.wraps(fn)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ValidationError as e:
            raise ToolError(validation_error_message(e)) from e
        except (KeyError, ValueError, IndexError) as e:
            raise ToolError(e.args[0] if e.args else str(e)) from e

    return guarded


def _tool(fn: Callable) -> Callable:
    return server.tool()(_guard(fn))


def _op_tool(model: type[OpBase]) -> Callable[[Callable], Callable]:
    """Register a build tool whose parameters are the fields of an op model, without `op`.

    The decorated stub only supplies the name and the docstring. Types, defaults and descriptions come
    from the model, so the tool and the op schema cannot drift apart. The tool builds the op model
    (which validates it) and hands model_dump() to the session.
    """
    op = get_args(model.model_fields["op"].annotation)[0]
    params = [inspect.Parameter("part", inspect.Parameter.KEYWORD_ONLY, annotation=str)]
    for name, field in model.model_fields.items():
        if name != "op":
            note = Field(description=field.description, discriminator=field.discriminator)
            params.append(inspect.Parameter(
                name, inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if field.is_required() else field.default,
                annotation=Annotated[field.annotation, *field.metadata, note]))

    def register(stub: Callable) -> Callable:
        def build(**fields: Any) -> dict:
            part = fields.pop("part")
            return session.apply(part, model(op=op, **fields).model_dump())

        build.__name__, build.__doc__ = stub.__name__, stub.__doc__
        build.__signature__ = inspect.Signature(params, return_annotation=dict)
        return _tool(build)

    return register


@_tool
def render_drawing(pdf: str, page: int = 1, dpi: int = 100, crop: list[float] | None = None) -> list:
    """Rendere eine Seite einer Zeichnungs-PDF oder einen Ausschnitt davon als PNG-Bild.

    Der PDF-Pfad ist relativ zum Datenordner. Der Textteil des Ergebnisses ist JSON mit der Seitenzahl
    ("pages"), der Seitengröße in pt und der tatsächlich verwendeten "dpi" (höchstens 600; die längste
    Bildkante ist maximal 2000 px). `crop` hat das Format [x0, y0, x1, y1] mit Anteilen der angezeigten
    Seite und Ursprung oben links; [0, 0, 0.5, 0.5] bezeichnet zum Beispiel das linke obere Viertel.
    Die Seitenzählung beginnt bei 1.

    Beispiel: render_drawing(pdf="Part A/drawing.pdf", page=1, dpi=300, crop=[0.1, 0.1, 0.6, 0.5])
    """
    info = drawing.pdf_info(pdf, page)
    png = drawing.render(pdf, page, dpi, crop)
    return [Image(data=png, format="png"), json.dumps(info | {"dpi": drawing.effective_dpi(info, dpi, crop)})]


@_tool
def read_drawing_text(pdf: str, page: int = 1, region: list[float] | None = None) -> str:
    """Lies die Textebene einer Zeichnungsseite aus: gedruckte Maße, Passungen, Toleranzen und Hinweise.

    Rufe dieses Werkzeug zuerst für jede Seite auf. Es liefert JSON-Elemente im Format
    {text, x0, y0, x1, y1, font, size, vertical, parsed}; x0..y1 sind Anteile der angezeigten Seite,
    Ursprung oben links, wie bei `crop` von render_drawing. `parsed` zerlegt ein Maß in prefix, count,
    nominal (mm), tol_upper, tol_lower (mm), fit, envelope und thread; bei anderem Text ist der Wert null.
    Stattdessen wird eine Meldung zurückgegeben, wenn die Seite keine Textebene hat (Text als Umrisse
    gezeichnet: lies das Bild) oder mehr als 300 Elemente enthält (übergib ein kleineres `region`).

    Beispiel: read_drawing_text(pdf="Part A/drawing.pdf", page=1, region=[0.5, 0.8, 1, 1])
    """
    items = read_text(drawing.resolve_drawing(pdf), page, region)
    if not items:
        if region:
            return "Kein Text in diesem Bereich: Vergrößere ihn oder lies die Maße aus dem gerenderten Bild."
        return "Keine Textebene vorhanden (Text als Vektor-Umrisse): Lies die Maße aus dem gerenderten Bild."
    if len(items) > MAX_TEXT_ITEMS:
        return f"{len(items)} Textelemente; mehr als {MAX_TEXT_ITEMS}: Übergib einen kleineren Bereich, um sie auszulesen."
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _scale_notes(scale: float | None, suggestion: list[dict], printed: int) -> list[str]:
    """Hints on the scale: missing, disagreeing with the geometry, or voted on by too few values."""
    notes = []
    if scale is None:
        notes.append("Maßstab nicht angegeben: Messung mit 1:1. Übergib den Maßstab aus dem Schriftfeld für reale Modellmaße.")
    if suggestion:
        best = suggestion[0]
        if scale is not None and abs(best["scale"] - scale) > SCALE_TOL * scale:
            notes.append(f"Maßstab im Schriftfeld weicht von der Geometrie ab: angegeben {scale:g}, Geometrie ergibt "
                         f"{best['scale']:g} ({best['votes']} von {printed} abgedruckten Werten)")
        if printed < MIN_PRINTED_DIMS:
            notes.append(f"Nur {printed} `printed_dims`: `scale_suggestion` ist unzuverlässig; gib mindestens "
                         f"{MIN_PRINTED_DIMS} Werte an.")
    elif printed:
        notes.append("Kein Maßstab passt zu `printed_dims`.")
    return notes


@_tool
def measure_drawing(
    pdf: str,
    page: int = 1,
    region: list[float] | None = None,
    scale: float | None = None,
    printed_dims: list[float] = [],
    expect: list[str] = [],
    edge_width: str | float = "auto",
) -> dict:
    """Miss Kreise, Bögen und Außenmaße aus der Vektorgeometrie einer Zeichnungsseite in Modell-mm.

    Verwende es für nicht abgedruckte Maße, zur Gegenprüfung gelesener Maße und zum Erkennen eines falschen
    Maßstabs im Schriftfeld. `region` ist [x0, y0, x1, y1] als Anteile der angezeigten Seite mit Ursprung
    oben links (eine Ansicht). `scale` ist Zeichnung:Modell, also 2 für 2:1 und 0.5 für 1:2. `printed_dims`
    enthält abgedruckte Längen und Durchmesser aus den Ansichten (mindestens sechs); dann enthält das
    Ergebnis `scale_suggestion`, nach bester Übereinstimmung sortiert. `expect` enthält Angaben wie
    "d=19", "r=10" oder "L=49", die mit der Geometrie abgeglichen werden: Status ok, MISMATCH oder
    no_candidates. `edge_width` ist "auto", "all" oder eine Linienbreite in pt. Koordinaten sind mm ab
    der linken unteren Ecke des Bereichs, y zeigt nach oben. Reine Rasterseiten enthalten keine Geometrie.

    Beispiel: Miss eine fehlende Breite in einer Ansicht im Maßstab 2:1 mit measure_drawing(pdf="Part A/drawing.pdf",
    region=[0.1, 0.08, 0.97, 0.45], scale=2) und lies `extent_mm` ab.
    """
    path = drawing.resolve_drawing(pdf)
    if isinstance(edge_width, str) and edge_width not in ("auto", "all"):
        edge_width = float(edge_width)
    res = vector.extract(path, page, region, scale or 1.0, edge_width)
    suggestion = vector.suggest_scale(path, printed_dims, page, region) if printed_dims else []
    out = {"scale_used": res["scale_used"]}
    if printed_dims:
        out["scale_suggestion"] = suggestion
    out |= {"extent_mm": res["extent_mm"], "circles": res["circles"][:MAX_SHAPES], "arcs": res["arcs"][:MAX_SHAPES]}
    if expect:
        out["verify"] = vector.verify(res, expect)
    return out | {"notes": _scale_notes(scale, suggestion, len(printed_dims))}


@_tool
def check_spec(spec: PartSpec, second: PartSpec | None = None) -> dict:
    """Prüfe eine Spezifikation vor `submit_spec` auf Werte, die nicht stimmen können. Es wird nichts übernommen.

    Liefert {"findings": [...], "count": n}; count 0 bedeutet, dass nichts gefunden wurde. Ein Befund hat `code`, `message`,
    `where` und `suggested_review`, den Entwurf eines Prüfeintrags (issue, refs, assumption, reason). Regeln: ein Loch, das
    in keiner Lage in den erwarteten Begrenzungsrahmen passt (HOLE_OUTSIDE_BBOX), überlappende Löcher (HOLES_OVERLAP), ein
    Rundungsradius über der halben mittleren Kante des Begrenzungsrahmens (FILLET_TOO_BIG). Mit `second`, einer unabhängigen
    zweiten Auswertung derselben Seite, ist auch jeder Unterschied zu `spec` ein Befund (A ist `spec`, B ist `second`):
    EXTRACTION_MISMATCH, ONLY_IN_A, ONLY_IN_B, HOLE_COUNT.

    Beispiel: check_spec(spec=<die Spezifikation wie für submit_spec>, second=<eine zweite Auswertung der Seite, optional>)
    """
    findings = crosscheck.plausibility(spec)
    if second is not None:
        findings += crosscheck.compare_specs(spec, second)
    return {"findings": findings, "count": len(findings)}


@_tool
def submit_spec(spec: PartSpec) -> dict:
    """Übernimm die Auswertung einer Zeichnungsseite (Maße, Ansichten, Merkmale, Hinweise, expected, review).

    Damit beginnt ein Bauteil; vorhandene ops eines gleichnamigen Bauteils werden zurückgesetzt. Längen
    sind in mm und Winkel in Grad; `expected.bbox` und Lochpositionen sind in Modellkoordinaten (Z nach
    oben). Jedes angenommene Maß sowie jedes Maß mit `confidence` unter 0.8 muss in `refs` eines
    Prüfeintrags aufgeführt sein. Verfasse alle selbst formulierten Freitexte in der Spezifikation auf
    Deutsch; übernimm `text` und direkte Zitate aus der Zeichnung exakt wie abgedruckt.

    Beispiel: submit_spec(spec={"name": "plate", "drawing": "A/p.pdf", "title": "Platte", "scale": "1:1",
    "views": [{"id": "v1", "kind": "top", "scale": "1:1"}], "dimensions": [{"id": "d1", "kind": "length",
    "value": 80, "text": "80", "source": null, "confidence": 0.95}], "features": [{"id": "f1", "type":
    "base", "dim_ids": ["d1"]}], "expected": {"bbox": [80, 50, 10]}, "review": []})
    """
    return session.submit_spec(spec)


@_op_tool(ExtrudeOp)
def extrude() -> dict:
    """Extrudiere ein auf einer Skizzenebene gezeichnetes Profil und füge es hinzu, schneide es aus oder bilde den Schnitt mit dem Bauteil.

    Längen sind in mm, Winkel in Grad. Ebenen: XY hat die Normale +Z (u=X, v=Y), XZ die Normale -Y
    (u=X, v=Z), YZ die Normale +X (u=Y, v=Z); `plane.offset` verschiebt entlang der Normalen. Bei
    positivem `distance` verläuft die Extrusion in Richtung der Ebenennormalen. Mit `both=True` ist
    `distance` die Gesamtdicke, zentriert um die Ebene. `refs` ordnet Parameterpfade Maß-IDs zu, zum
    Beispiel {"profile.w": "d1", "distance": "t1"}.

    Beispiel: extrude(part="plate", profile={"type": "rect", "center": [0, 0], "w": 80, "h": 50},
    plane={"name": "XY"}, distance=10, refs={"profile.w": "d1"})
    """


@_op_tool(RevolveOp)
def revolve() -> dict:
    """Rotiere ein Profil um die globale Z-Achse und füge es hinzu, schneide es aus oder bilde den Schnitt mit dem Bauteil.

    Das Profil liegt in der XZ-Ebene mit u = r (Abstand zur Achse in mm) und v = z (in mm), daher muss
    r >= 0 sein. Übergib `points` (ein automatisch geschlossenes Polygon aus [r, z]) oder `profile`
    (einen Pfad mit Bögen und Rundungen). `angle` ist der Winkel in Grad, gegen den Uhrzeigersinn von +Z
    aus gesehen. `refs` ordnet Parameterpfade Maß-IDs zu, z. B. {"points.1.0": "d2"}.

    Beispiel: revolve(part="pin", points=[[0, 0], [5, 0], [5, 30], [0, 30]])
    """


@_op_tool(HolesOp)
def add_holes() -> dict:
    """Bohre von einer Skizzenebene aus einfache, zylindrisch gesenkte, kegelig gesenkte oder mit Gewinde versehene Löcher in das Bauteil.

    Längen sind in mm, Winkel in Grad. Ebenen: XY hat die Normale +Z (u=X, v=Y), XZ die Normale -Y
    (u=X, v=Z), YZ die Normale +X (u=Y, v=Z). Löcher werden entgegen der Ebenennormalen gebohrt, außer
    bei `flip=True`; lege die Ebene also auf die Eintrittsfläche (bei XY auf die Oberseite und mit
    `offset` auf deren Höhe). Positionen sind [u, v] in der Ebene; alternativ kannst du `polar` verwenden.
    Mit `depth` gleich null wird durchgebohrt. `refs` ordnet Parameterpfade Maß-IDs zu, z. B. {"d": "d3",
    "positions.0.0": "d4"}.

    Beispiel: add_holes(part="plate", plane={"name": "XY", "offset": 10}, d=6.6,
    positions=[[30, 15], [-30, 15]], refs={"d": "d3"})
    """


@_op_tool(EdgeFinishOp)
def edge_finish() -> dict:
    """Verrunde oder fase Kanten in der Nähe angegebener Punkte.

    Längen sind in mm. `near` enthält globale [x, y, z]-Punkte auf oder nahe den Kanten (zum Beispiel
    deren Mittelpunkte); `tol` ist der größte zulässige Abstand zwischen einer Kante und einem Punkt.
    Nahtkanten runder Flächen werden übersprungen. Rufe das Werkzeug direkt nach dem Körper auf, dessen
    Kanten es ändert. `refs` ordnet Parameterpfade Maß-IDs zu, zum Beispiel {"size": "d5"}.

    Beispiel: edge_finish(part="plate", kind="fillet", size=2, near=[[40, 0, 5]])
    """


@_tool
def run_script(part: str, code: str) -> dict:
    """Führe CadQuery-Code als nächsten Vorgang aus. Verwende dies nur, wenn kein anderes Werkzeug die Geometrie abdeckt.

    Längen sind in mm. Im Code stehen `result` (das bisher aufgebaute cq.Workplane, beim ersten Vorgang
    ein leeres XY-Workplane), `cq`, `math` und einige eingebaute Funktionen zur Verfügung (abs, min, max,
    range, len, float, int, round, sum, enumerate, zip, list, tuple, dict, print). Imports und Namen mit
    führendem `_` sind verboten (auch `_` als Schleifenvariable). `result` muss am Ende ein cq.Workplane
    mit dem Volumenkörper enthalten. Ebenen folgen CadQuery: XY hat die Normale +Z, XZ -Y, YZ +X.

    Beispiel: run_script(part="plate", code="result = result.faces('>Z').workplane().hole(5)")
    """
    return session.apply(part, ScriptOp(op="script", code=code).model_dump())


@_tool
def edit_op(part: str, index: int, op: Op) -> dict:
    """Ersetze den Vorgang an `index` (ab 0) durch einen vollständigen neuen Vorgang und baue das Bauteil erneut auf.

    `op` hat dieselben Felder wie die Werkzeuge zum Aufbau, zusätzlich "op" (extrude, revolve, holes,
    edge_finish oder script). Längen sind in mm; für Ebene und Richtung gelten dieselben Konventionen.

    Beispiel: edit_op(part="plate", index=0, op={"op": "extrude", "profile": {"type": "circle", "center":
    [0, 0], "d": 30}, "plane": {"name": "XY"}, "distance": 12})
    """
    return session.edit_op(part, index, op.model_dump())


@_tool
def delete_op(part: str, index: int) -> dict:
    """Lösche den Vorgang an `index` (ab 0) und baue das Bauteil erneut auf. Das Löschen des einzigen Vorgangs wird abgelehnt.

    Beispiel: delete_op(part="plate", index=2)
    """
    return session.delete_op(part, index)


@_tool
def move_op(part: str, index: int, new_index: int) -> dict:
    """Verschiebe den Vorgang an `index` auf Position `new_index` (beide ab 0) und baue das Bauteil erneut auf.

    Verwende dies, wenn `edge_finish` fehlschlägt, weil es zu früh oder zu spät kommt. Der erste Vorgang
    muss `add` verwenden.

    Beispiel: move_op(part="plate", index=3, new_index=1)
    """
    return session.move_op(part, index, new_index)


@_tool
def get_state(part: str) -> dict:
    """Zeige die Zusammenfassung der Spezifikation, die Vorgangsliste und den letzten Aufbau-Bericht eines Bauteils.

    Beispiel: get_state(part="plate")
    """
    return session.state(part)


@_tool
def export_step(part: str) -> dict:
    """Baue das Bauteil und schreibe bei bestandenen Prüfungen die STEP-Datei, den Bericht und die Prüfliste (review.md).

    Längen in der STEP-Datei sind mm. Das Ergebnis enthält {"ok", "files", "report"}; bei ok=false wird
    nichts geschrieben und der Bericht nennt die fehlgeschlagenen Prüfungen.

    Beispiel: export_step(part="plate")
    """
    return session.export(part)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m d2c.server", description=__doc__)
    parser.add_argument("--http", metavar="HOST:PORT", help="serve streamable HTTP at /mcp instead of stdio")
    args = parser.parse_args()
    if args.http is None:
        server.run("stdio")
        return
    host, _, port = args.http.rpartition(":")
    if not host or not port.isdigit():
        parser.error("--http expects HOST:PORT, e.g. 0.0.0.0:8765")
    # On 127.0.0.1 or localhost mcp accepts only those two Host headers; bind 0.0.0.0 in a container.
    server.run("streamable-http", host=host, port=int(port))


if __name__ == "__main__":
    main()

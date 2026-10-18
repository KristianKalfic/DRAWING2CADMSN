"""MCP-Server: rendert und liest Zeichnungen, übernimmt Spezifikationen, baut Bauteile Schritt für Schritt und exportiert STEP."""

import functools
import inspect
import json
import logging
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, get_args

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent
from pydantic import Field, ValidationError

from d2c.cad.ops import EdgeFinishOp, ExtrudeOp, HolesOp, Op, OpBase, RevolveOp, ScriptOp
from d2c.config import report_full, results_root
from d2c.drawing import pages, vector
from d2c.drawing.scale import scale_check, scale_notes, title_block_ratio
from d2c.drawing.text import read_text
from d2c.errors import validation_error_message
from d2c.quality import crosscheck
from d2c.quality.gates import Finding, followed_model
from d2c.quality.tolerances import Variant
from d2c.server import coerce, guide
from d2c.server.readgate import SHARP_DPI, read_findings
from d2c.session import CHANGES_FILE, Session
from d2c.spec import PartSpec

log = logging.getLogger(__name__)

MAX_TEXT_ITEMS = 300
MAX_SHAPES = 12  # Anzahl der von measure_drawing ausgegebenen Kreise und Bögen.
MAX_LINES = 40  # Anzahl der von measure_drawing ausgegebenen Geraden (die längsten).

session = Session()
# Stand des Messgates je Serverlauf: Anzahl der measure_drawing-Aufrufe und ihr Stand beim letzten submit_spec je Bauteil.
gate = {"measured": 0, "submitted": {}}


def _part_bbox(arguments: dict) -> list[float] | None:
    """Der Begrenzungsrahmen des letzten Aufbaus von `arguments["part"]`; None, wenn es keinen gibt."""
    try:
        part = unicodedata.normalize("NFC", arguments["part"])
        return session.last[part]["solid"]["bbox"]
    except (KeyError, TypeError):
        return None


def _with_coerced(payload: object, notes: list[str]) -> object:
    """`payload` mit "coerced" als erstem Schlüssel; schon vorhandene Notizen (aus dem Aufbau) folgen den neuen."""
    old = payload.get("coerced", []) if isinstance(payload, dict) else []
    return {"coerced": notes + old} | {k: v for k, v in payload.items() if k != "coerced"}


class D2CServer(MCPServer):
    """MCPServer, der Werkzeugargumente vor der Validierung umdeutet (d2c.server.coerce) und Argumentfehler auf Englisch meldet."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        try:
            args, notes = coerce.normalize(name, arguments, _part_bbox(arguments))
        except Exception:  # Die Umdeutung darf nie ein Werkzeug blockieren: dann gilt die Eingabe unverändert.
            log.exception("coerce.normalize(%s) fehlgeschlagen", name)
            args, notes = arguments, []
        try:
            result = await super().call_tool(name, args, context)
        except ToolError as e:
            if isinstance(e.__cause__, ValidationError):
                message = validation_error_message(e.__cause__)
                if notes:
                    message += "\nCoercions: " + "; ".join(notes)
                log.warning("%s: %s", name, message)
                raise ToolError(message) from e.__cause__
            raise
        return self._report_coerced(result, notes)

    @staticmethod
    def _report_coerced(result: Any, notes: list[str]) -> Any:
        """Setzt die Notizen in das JSON des ersten Textteils und in structured_content; sonst hängt es einen Textteil an."""
        first = result.content[0] if getattr(result, "content", None) else None
        payload = None
        if isinstance(first, TextContent):
            try:
                payload = json.loads(first.text)
            except ValueError:
                pass
        if isinstance(payload, dict):
            if notes or "coerced" in payload:
                first.text = json.dumps(_with_coerced(payload, notes), ensure_ascii=False, separators=(",", ":"))
                if isinstance(result.structured_content, dict):
                    result.structured_content = _with_coerced(result.structured_content, notes)
        elif notes:
            result.content.append(TextContent(type="text", text=json.dumps({"coerced": notes}, ensure_ascii=False)))
        return result


server = D2CServer("drawing2cad", instructions=Path(__file__).with_name("instructions.md").read_text("utf-8"))


def _guard(fn: Callable) -> Callable:
    """Leitet Werkzeugfehler als ToolError weiter; nur ihre Meldung erreicht den Client."""

    @functools.wraps(fn)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ValidationError as e:
            message = validation_error_message(e)
            log.warning("%s: %s", fn.__name__, message)
            raise ToolError(message) from e
        except (KeyError, ValueError, IndexError) as e:
            message = e.args[0] if e.args else str(e)
            log.warning("%s: %s", fn.__name__, message)
            raise ToolError(message) from e

    return guarded


def _answer(part: str, report: dict) -> dict:
    """Die Antwort eines Aufbauschritts: der kompakte Bericht mit `next`; D2C_REPORT=full liefert den vollen Worker-Bericht."""
    if report_full():
        return report
    part = unicodedata.normalize("NFC", part)
    return guide.compact(report, session.specs[part], session.ops[part], part)


def _tool(fn: Callable) -> Callable:
    return server.tool()(_guard(fn))


def _op_tool(model: type[OpBase]) -> Callable[[Callable], Callable]:
    """Registriert ein Aufbauwerkzeug mit den Feldern eines Op-Modells als Parametern, ohne `op`.

    Der dekorierte Stub liefert nur Namen und Docstring. Typen, Standardwerte und Beschreibungen stammen
    aus dem Modell, damit Werkzeug und Op-Schema nicht auseinanderlaufen. Das Werkzeug erstellt das
    Op-Modell (und validiert es) und übergibt model_dump() an die Session.
    """
    op = get_args(model.model_fields["op"].annotation)[0]
    params = [inspect.Parameter("part", inspect.Parameter.KEYWORD_ONLY, annotation=str)]
    for name, field in model.model_fields.items():
        if name != "op":
            note = Field(description=field.description, discriminator=field.discriminator)
            params.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=inspect.Parameter.empty if field.is_required() else field.default,
                    annotation=Annotated[field.annotation, *field.metadata, note],
                )
            )

    def register(stub: Callable) -> Callable:
        def build(**fields: Any) -> dict:
            part = fields.pop("part")
            return _answer(part, session.apply(part, model(op=op, **fields).model_dump()))

        build.__name__, build.__doc__ = stub.__name__, stub.__doc__
        build.__signature__ = inspect.Signature(params, return_annotation=dict)
        return _tool(build)

    return register


@_tool
def render_drawing(pdf: str, page: int = 1, dpi: int = 100, crop: list[float] | None = None) -> list:
    """Render a page of a drawing PDF, or a crop of it, as a PNG image.

    The PDF path is relative to the data folder. The text part of the result is JSON with the page count
    ("pages"), the page size in pt and the "dpi" actually used (at most 600; the longest image edge is
    at most 2000 px). `crop` has the form [x0, y0, x1, y1] as fractions of the displayed page with the
    origin at the top left; [0, 0, 0.5, 0.5], for example, denotes the top left quarter.
    Page numbering starts at 1.

    Example: render_drawing(pdf="Part A/drawing.pdf", page=1, dpi=300, crop=[0.1, 0.1, 0.6, 0.5])
    """
    info = pages.pdf_info(pdf, page)
    png = pages.render(pdf, page, dpi, crop)
    used = pages.effective_dpi(info, dpi, crop)
    session.gates.note_read(pages.resolve_drawing(pdf), page, sharp=crop is not None and used >= SHARP_DPI)
    return [Image(data=png, format="png"), json.dumps(info | {"dpi": used})]


def _text_summary(items: list[dict]) -> dict:
    """Die Textelemente mit den gedruckten Nennwerten (ohne Abmaße und Gewinde) und dem Maßstab aus dem Schriftfeld."""
    dims = {
        i["parsed"]["nominal"]
        for i in items
        if i["parsed"] and not i["parsed"]["thread"] and i["text"].lstrip()[:1] not in ("+", "-", "±", "−")
    }
    return {"items": items, "printed_dims": sorted(dims), "title_scale": title_block_ratio(items)}


@_tool
def read_drawing_text(pdf: str, page: int = 1, region: list[float] | None = None) -> str:
    """Read the text layer of a drawing page: printed dimensions, fits, tolerances and notes.

    Call it first for every page. Result: {"items", "printed_dims", "title_scale"}. `items` are
    {text, x0, y0, x1, y1, font, size, vertical, parsed}; x0..y1 are fractions of the page (origin at the top
    left, like `crop` of render_drawing). `parsed` splits a dimension into prefix, count, nominal, tol_upper,
    tol_lower, fit, envelope, thread (otherwise null). `printed_dims` are the distinct nominal values (for
    measure_drawing), `title_scale` is the title block scale such as "2:1" or null. Instead of the result you
    get a message if the page has no text layer (read the image) or more than 300 items (pass a smaller `region`).

    Example: read_drawing_text(pdf="Part A/drawing.pdf", page=1, region=[0.5, 0.8, 1, 1])
    """
    path = pages.resolve_drawing(pdf)
    items = read_text(path, page, region)
    session.gates.note_read(path, page)
    if not items:
        if region:
            return "No text in this region: enlarge it or read the dimensions from the rendered image."
        return "No text layer present (text is vector outlines): read the dimensions from the rendered image."
    if len(items) > MAX_TEXT_ITEMS:
        return f"{len(items)} text items, more than {MAX_TEXT_ITEMS}: pass a smaller `region` to read them."
    return json.dumps(_text_summary(items), ensure_ascii=False, separators=(",", ":"))


@_tool
def measure_drawing(
    pdf: str,
    page: int = 1,
    region: list[float] | None = None,
    scale: float | None = None,
    printed_dims: Sequence[float] = (),
    expect: Sequence[str] = (),
    edge_width: str | float = "auto",
) -> dict:
    """Measure circles, arcs and overall dimensions from the vector geometry of a drawing page in model mm.

    For dimensions that are not printed, to cross-check dimensions you have read, and to catch a wrong title
    block scale. `region` is [x0, y0, x1, y1] as fractions of the page, origin at the top left (one view).
    `scale` is drawing:model (2 for 2:1, 0.5 for 1:2). `printed_dims`: printed lengths and diameters (at least
    six); the result then contains `scale_suggestion`, best first. `circle_groups` counts all circles per
    diameter (`circles` shows only the first twelve). `expect` holds claims such as "d=19", "r=10", "L=49"
    and returns ok, MISMATCH or no_candidates for each. `edge_width` is "auto", "all" or a line width
    in pt. Coordinates are mm from the bottom left corner of the region, y points up. Raster pages have
    no geometry. `lines` are the 40 longest straight lines of the region (x0, y0, x1, y1, length_mm, angle_deg
    against the u axis from 0 to 180, in_contour; runs of short collinear pieces are merged): read unprinted angles
    and edge positions there, with a `region` as small as one view. Circles and `verify` results are remembered
    for the export gates: a measured circle without a counterpart in the model and a MISMATCH that is neither
    corrected nor named by a review item with issue="conflict" make the first export_step refuse.

    Example: measure a missing width in a view at scale 2:1 with measure_drawing(pdf="Part A/drawing.pdf",
    region=[0.1, 0.08, 0.97, 0.45], scale=2) and read `extent_mm`.
    """
    path = pages.resolve_drawing(pdf)
    if isinstance(edge_width, str) and edge_width not in ("auto", "all"):
        edge_width = float(edge_width)
    if scale is not None and not scale > 0:
        raise ValueError(f"`scale` must be positive, e.g. 2 for 2:1; received: {scale}")
    try:
        res = vector.extract(path, page, region, scale if scale is not None else 1.0, edge_width)
        suggestion = vector.suggest_scale(path, printed_dims, page, region) if printed_dims else []
    except pages.PYMUPDF_ERRORS as e:
        raise ValueError(f"Drawing could not be read: {e}") from e
    out = {"scale_used": res["scale_used"]}
    if printed_dims:
        out["scale_suggestion"] = suggestion
    groups = Counter(round(c["d_mm"], 2) for c in res["circles"])
    out |= {
        "extent_mm": res["extent_mm"],
        "circles_total": len(res["circles"]),
        "circle_groups": [{"d_mm": d, "count": n} for d, n in sorted(groups.items(), reverse=True)],
        "circles": res["circles"][:MAX_SHAPES],
        "arcs_total": len(res["arcs"]),
        "arcs": res["arcs"][:MAX_SHAPES],
        "lines": res["lines"][:MAX_LINES],
    }
    verdicts = vector.verify(res, expect) if expect else []
    if expect:
        out["verify"] = verdicts
    session.note_measured(path, page)
    # Ohne angegebenen Maßstab misst measure_drawing im Maßstab 1:1; nur mit ihm sagen Kreise und Prüfwerte etwas über das Modell.
    view = tuple(region) if region and scale else None
    session.gates.note_measure(path, page, printed_dims, view, res["circles"], verdicts if scale else [])
    gate["measured"] += 1
    notes = scale_notes(scale, suggestion, len(printed_dims))
    cut = [f"{n} {name}" for name, n in (("circles", len(res["circles"])), ("arcs", len(res["arcs"]))) if n > MAX_SHAPES]
    if cut:
        notes.append(
            f"At most {MAX_SHAPES} of each are shown; present: {', '.join(cut)} (circles: see circle_groups). Choose a smaller `region`."
        )
    return out | {"notes": notes}


def _require_measured(spec: PartSpec) -> None:
    """ValueError, wenn die Spezifikation angenommene Maße hat und measure_drawing nicht auf ihrer Zeichnungsseite lief."""
    assumed = [d.id for d in spec.dimensions if d.assumed]
    if assumed and not session.was_measured(pages.resolve_drawing(spec.drawing), spec.page):
        raise ValueError(
            f"The dimensions {assumed} are assumed, but measure_drawing did not run on page {spec.page} of "
            f"{spec.drawing!r}: call measure_drawing(pdf={spec.drawing!r}, page={spec.page}, region=<the view "
            "with the dimension>, scale=<title block scale, 2 for 2:1>), take the value from `extent_mm`, "
            "`circles` or `arcs`, and call submit_spec again."
        )


def _previous(name: str) -> PartSpec | None:
    """Die zuletzt übernommene Spezifikation des Bauteils (aus dem Speicher oder von results/), sonst None."""
    name = unicodedata.normalize("NFC", name)
    if name in session.specs:
        return session.specs[name]
    try:
        return PartSpec.model_validate_json((results_root() / name / "spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _followed(spec: PartSpec) -> list[Finding]:
    """Befunde, wenn ein keep_ops-Aufruf die Soll-Werte ohne geändertes Maß an das gebaute Modell anpasst. Eine Revision
    eines exportierten Teils (STEP oder changes.json) ist davon ausgenommen: Dort ändert die Zeichnung die Soll-Werte."""
    name = unicodedata.normalize("NFC", spec.name)
    folder = results_root() / name
    previous, solid = _previous(name), (session.last.get(name) or {}).get("solid")
    if previous is None or (folder / f"{name}.step").is_file() or (folder / CHANGES_FILE).is_file():
        return []
    return followed_model(previous, spec, solid)


def _require_new_measurement(spec: PartSpec) -> None:
    """ValueError, wenn ein zuvor angenommenes Maß jetzt als gedruckt gilt, ohne dass seit dem letzten submit_spec
    desselben Bauteils gemessen wurde: So lässt sich eine Annahme nicht durch erneutes Einreichen "waschen"."""
    previous = _previous(spec.name)
    if previous is None:
        return
    was_assumed = {d.id for d in previous.dimensions if d.assumed}
    washed = [d.id for d in spec.dimensions if d.id in was_assumed and not d.assumed]
    if washed and gate["measured"] <= gate["submitted"].get(unicodedata.normalize("NFC", spec.name), 0):
        raise ValueError(
            f"The dimensions {washed} were accepted as assumed (`assumed=true`) and would now count as printed, "
            "although no measurement has been taken since. A dimension changes from `assumed=true` to `false` only "
            f"after a new measurement: call measure_drawing(pdf={spec.drawing!r}, page={spec.page}, region=<the view "
            "with the dimension>, scale=<title block scale>) and take the measured value from `extent_mm`, `circles` or "
            "`arcs`. If the dimension is still assumed, resubmit the spec unchanged with `assumed=true`."
        )


@_tool
def check_spec(spec: PartSpec, second: PartSpec | None = None) -> dict:
    """Check a spec before `submit_spec` for values that cannot be right. Nothing is accepted.

    Returns {"findings": [...], "count": n}. A finding has `code`, `message`, `where` and `suggested_review`
    (a draft review item). Rules: HOLE_OUTSIDE_BBOX (the hole fits the expected bounding box in no position),
    HOLES_OVERLAP, FILLET_TOO_BIG (radius above half the middle edge). With `second`, an independent
    second reading of the same page, every difference is a finding: EXTRACTION_MISMATCH,
    ONLY_IN_A (`spec`), ONLY_IN_B (`second`), HOLE_COUNT.

    Example: check_spec(spec=<the spec as for submit_spec>, second=<a second reading of the page, optional>)
    """
    findings = crosscheck.plausibility(spec)
    if second is not None:
        findings += crosscheck.compare_specs(spec, second)
    return {"findings": findings, "count": len(findings)}


@_tool
def submit_spec(spec: PartSpec, keep_ops: bool = False) -> dict:
    """Accept the reading of a drawing page (dimensions, views, features, notes, expected, review).

    This starts a part; existing ops of a part with the same name are reset. With keep_ops=True the ops
    are kept: their refs are checked against the new spec and the part is rebuilt; the build report is
    returned as "report" (for a correction to expected, review or mark_near). If the part already has an
    exported model, this is a new revision: the answer then contains `changes`, the differences from the
    previous spec (dimensions, tolerances, form and position tolerances, holes, bounding box), and
    `previous_version`, the state saved under versions/. Lengths are in mm and angles
    in degrees; `expected.bbox` and hole positions are in model coordinates (Z up). Every assumed dimension
    and every dimension with `confidence` below 0.8 must be listed in the `refs` of a review item. Assumed
    dimensions require that measure_drawing ran on `spec.drawing` and `spec.page`. The result contains
    `scale_check`, the comparison of `scale` (title block) with the geometry; a clear difference is rejected
    unless a review item has `issue="conflict"`. `warnings` contains the findings of the plausibility rules as
    in check_spec (warnings, not a rejection): accept each as a review item or fix it. On a repeated
    submit_spec, a dimension changes from `assumed=true` to `false` only after a new measurement. Write all
    free text you compose yourself in the spec in English; take `text` and direct quotes from the drawing exactly as printed.

    With keep_ops=True, `expected` must not be set to the values of the built model without a changed dimension: that is
    refused once (the expected values come from the drawing), the same state then passes with a warning in review.md.

    Reading gates (each refuses once per part, the same state then passes with a warning in `warnings` and in
    review.md): every page of a multi-page PDF seen with render_drawing or read_drawing_text; one render_drawing
    with `crop` and at least 200 dpi per drawing; every value passed as `printed_dims` to measure_drawing appears in a
    dimension; if the page has a text layer, dimensions cover at least 25 % of its numeric values (below 60 % only a warning).

    Example: submit_spec(spec={"name": "plate", "drawing": "A/p.pdf", "title": "Plate", "scale": "1:1",
    "views": [{"id": "v1", "kind": "top", "scale": "1:1"}], "dimensions": [{"id": "d1", "kind": "length",
    "value": 80, "text": "80", "source": null, "confidence": 0.95}], "features": [{"id": "f1", "type":
    "base", "dim_ids": ["d1"]}], "expected": {"bbox": [80, 50, 10]}, "review": []})
    """
    # Dieselbe Spezifikation wurde schon geprüft übernommen (z. B. von der Lesesitzung, die Bausitzung reicht sie erneut
    # ein): Lese- und Messgates prüften damals die Zeichnung; ein neuer Serverprozess kennt diese Schritte nicht.
    accepted = _previous(spec.name) == spec
    if not accepted:
        _require_measured(spec)
        _require_new_measurement(spec)
    check = scale_check(spec)
    if check["conflict"] and not any(item.issue == "conflict" for item in spec.review):
        raise ValueError(
            f"Scale conflict: {check['note']}. Add a review item with issue 'conflict' that names both "
            f"scales, measure with the scale of the geometry (measure_drawing, scale={check['best']:g}) and "
            "call submit_spec again."
        )
    hard, soft = ([], []) if accepted else read_findings(spec, session.gates)
    hard = hard + (_followed(spec) if keep_ops and not accepted else [])
    gates = [{"code": "READ_GATE", "message": text, "where": "submit_spec"} for text in session.gates.read_gates(spec.name, hard, soft)]
    session.gates.bind(spec.name, pages.resolve_drawing(spec.drawing), spec.page)
    result = session.submit_spec(spec, keep_ops)
    if "report" in result:
        result["report"] = _answer(result["part"], result["report"])
    else:
        result["next"] = guide.submit_next(spec)
    gate["submitted"][unicodedata.normalize("NFC", spec.name)] = gate["measured"]
    return result | {"scale_check": check, "warnings": crosscheck.plausibility(spec) + gates}


@_op_tool(ExtrudeOp)
def extrude() -> dict:
    """Extrude a profile drawn on a sketch plane and add it to the part, cut it out, or intersect it with the part.

    Lengths are in mm, angles in degrees. Planes: XY has normal +Z (u=X, v=Y), XZ has normal -Y
    (u=X, v=Z), YZ has normal +X (u=Y, v=Z); `plane.offset` shifts along the normal. With positive
    `distance` the extrusion runs in the direction of the plane normal. With `both=True`, `distance`
    is the total thickness, centered on the plane. `refs` maps parameter paths to dimension IDs, for
    example {"profile.w": "d1", "distance": "t1"}.

    The report checks consistency with the captured spec (`dims`), not with the drawing; `dims_found`
    lists printed dimensions that cannot be found in the model.

    Example: extrude(part="plate", profile={"type": "rect", "center": [0, 0], "w": 80, "h": 50},
    plane={"name": "XY"}, distance=10, refs={"profile.w": "d1"})
    """


@_op_tool(RevolveOp)
def revolve() -> dict:
    """Revolve a profile about the global Z axis and add it to the part, cut it out, or intersect it with the part.

    The profile lies in the XZ plane with u = r (distance from the axis in mm) and v = z (in mm), so
    r >= 0 is required. Pass `points` (an automatically closed polygon of at least three [r, z]) or `profile`
    (a path with arcs and fillets). `angle` is the angle in degrees, counterclockwise as seen from +Z.
    `refs` maps parameter paths to dimension IDs, e.g. {"points.1.0": "d2"}.

    The report checks consistency with the captured spec (`dims`), not with the drawing; `dims_found`
    lists printed dimensions that cannot be found in the model.

    Example: revolve(part="pin", points=[[0, 0], [5, 0], [5, 30], [0, 30]])
    """


@_op_tool(HolesOp)
def add_holes() -> dict:
    """Drill plain, counterbored, countersunk or threaded holes into the part from a sketch plane.

    Lengths are in mm, angles in degrees. Planes: XY has normal +Z (u=X, v=Y), XZ has normal -Y
    (u=X, v=Z), YZ has normal +X (u=Y, v=Z). Holes are drilled against the plane normal unless
    `flip=True`; so place the plane on the entry face (for XY on the top face, with `offset` set to its
    height). Positions are [u, v] in the plane; alternatively use `polar`.
    Without `depth` (omit it) the hole is drilled through. `refs` maps parameter paths to dimension IDs,
    e.g. {"d": "d3", "positions.0.0": "d4"}.

    The report checks consistency with the captured spec (`dims`), not with the drawing; `dims_found`
    lists printed dimensions that cannot be found in the model.

    Example: add_holes(part="plate", plane={"name": "XY", "offset": 10}, d=6.6,
    positions=[[30, 15], [-30, 15]], refs={"d": "d3"})
    """


@_op_tool(EdgeFinishOp)
def edge_finish() -> dict:
    """Fillet or chamfer edges near the given points.

    Lengths are in mm. `near` holds global [x, y, z] points on or near the edges (for example their
    midpoints); `tol` is the largest permitted distance between an edge and a point. Seam edges of
    round faces are skipped. Call the tool directly after the body whose edges it changes. `refs` maps
    parameter paths to dimension IDs, for example {"size": "d5"}.
    Instead of `near`, `on_plane` ({"name": "XY", "offset": 10}) selects every edge lying entirely in that plane,
    e.g. to build a dimensioned radius or chamfer on all alike edges.

    The report checks consistency with the captured spec (`dims`), not with the drawing; `dims_found`
    lists printed dimensions that cannot be found in the model.

    Example: edge_finish(part="plate", kind="fillet", size=2, near=[[40, 0, 5]])
    """


@_tool
def run_script(part: str, code: str) -> dict:
    """Run CadQuery code as the next operation. Use this only if no other tool covers the geometry.

    Lengths are in mm. The code has access to `result` (the cq.Workplane built so far, an empty XY
    workplane for the first operation), `cq`, `math` and a few built-in functions (abs, min, max,
    range, len, float, int, round, sum, enumerate, zip, list, tuple, dict, print). Imports and names with a
    leading `_` are forbidden (including `_` as a loop variable). At the end, `result` must be a cq.Workplane
    holding the solid. Planes follow CadQuery: XY has normal +Z, XZ -Y, YZ +X.

    Example: run_script(part="plate", code="result = result.faces('>Z').workplane().hole(5)")
    """
    return _answer(part, session.apply(part, ScriptOp(op="script", code=code).model_dump()))


@_tool
def edit_op(part: str, index: int, op: Op) -> dict:
    """Replace the operation at `index` (from 0) with a complete new operation and rebuild the part.

    `op` has the same fields as the build tools, plus "op" (extrude, revolve, holes, edge_finish or
    script). Lengths are in mm; the same conventions apply for plane and direction.
    If the new operation equals the old one, the tool answers ok=true with `unchanged` and rebuilds nothing:
    repeating it changes nothing.

    Example: edit_op(part="plate", index=0, op={"op": "extrude", "profile": {"type": "circle", "center":
    [0, 0], "d": 30}, "plane": {"name": "XY"}, "distance": 12})
    """
    result = session.edit_op(part, index, op.model_dump())
    return result if result.get("unchanged") else _answer(part, result)


@_tool
def delete_op(part: str, index: int) -> dict:
    """Delete the operation at `index` (from 0) and rebuild the part. After deleting the only operation the part is empty; then start with a new base body.

    Example: delete_op(part="plate", index=2)
    """
    return _answer(part, session.delete_op(part, index))


@_tool
def move_op(part: str, index: int, new_index: int) -> dict:
    """Move the operation at `index` to position `new_index` (both from 0) and rebuild the part.

    Use this when `edge_finish` fails because it comes too early or too late. The first operation
    must use `add`.

    Example: move_op(part="plate", index=3, new_index=1)
    """
    return _answer(part, session.move_op(part, index, new_index))


@_tool
def get_state(part: str) -> dict:
    """Show the spec summary, the operation list and the latest build report of a part.

    Example: get_state(part="plate")
    """
    return session.state(part)


@_tool
def export_step(part: str, variant: Variant = "nominal") -> dict:
    """Build the part and, if the checks pass, write the STEP file, the report and the review checklist (review.md).

    Lengths in the STEP file are mm. The STEP file is AP242, with the review marks in orange and semantic PMI for
    hole diameters (fit or deviations) and assumed lengths; a GLB file, marks.json and pmi.json are written as well.
    variant="mid" instead builds the part with every toleranced dimension at mid-tolerance and writes only
    <part>_mid.step and report_mid.json (needs the tolerance tables). The result contains {"ok", "files", "report"};
    with ok=false nothing is written and the report names the failed checks. report["compliance"] lists
    every toleranced dimension with in_tolerance true, false or null (nothing bound to it, not checked); if there is
    no binding (refs), the dimension is checked against the measured cylinder diameters, plane distances and axis positions (geometry=true).
    The tolerance tables come from D2C_TABLES, otherwise ISO 2768-1 and ISO 286-1 apply as the default. The result also
    contains "warnings" (assumed dimensions without PMI, toleranced dimensions without a binding, bindings that were not
    shifted), "shifts" (values set to mid-tolerance with variant="mid") and "skipped" (bindings whose value is not a
    multiple of the dimension). report["roundtrip"]["pmi"]["missing"] lists assumed dimensions without PMI.
    The report establishes consistency with the captured spec, not with the drawing itself; `dims_found`
    lists printed dimensions that cannot be found in the model (hint: check, correct, or justify in the review item).

    Gates (the first nominal export of a state refuses with {"ok": false, "error", "next"}, the second passes with a
    warning in review.md): a review item that admits an omitted feature ("not modelled", "simplified"); a note naming a
    chamfer, fillet or draft angle that no op models; a measured circle (measure_drawing) without a counterpart in the
    model; a MISMATCH without a conflict item; a printed dimension missing in the model without a review item.

    Example: export_step(part="plate", variant="mid")
    """
    return session.export(part, variant)

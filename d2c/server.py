"""The MCP server: render and read drawings, submit a spec, build the part op by op, export the STEP."""

import functools
import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, get_args

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from d2c import drawing, vector
from d2c.ops import EdgeFinishOp, ExtrudeOp, HolesOp, Op, OpBase, RevolveOp, ScriptOp
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
        except (KeyError, ValueError, IndexError) as e:  # pydantic's ValidationError is a ValueError
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
    """Render one page of a drawing PDF, or a crop of it, as a PNG image.

    The PDF path is relative to the data folder. The text part of the result is JSON with the page count
    ("pages"), the page size in pt and the effective "dpi" (at most 600; the longest image side is 2000 px).
    `crop` is [x0, y0, x1, y1] as fractions of the displayed page, origin top left, e.g. [0, 0, 0.5, 0.5]
    is the upper left quarter. Pages count from 1.

    Example: render_drawing(pdf="Part A/drawing.pdf", page=1, dpi=300, crop=[0.1, 0.1, 0.6, 0.5])
    """
    info = drawing.pdf_info(pdf, page)
    png = drawing.render(pdf, page, dpi, crop)
    return [Image(data=png, format="png"), json.dumps(info | {"dpi": drawing.effective_dpi(info, dpi, crop)})]


@_tool
def read_drawing_text(pdf: str, page: int = 1, region: list[float] | None = None) -> str:
    """Read the text layer of a drawing page: printed dimensions, fits, tolerances and notes as text.

    Call this first for every page. Returns JSON items {text, x0, y0, x1, y1, font, size, vertical, parsed};
    x0..y1 are fractions of the displayed page, origin top left, like `crop` of render_drawing. `parsed`
    splits a dimension into prefix, count, nominal (mm), tol_upper, tol_lower (mm), fit, envelope, thread;
    it is null for other text. Returns a message instead if the page has no text layer (text drawn as
    outlines: read the image) or has more than 300 items (pass a smaller `region`).

    Example: read_drawing_text(pdf="Part A/drawing.pdf", page=1, region=[0.5, 0.8, 1, 1])
    """
    items = read_text(drawing.resolve_drawing(pdf), page, region)
    if not items:
        if region:
            return "no text in this region: widen it, or read the dimensions from the rendered image"
        return "no text layer (text is vector outlines): read dimensions from the rendered image"
    if len(items) > MAX_TEXT_ITEMS:
        return f"{len(items)} text items, more than {MAX_TEXT_ITEMS}: pass a smaller region to read them"
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _scale_notes(scale: float | None, suggestion: list[dict], printed: int) -> list[str]:
    """Hints on the scale: missing, disagreeing with the geometry, or voted on by too few values."""
    notes = []
    if scale is None:
        notes.append("scale not given: measured at 1:1, pass the title-block scale for real model sizes")
    if suggestion:
        best = suggestion[0]
        if scale is not None and abs(best["scale"] - scale) > SCALE_TOL * scale:
            notes.append(f"title-block scale disagrees with geometry: given {scale:g}, geometry says "
                         f"{best['scale']:g} ({best['votes']} of {printed} printed values)")
        if printed < MIN_PRINTED_DIMS:
            notes.append(f"only {printed} printed_dims: scale_suggestion is unreliable, give {MIN_PRINTED_DIMS} or more")
    elif printed:
        notes.append("no scale fits the printed_dims")
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
    """Measure circles, arcs and the extent from the vector geometry of a drawing page, in model mm.

    Use it for dimensions that are not printed, to cross-check read ones, and to find a wrong title-block
    scale. `region` is [x0, y0, x1, y1] as fractions of the displayed page, origin top left (one view).
    `scale` is drawing:model, 2 for 2:1 and 0.5 for 1:2. `printed_dims` are printed lengths and diameters of
    the views (six or more): the result then holds `scale_suggestion` (best first). `expect` are claims
    like "d=19", "r=10", "L=49" checked against the geometry: status ok, MISMATCH or no_candidates.
    `edge_width` is "auto", "all" or a stroke width in pt. Coordinates are mm from the lower left corner
    of the region, y up. Raster-only pages have no geometry.

    Example, measure a missing width in a view at scale 2:1: measure_drawing(pdf="Part A/drawing.pdf",
    region=[0.1, 0.08, 0.97, 0.45], scale=2) and read extent_mm
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
def submit_spec(spec: PartSpec) -> dict:
    """Register what was read from a drawing page (dimensions, views, features, notes, expected, review).

    Starts a part: it resets the ops of a part with the same name. Lengths are mm, angles degrees; the
    expected bbox and hole positions are in model coordinates (Z up). Every assumed dimension or one with
    confidence below 0.8 must be listed in the refs of a review item.

    Example: submit_spec(spec={"name": "plate", "drawing": "A/p.pdf", "title": "Plate", "scale": "1:1",
    "views": [{"id": "v1", "kind": "top", "scale": "1:1"}], "dimensions": [{"id": "d1", "kind": "length",
    "value": 80, "text": "80", "source": null, "confidence": 0.95}], "features": [{"id": "f1", "type":
    "base", "dim_ids": ["d1"]}], "expected": {"bbox": [80, 50, 10]}, "review": []})
    """
    return session.submit_spec(spec)


@_op_tool(ExtrudeOp)
def extrude() -> dict:
    """Extrude a profile drawn on a sketch plane and add, cut or intersect it with the part.

    Lengths are mm, angles degrees. Planes: XY has normal +Z (u=X, v=Y), XZ normal -Y (u=X, v=Z),
    YZ normal +X (u=Y, v=Z); `plane.offset` shifts along the normal. The extrusion runs along the plane
    normal for a positive `distance`. With both=True, `distance` is the TOTAL thickness, centred on the
    plane. `refs` maps parameter paths to dimension ids, e.g. {"profile.w": "d1", "distance": "t1"}.

    Example: extrude(part="plate", profile={"type": "rect", "center": [0, 0], "w": 80, "h": 50},
    plane={"name": "XY"}, distance=10, refs={"profile.w": "d1"})
    """


@_op_tool(RevolveOp)
def revolve() -> dict:
    """Revolve a profile around the global Z axis and add, cut or intersect it with the part.

    The profile lies in the XZ plane with u = r (distance from the axis, mm) and v = z (mm), so r must be
    >= 0. Give `points` (a closed polygon of [r, z]) or `profile` (a path with arcs and fillets).
    `angle` is degrees, counter-clockwise seen from +Z. `refs` maps parameter paths to dimension ids,
    e.g. {"points.1.0": "d2"}.

    Example: revolve(part="pin", points=[[0, 0], [5, 0], [5, 30], [0, 30]])
    """


@_op_tool(HolesOp)
def add_holes() -> dict:
    """Drill holes (plain, counterbored, countersunk or tapped) from a sketch plane into the part.

    Lengths are mm, angles degrees. Planes: XY has normal +Z (u=X, v=Y), XZ normal -Y (u=X, v=Z),
    YZ normal +X (u=Y, v=Z). Holes drill along -normal of the plane unless `flip=True`, so put the plane
    on the face the holes enter (for XY on the top face, with the offset at its height). Positions are
    [u, v] in the plane, or use `polar`. A `depth` of null drills through. `refs` maps parameter paths to
    dimension ids, e.g. {"d": "d3", "positions.0.0": "d4"}.

    Example: add_holes(part="plate", plane={"name": "XY", "offset": 10}, d=6.6,
    positions=[[30, 15], [-30, 15]], refs={"d": "d3"})
    """


@_op_tool(EdgeFinishOp)
def edge_finish() -> dict:
    """Fillet or chamfer the edges that lie near given points.

    Lengths are mm. `near` are global [x, y, z] points on or near the edges (their midpoints), `tol` is the
    largest distance from an edge to a point. Seam edges of round faces are skipped. Call it right after
    the body whose edges it changes. `refs` maps parameter paths to dimension ids, e.g. {"size": "d5"}.

    Example: edge_finish(part="plate", kind="fillet", size=2, near=[[40, 0, 5]])
    """


@_tool
def run_script(part: str, code: str) -> dict:
    """Run CadQuery code as the next op. Use only if no other tool covers the geometry.

    Lengths are mm. The code gets `result` (the cq.Workplane built so far, an empty XY workplane for the
    first op), `cq`, `math` and a few builtins (abs, min, max, range, len, float, int, round, sum,
    enumerate, zip, list, tuple, dict, print). No imports and no names starting with `_` (not even `_` as
    a loop variable). It must leave a cq.Workplane with the solid in `result`. Planes follow CadQuery:
    XY normal +Z, XZ normal -Y, YZ normal +X.

    Example: run_script(part="plate", code="result = result.faces('>Z').workplane().hole(5)")
    """
    return session.apply(part, ScriptOp(op="script", code=code).model_dump())


@_tool
def edit_op(part: str, index: int, op: Op) -> dict:
    """Replace the op at `index` (0-based) with a complete new op and rebuild the part.

    `op` has the same fields as the build tools plus "op" (extrude, revolve, holes, edge_finish or
    script). Lengths are mm; plane and direction conventions as in those tools.

    Example: edit_op(part="plate", index=0, op={"op": "extrude", "profile": {"type": "circle", "center":
    [0, 0], "d": 30}, "plane": {"name": "XY"}, "distance": 12})
    """
    return session.edit_op(part, index, op.model_dump())


@_tool
def delete_op(part: str, index: int) -> dict:
    """Delete the op at `index` (0-based) and rebuild the part. Deleting the only op is rejected.

    Example: delete_op(part="plate", index=2)
    """
    return session.delete_op(part, index)


@_tool
def move_op(part: str, index: int, new_index: int) -> dict:
    """Move the op at `index` to position `new_index` (both 0-based) and rebuild the part.

    Use it when an `edge_finish` fails because it comes too late or too early. The first op must add.

    Example: move_op(part="plate", index=3, new_index=1)
    """
    return session.move_op(part, index, new_index)


@_tool
def get_state(part: str) -> dict:
    """Show the spec summary, the op list and the last build report of a part.

    Example: get_state(part="plate")
    """
    return session.state(part)


@_tool
def export_step(part: str) -> dict:
    """Build the part and write the STEP file, the report and the review list (review.md) if all checks pass.

    Lengths in the STEP are mm. Returns {"ok", "files", "report"}; with ok=false nothing is written and the
    report names the failed checks.

    Example: export_step(part="plate")
    """
    return session.export(part)


if __name__ == "__main__":
    server.run("stdio")

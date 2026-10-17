"""Operation models and the CadQuery build.

A part is a list of operations. Each one makes its own solid, which is united with the
result so far (mode "add"), cut from it (mode "cut") or intersected with it (mode "intersect").
Units are mm and degrees.
Sketch planes: XY has normal +Z (u=X, v=Y), XZ has normal -Y (u=X, v=Z),
YZ has normal +X (u=Y, v=Z).
"""
import builtins
import functools
import itertools
import math
import re
from typing import Annotated, Any, Literal, Self, Union

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.StdFail import StdFail_NotDone
from pydantic import BaseModel, Field, field_validator, model_validator

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]
Mode = Literal["add", "cut", "intersect"]

COARSE_PITCH = {2.0: 0.4, 2.5: 0.45, 3.0: 0.5, 4.0: 0.7, 5.0: 0.8, 6.0: 1.0, 8.0: 1.25, 10.0: 1.5, 12.0: 1.75,
                16.0: 2.0, 20.0: 2.5}
TAP_DRILL = {2.0: 1.6, 2.5: 2.05, 3.0: 2.5, 4.0: 3.3, 5.0: 4.2, 6.0: 5.0, 8.0: 6.8, 10.0: 8.5, 12.0: 10.2}
MINOR_FACTOR = 1.082532  # ISO 68-1: minor diameter = d - 1.082532 * pitch
CORNER_TOL = 0.5  # mm: a fillet finds its corner within this distance of `at`
MIN_TAPER = 0.001  # degrees, smaller drafts crash OCC
TAPER_LENGTH_TOL = 0.01  # mm, allowed difference between requested and built length of a tapered extrusion
HULL_SAMPLES = 7200  # directions at which the support function of a circle hull is sampled
NO_EFFECT = "removed no material: check plane, offset and direction"
# What a ScriptOp may use. check_script (d2c/sandbox.py) rejects imports and names starting with "_" before it runs.
SCRIPT_BUILTINS = {
    name: getattr(builtins, name)
    for name in "abs min max range len float int round sum enumerate zip list tuple dict print".split()
}


class BuildError(Exception):
    """An operation cannot be turned into geometry."""


def _index(key: str) -> int:
    """A list index of a dotted path: a non-negative whole number."""
    if not key.isdecimal():
        raise KeyError(key)
    return int(key)


def get_path(d: dict, path: str) -> Any:
    """Read the value at a dotted path in a dumped op, e.g. "profile.w" or "positions.1.0"."""
    node: Any = d
    try:
        for key in path.split("."):
            node = node[_index(key)] if isinstance(node, (list, tuple)) else node[key]
    except (KeyError, IndexError, TypeError):
        raise KeyError(path) from None
    return node


def set_path(d: dict, path: str, value: float) -> None:
    """Write a value at an existing dotted path of a dumped op, in place. Tuples on the way are rebuilt."""

    def put(node: Any, keys: list[str]) -> Any:
        key = _index(keys[0]) if isinstance(node, (list, tuple)) else keys[0]
        old = node[key]  # raises when the path does not exist
        child = value if len(keys) == 1 else put(old, keys[1:])
        if isinstance(node, tuple):
            return node[:key] + (child,) + node[key + 1:]
        node[key] = child
        return node

    try:
        put(d, path.split("."))
    except (KeyError, IndexError, TypeError):
        raise KeyError(path) from None


class _Model(BaseModel, extra="forbid", use_attribute_docstrings=True):
    """Base of all op parts: unknown fields are errors, attribute docstrings become schema descriptions."""


class SketchPlane(_Model):
    """Sketch plane. XY: normal +Z, u=X, v=Y. XZ: normal -Y, u=X, v=Z. YZ: normal +X, u=Y, v=Z."""

    name: Literal["XY", "XZ", "YZ", "custom"]
    offset: float = 0.0
    """Shift along the plane normal in mm."""
    origin: Point3 | None = None
    """custom only: a point on the plane."""
    normal: Point3 | None = None
    """custom only: plane normal, any length."""
    xdir: Point3 | None = None
    """custom only, optional: direction of u. Default: global X (global Y if the normal runs along X)."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        given = [k for k in ("origin", "normal", "xdir") if getattr(self, k) is not None]
        if self.name != "custom":
            if given:
                raise ValueError(f"plane {self.name} takes no {', '.join(given)}")
        elif self.origin is None or self.normal is None:
            raise ValueError("plane custom requires origin and normal")
        else:
            self._frame()  # rejects a zero normal and an xdir along the normal
        return self

    def _frame(self) -> tuple[cq.Vector, cq.Vector, cq.Vector]:
        """Origin (offset applied), u axis and normal as global unit vectors."""
        if self.name != "custom":
            base = cq.Plane.named(self.name)
            origin, u, n = base.origin, base.xDir, base.zDir
        else:
            n = cq.Vector(*self.normal)
            if n.Length < 1e-9:
                raise ValueError("normal must not be zero")
            n = n.normalized()
            origin = cq.Vector(*self.origin)
            if self.xdir is None:
                u = _across(cq.Vector(1, 0, 0), n) or _across(cq.Vector(0, 1, 0), n)
            else:
                u = _across(cq.Vector(*self.xdir), n)
                if u is None:
                    raise ValueError("xdir must not be zero or parallel to the normal")
        return origin + n * self.offset, u, n

    def workplane(self) -> cq.Workplane:
        origin, u, n = self._frame()
        return cq.Workplane(cq.Plane(origin, u, n))


def _across(v: cq.Vector, n: cq.Vector) -> cq.Vector | None:
    """Part of v perpendicular to the unit vector n, normalised; None if there is none."""
    part = v - n * v.dot(n)
    return part.normalized() if part.Length > 1e-9 else None


def _round_corners(face: cq.Face, vertices: list[cq.Vertex], r: float) -> cq.Face:
    try:
        return face.fillet2D(r, vertices)
    except StdFail_NotDone:
        raise BuildError(f"2D fillet R{r} does not fit: radius too large, or the two edges are tangent there") from None


class RectProfile(_Model):
    """Rectangle in plane coordinates (u, v)."""

    type: Literal["rect"]
    center: Point2
    w: float = Field(gt=0)
    """Size along u in mm."""
    h: float = Field(gt=0)
    """Size along v in mm."""
    corner_radius: float = Field(default=0.0, ge=0)
    """Radius in mm rounding all four corners, 0 for sharp corners."""

    def face(self, wp: cq.Workplane) -> cq.Face:
        face = cq.Face.makeFromWires(wp.center(*self.center).rect(self.w, self.h).val())
        return _round_corners(face, face.Vertices(), self.corner_radius) if self.corner_radius else face


class CircleProfile(_Model):
    """Circle in plane coordinates (u, v)."""

    type: Literal["circle"]
    center: Point2
    d: float = Field(gt=0)
    """Diameter in mm."""

    def face(self, wp: cq.Workplane) -> cq.Face:
        return cq.Face.makeFromWires(wp.center(*self.center).circle(self.d / 2).val())


class SlotProfile(_Model):
    """Slot with two round ends in plane coordinates (u, v)."""

    type: Literal["slot"]
    center: Point2
    center_distance: float = Field(gt=0)
    """Distance between the two arc centres in mm."""
    width: float = Field(gt=0)
    """Slot width = arc diameter in mm."""
    angle: float = 0.0
    """Rotation of the slot axis away from +u in degrees."""

    def face(self, wp: cq.Workplane) -> cq.Face:
        # slot2D takes the overall length: arc centre distance plus one width
        wire = wp.center(*self.center).slot2D(self.center_distance + self.width, self.width, self.angle).val()
        return cq.Face.makeFromWires(wire)


class LineSegment(_Model):
    """Straight line from the current point."""

    kind: Literal["line"]
    to: Point2

    def draw(self, wp: cq.Workplane) -> cq.Workplane:
        return wp.lineTo(*self.to)


class Arc3Segment(_Model):
    """Circular arc from the current point through `via` to `to`."""

    kind: Literal["arc3"]
    via: Point2
    """Any point on the arc between start and end."""
    to: Point2

    def draw(self, wp: cq.Workplane) -> cq.Workplane:
        return wp.threePointArc(self.via, self.to)


class RadiusArcSegment(_Model):
    """r > 0: clockwise arc, r < 0: counter-clockwise, at most 180 degrees. Prefer arc3."""

    kind: Literal["radius_arc"]
    to: Point2
    r: float
    """Arc radius in mm, must be at least half the distance to `to`."""

    @field_validator("r")
    @classmethod
    def _nonzero(cls, r: float) -> float:
        if r == 0:
            raise ValueError("r must not be 0")
        return r

    def draw(self, wp: cq.Workplane) -> cq.Workplane:
        return wp.radiusArc(self.to, self.r)


Segment = Annotated[Union[LineSegment, Arc3Segment, RadiusArcSegment], Field(discriminator="kind")]


class CornerFillet(_Model):
    """Rounds one corner of a path profile."""

    at: Point2
    """Position (u, v) of the sharp corner, found within CORNER_TOL (0.5 mm)."""
    r: float = Field(gt=0)
    """Fillet radius in mm."""


class PathProfile(_Model):
    """Outline from `start` along the segments, closed automatically back to `start`.

    Tangent transitions: draw the sharp corner, then round it with `fillets`.
    """

    type: Literal["path"]
    start: Point2
    segments: list[Segment] = Field(min_length=1)
    fillets: list[CornerFillet] = []

    def face(self, wp: cq.Workplane) -> cq.Face:
        wp = wp.moveTo(*self.start)
        for segment in self.segments:
            wp = segment.draw(wp)
        face = cq.Face.makeFromWires(wp.close().val())
        if face.Area() < 1e-9:
            raise BuildError("path profile encloses no area")
        for fillet in self.fillets:
            corner = wp.plane.toWorldCoords(fillet.at)
            hits = [v for v in face.Vertices() if (v.Center() - corner).Length <= CORNER_TOL]
            if len(hits) != 1:
                raise BuildError(
                    f"fillet at {fillet.at}: {len(hits)} corners within {CORNER_TOL} mm, need exactly 1")
            face = _round_corners(face, hits, fillet.r)
        return face


class HullProfile(_Model):
    """Convex hull of circles: hubs and lugs joined by straight outer tangents, no fillets."""

    type: Literal["hull"]
    circles: list[Point3] = Field(min_length=2)
    """Circles as (u, v, r) in mm, every r > 0. A circle inside the hull of the others adds nothing."""

    @field_validator("circles")
    @classmethod
    def _positive_radius(cls, circles: list[Point3]) -> list[Point3]:
        if any(r <= 0 for _, _, r in circles):
            raise ValueError("every circle radius must be > 0")
        return circles

    @functools.cached_property
    def order(self) -> list[int]:
        """Indices of the circles on the hull, counter-clockwise from direction +u (a circle may repeat)."""
        best = []
        for k in range(HULL_SAMPLES):
            eu, ev = math.cos(2 * math.pi * k / HULL_SAMPLES), math.sin(2 * math.pi * k / HULL_SAMPLES)
            support = [round(u * eu + v * ev + r, 9) for u, v, r in self.circles]  # rounding makes ties exact
            best.append(support.index(max(support)))  # the lowest index wins a tie
        runs = [(i, len(list(group))) for i, group in itertools.groupby(best)]
        if len(runs) > 1 and runs[0][0] == runs[-1][0]:
            runs[0] = (runs[0][0], runs[0][1] + runs.pop()[1])
        # a one-sample run is a tie between circles, not an arc of the hull
        order = [i for i, _ in itertools.groupby(i for i, n in runs if n > 1)]
        if len(order) > 1 and order[0] == order[-1]:
            order.pop()
        return order

    def _point(self, i: int, angle: float) -> Point2:
        u, v, r = self.circles[i]
        return u + r * math.cos(angle), v + r * math.sin(angle)

    def _normal(self, i: int, j: int) -> float:
        """Angle of the outward normal of the outer tangent from circle i to circle j: n . (cj - ci) = ri - rj."""
        (ui, vi, ri), (uj, vj, rj) = self.circles[i], self.circles[j]
        return math.atan2(vj - vi, uj - ui) - math.acos((ri - rj) / math.hypot(uj - ui, vj - vi))

    def outline(self) -> CircleProfile | PathProfile:
        """The hull as an exact profile: an arc on every circle of `order`, joined by tangent lines."""
        order = self.order
        if len(order) == 1:
            u, v, r = self.circles[order[0]]
            return CircleProfile(type="circle", center=(u, v), d=2 * r)
        normals = [self._normal(i, j) for i, j in zip(order, order[1:] + order[:1])]  # normals[k] leaves order[k]
        segments: list[Arc3Segment | LineSegment] = []
        for k, i in enumerate(order):
            arrive, leave = normals[k - 1], normals[k]
            sweep = (leave - arrive) % (2 * math.pi)
            segments.append(Arc3Segment(kind="arc3", via=self._point(i, arrive + sweep / 2), to=self._point(i, leave)))
            segments.append(LineSegment(kind="line", to=self._point(order[(k + 1) % len(order)], leave)))
        return PathProfile(type="path", start=self._point(order[0], normals[-1]), segments=segments)

    def face(self, wp: cq.Workplane) -> cq.Face:
        return self.outline().face(wp)


Profile = Annotated[Union[RectProfile, CircleProfile, SlotProfile, PathProfile, HullProfile],
                    Field(discriminator="type")]


class OpBase(_Model):
    """Fields shared by all operations. Each one implements apply(shape) -> (new shape, build-note fields)."""

    refs: dict[str, str] = {}
    """Maps numeric parameters to Dimension ids, e.g. {"d": "d3", "profile.w": "d7", "positions.1.0": "d9"}.
    Keys are dotted paths into the op, list indices are numbers."""

    @model_validator(mode="after")
    def _check_refs(self) -> Self:
        dumped = self.model_dump()
        for path in self.refs:
            try:
                value = get_path(dumped, path)
            except KeyError:
                raise ValueError(f"refs path {path!r} does not exist") from None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"refs path {path!r} is not a number")
        return self


def _combine(shape: cq.Shape | None, tool: cq.Shape, mode: Mode) -> cq.Shape:
    if shape is None:
        if mode != "add":
            raise BuildError("the first operation must have mode 'add'")
        return tool
    if mode == "add":
        return shape.fuse(tool).clean()
    if mode == "cut":
        return shape.cut(tool).clean()
    if mode == "intersect":
        return shape.intersect(tool).clean()
    raise BuildError(f"unknown mode {mode!r}")


def _no_effect(before: cq.Shape | None, after: cq.Shape, mode: Mode) -> dict:
    """Build-note fields warning about a cut or intersect that left the volume unchanged."""
    if before is not None and mode != "add" and before.Volume() - after.Volume() <= 1e-9 * before.Volume():
        return {"warning": NO_EFFECT}
    return {}


def _require(shape: cq.Shape | None) -> cq.Shape:
    if shape is None:
        raise BuildError("needs an existing solid: the first operation must add one")
    return shape


def _require_full_length(tool: cq.Solid, normal: cq.Vector, distance: float) -> None:
    """A draft can collapse the profile early, OCC then returns a shorter solid without complaint."""
    heights = [v.Center().dot(normal) for v in tool.Vertices()]
    length = max(heights) - min(heights)
    if abs(length - abs(distance)) > TAPER_LENGTH_TOL:
        raise BuildError(f"taper too large: the profile collapses after {length:.2f} mm, "
                         f"reduce taper or distance ({abs(distance):g} mm requested)")


class ExtrudeOp(OpBase):
    """Extrude a profile drawn on a sketch plane along the plane normal."""

    op: Literal["extrude"]
    profile: Profile
    plane: SketchPlane
    distance: float
    """Length in mm along the plane normal, negative runs against it."""
    mode: Mode = "add"
    """add: unite with the part, cut: remove from the part, intersect: keep only what lies in both."""
    both: bool = False
    """Extrude symmetrically about the plane. distance is then the total thickness."""
    taper: float = Field(default=0.0, gt=-90, lt=90)
    """Draft angle in degrees over the whole length, positive narrows the profile in extrusion direction.
    0 or at least 0.001 in size. With both=True the draft runs from the face at -distance/2 to the face at
    +distance/2 and is not mirrored about the plane. A draft that collapses the profile before the end is an error."""

    @field_validator("distance")
    @classmethod
    def _nonzero(cls, distance: float) -> float:
        if distance == 0:
            raise ValueError("distance must not be 0")
        return distance

    @field_validator("taper")
    @classmethod
    def _usable_taper(cls, taper: float) -> float:
        if 0 < abs(taper) < MIN_TAPER:  # OCC crashes the interpreter on a draft this small
            raise ValueError(f"taper must be 0 or at least {MIN_TAPER} degrees")
        return taper

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        wp = self.plane.workplane()
        normal = wp.plane.zDir
        face = self.profile.face(wp)
        if self.both:
            face = face.translate(normal * (-self.distance / 2))
        tool = cq.Solid.extrudeLinear(face, normal * self.distance, taper=self.taper)
        if self.taper:
            _require_full_length(tool, normal, self.distance)
        result = _combine(shape, tool, self.mode)
        extra = {"taper": self.taper} if self.taper else {}
        if isinstance(self.profile, HullProfile):
            extra["hull_circles"] = self.profile.order
        return result, extra | _no_effect(shape, result, self.mode)


class RevolveOp(OpBase):
    """Revolve a profile drawn on XZ (u = r, v = z) around the global Z axis. Give points or profile."""

    op: Literal["revolve"]
    points: list[Point2] | None = Field(default=None, min_length=3)
    """Polygon of (r, z) points, closed automatically."""
    profile: PathProfile | None = None
    """Outline with arcs and fillets in (r, z), instead of points."""
    angle: float = Field(default=360.0, gt=0, le=360)
    """Sweep angle in degrees, counter-clockwise seen from +Z."""
    mode: Mode = "add"
    """add: unite with the part, cut: remove from the part, intersect: keep only what lies in both."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.points is None) == (self.profile is None):
            raise ValueError("give exactly one of points or profile")
        if self.points is not None:
            radii = [r for r, _ in self.points]
        else:
            corners = [self.profile.start]
            for s in self.profile.segments:
                corners += [s.via, s.to] if isinstance(s, Arc3Segment) else [s.to]
            radii = [r for r, _ in corners]
        if min(radii) < 0:
            raise ValueError("all r (first coordinate) must be >= 0")
        return self

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        profile = self.profile or PathProfile(
            type="path", start=self.points[0], segments=[LineSegment(kind="line", to=p) for p in self.points[1:]])
        face = profile.face(cq.Workplane("XZ"))
        if face.BoundingBox().xmin < -1e-6:
            raise BuildError("profile crosses the revolve axis (r < 0): check arc via points / radius_arc sign")
        tool = cq.Solid.revolve(face, self.angle, (0, 0, 0), (0, 0, 1))
        result = _combine(shape, tool, self.mode)
        return result, _no_effect(shape, result, self.mode)


class PolarPattern(_Model):
    """Holes evenly spaced on a circle."""

    center: Point2
    """Circle centre (u, v)."""
    pitch_d: float = Field(gt=0)
    """Pitch circle diameter in mm."""
    count: int = Field(ge=1)
    start_angle: float = 0.0
    """Angle of the first hole from +u in degrees, the others follow every 360/count."""


def _thread(spec: str) -> tuple[float, float]:
    """Nominal diameter and pitch of "M5" (coarse) or "M10x1"."""
    match = re.fullmatch(r"M(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?", spec)
    if match is None:
        raise ValueError(f"thread {spec!r} must look like M5 or M10x1")
    nominal = float(match[1])
    pitch = float(match[2]) if match[2] else COARSE_PITCH.get(nominal)
    if pitch is None:
        raise ValueError(f"thread {spec!r} has no known coarse pitch, write M{match[1]}x<pitch>")
    return nominal, pitch


class HolesOp(OpBase):
    """Holes drilled from a sketch plane against its normal (flip: along it). Give positions or polar."""

    op: Literal["holes"]
    plane: SketchPlane
    """Plane the holes start on, usually the face they enter."""
    positions: list[Point2] = []
    """Hole centres (u, v) in the plane."""
    polar: PolarPattern | None = None
    """Holes on a circle, instead of positions."""
    d: float = Field(gt=0)
    """Hole diameter in mm. For kind tapped: the nominal thread diameter."""
    depth: float | None = Field(default=None, gt=0)
    """Drill depth in mm, null drills through the part."""
    kind: Literal["plain", "cbore", "csk", "tapped"] = "plain"
    """cbore: counterbore, csk: countersink, tapped: threaded hole."""
    cbore_d: float | None = Field(default=None, gt=0)
    """Counterbore diameter in mm."""
    cbore_depth: float | None = Field(default=None, gt=0)
    """Counterbore depth in mm."""
    csk_d: float | None = Field(default=None, gt=0)
    """Countersink diameter at the surface in mm."""
    csk_angle: float = Field(default=90.0, gt=0, lt=180)
    """Included countersink angle in degrees."""
    thread: str | None = None
    """kind tapped only: M5 or M10x1. Modelled as a plain hole, no thread geometry."""
    thread_core: Literal["tap_drill", "minor"] = "tap_drill"
    """Drilled diameter of a tapped hole: tap_drill (M5: 4.2) or minor, the thread's core diameter (M5: 4.134)."""
    flip: bool = False
    """Drill along the plane normal instead of against it."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if bool(self.positions) == (self.polar is not None):
            raise ValueError("give exactly one of positions (not empty) or polar")
        if (self.kind == "tapped") != (self.thread is not None):
            raise ValueError("thread is required for kind 'tapped' and allowed only there")
        if self.thread is not None and abs(_thread(self.thread)[0] - self.d) > 1e-9:
            raise ValueError(f"d ({self.d}) must equal the nominal diameter of thread {self.thread}")
        if (self.kind != "cbore" and (self.cbore_d is not None or self.cbore_depth is not None)) or (
                self.kind != "csk" and self.csk_d is not None):
            raise ValueError("cbore_d, cbore_depth and csk_d only go with kind 'cbore' or 'csk'")
        if self.kind == "cbore":
            if self.cbore_d is None or self.cbore_depth is None:
                raise ValueError("kind 'cbore' needs cbore_d and cbore_depth")
            if self.cbore_d <= self.d:
                raise ValueError("cbore_d must be larger than d")
            deepest = self.cbore_depth
        elif self.kind == "csk":
            if self.csk_d is None:
                raise ValueError("kind 'csk' needs csk_d")
            if self.csk_d <= self.d:
                raise ValueError("csk_d must be larger than d")
            deepest = self._csk_depth()
        else:
            return self
        if self.depth is not None and deepest >= self.depth:
            raise ValueError("the counterbore or countersink must be shallower than depth")
        return self

    def _csk_depth(self) -> float:
        return (self.csk_d - self.d) / 2 / math.tan(math.radians(self.csk_angle) / 2)

    def _drilled_d(self) -> float:
        """Diameter actually drilled: d, or for a tapped hole the tap drill or minor diameter."""
        if self.kind != "tapped":
            return self.d
        nominal, pitch = _thread(self.thread)
        if self.thread_core == "minor":
            core = nominal - MINOR_FACTOR * pitch
        elif pitch == COARSE_PITCH.get(nominal):
            core = TAP_DRILL.get(nominal, nominal - pitch)
        else:
            core = nominal - pitch
        return round(core, 6)

    def _centers(self) -> list[Point2]:
        if self.polar is None:
            return self.positions
        p = self.polar
        angles = [math.radians(p.start_angle + i * 360 / p.count) for i in range(p.count)]
        return [(p.center[0] + p.pitch_d / 2 * math.cos(a), p.center[1] + p.pitch_d / 2 * math.sin(a)) for a in angles]

    @staticmethod
    def _through_length(shape: cq.Shape, origin: cq.Vector, drill: cq.Vector) -> float:
        """Longer than the part: bounding box diagonal plus the gap between the plane and the box, plus 1 mm."""
        box = shape.BoundingBox()
        corners = itertools.product((box.xmin, box.xmax), (box.ymin, box.ymax), (box.zmin, box.zmax))
        gap = min((cq.Vector(*c) - origin).dot(drill) for c in corners)
        return box.DiagonalLength + max(gap, 0.0) + 1

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        shape = _require(shape)
        wp = self.plane.workplane()
        drill = wp.plane.zDir * (1 if self.flip else -1)
        length = self.depth if self.depth is not None else self._through_length(shape, wp.plane.origin, drill)
        drilled = self._drilled_d()
        centers = self._centers()
        tools = []
        for center in centers:
            start = wp.plane.toWorldCoords(center)
            tools.append(cq.Solid.makeCylinder(drilled / 2, length, start, drill))
            if self.kind == "cbore":
                tools.append(cq.Solid.makeCylinder(self.cbore_d / 2, self.cbore_depth, start, drill))
            elif self.kind == "csk":
                tools.append(cq.Solid.makeCone(self.csk_d / 2, drilled / 2, self._csk_depth(), start, drill))
        result = shape.cut(*tools).clean()
        return result, {"drilled_d": drilled, "holes": len(centers)} | _no_effect(shape, result, "cut")


def _is_seam(edge: cq.Edge, shape: cq.Shape) -> bool:
    """A seam edge borders the same face on both sides, like the cut line of a cylinder."""
    return any(BRep_Tool.IsClosed_s(edge.wrapped, face.wrapped) for face in edge.ancestors(shape, "Face"))


class EdgeFinishOp(OpBase):
    """Fillet or chamfer every edge within tol of a near point. Seam edges of round faces are skipped."""

    op: Literal["edge_finish"]
    kind: Literal["fillet", "chamfer"]
    size: float = Field(gt=0)
    """Fillet radius or chamfer length in mm."""
    near: list[Point3] = Field(min_length=1)
    """Global points (x, y, z) on or near the edges, for example their midpoints."""
    tol: float = Field(default=0.5, gt=0)
    """Largest distance in mm between an edge and a near point."""

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        shape = _require(shape)
        points = [cq.Vertex.makeVertex(*p) for p in self.near]
        edges = [e for e in shape.Edges() if min(e.distance(p) for p in points) <= self.tol and not _is_seam(e, shape)]
        if not edges:
            raise BuildError("no edge within tol of near points")
        # the edges come from this very solid: edges of another instance make the fillet fail
        selected = cq.Workplane(obj=shape).newObject(edges)
        try:
            result = selected.fillet(self.size) if self.kind == "fillet" else selected.chamfer(self.size)
        except StdFail_NotDone:
            raise BuildError(f"{self.kind} {self.size} mm failed on {len(edges)} edge(s): "
                             "size too large for the adjacent faces?") from None
        return result.val(), {"edges_selected": len(edges)}


class ScriptOp(OpBase):
    """Fallback: Python code run with `result` (the cq.Workplane so far), `cq`, `math` and a few builtins.

    Builtins: abs, min, max, range, len, float, int, round, sum, enumerate, zip, list, tuple, dict, print.
    No imports and no names starting with "_" (not even `_` as a loop variable).
    It must leave a cq.Workplane holding the solid in `result`.
    """

    op: Literal["script"]
    code: str

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        wp = cq.Workplane("XY") if shape is None else cq.Workplane(obj=shape)
        scope = {"__builtins__": dict(SCRIPT_BUILTINS), "cq": cq, "math": math, "result": wp}
        exec(compile(self.code, "<script>", "exec"), scope)
        if not isinstance(scope["result"], cq.Workplane):
            raise BuildError("the script must leave a cq.Workplane in `result`")
        return scope["result"].findSolid(), {}


Op = Annotated[Union[ExtrudeOp, RevolveOp, HolesOp, EdgeFinishOp, ScriptOp], Field(discriminator="op")]


def build(ops: list[Op]) -> tuple[cq.Workplane, list[dict]]:
    """Build the ops in order. Returns the model and notes like {"op_index": 2, "edges_selected": 1}."""
    shape: cq.Shape | None = None
    notes: list[dict] = []
    for index, op in enumerate(ops):
        try:
            shape, extra = op.apply(shape)
        except BuildError as e:
            raise BuildError(f"op {index} ({op.op}): {e}") from None
        except Exception as e:  # CadQuery, OpenCascade or script errors: say which op failed
            raise BuildError(f"op {index} ({op.op}): {type(e).__name__}: {e}") from e
        if extra:
            notes.append({"op_index": index, **extra})
    if shape is None:
        raise BuildError("no operations")
    return cq.Workplane(obj=shape), notes

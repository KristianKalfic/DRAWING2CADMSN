"""Modelle der Vorgänge und Aufbau mit CadQuery.

Ein Bauteil besteht aus einer Liste von Vorgängen. Jeder erzeugt einen eigenen Körper,
der mit dem bisherigen Ergebnis vereinigt (Modus "add"), davon abgezogen (Modus "cut")
oder damit geschnitten wird (Modus "intersect").
Einheiten: mm und Grad.
Skizzenebenen: XY mit Normale +Z (u=X, v=Y), XZ mit Normale -Y (u=X, v=Z),
YZ mit Normale +X (u=Y, v=Z)."""

import builtins
import functools
import itertools
import math
import re
import types
from typing import Annotated, Literal, Self, Union

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.Standard import Standard_Failure
from OCP.StdFail import StdFail_NotDone
from pydantic import BaseModel, Field, field_validator, model_validator

from d2c.errors import NO_EFFECT as NO_EFFECT_PREFIX
from d2c.oppath import get_path

# OpenCascade meldet eine fehlgeschlagene Verrundung je nach Build als StdFail_NotDone (Linux) oder als
# Standard_Failure (macOS); die beiden Python-Klassen liegen in keiner Ableitungsbeziehung zueinander.
STD_FAILURES = (Standard_Failure, StdFail_NotDone)

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]
Mode = Literal["add", "cut", "intersect"]

COARSE_PITCH = {2.0: 0.4, 2.5: 0.45, 3.0: 0.5, 4.0: 0.7, 5.0: 0.8, 6.0: 1.0, 8.0: 1.25, 10.0: 1.5, 12.0: 1.75, 16.0: 2.0, 20.0: 2.5}
TAP_DRILL = {2.0: 1.6, 2.5: 2.05, 3.0: 2.5, 4.0: 3.3, 5.0: 4.2, 6.0: 5.0, 8.0: 6.8, 10.0: 8.5, 12.0: 10.2}
MINOR_FACTOR = 1.082532  # ISO 68-1: Kerndurchmesser = d - 1.082532 * Gewindesteigung
ARC_R_TOL = 0.5  # mm: größte Abweichung zwischen dem gedruckten Radius und dem Abstand von `via` zum Mittelpunkt
PLANE_TOL = 0.01  # mm: größter Abstand einer Kante von der Ebene `on_plane`, damit sie darin liegt
CORNER_TOL = 0.5  # mm: In diesem Abstand zu at wird die Ecke für eine Rundung gefunden
MIN_TAPER = 0.001  # Grad; kleinere Verjüngungen bringen OCC zum Absturz
TAPER_LENGTH_TOL = 0.01  # mm: zulässige Abweichung zwischen angeforderter und erzeugter Länge einer verjüngten Extrusion
HULL_SAMPLES = 7200  # Richtungen, in denen die Stützfunktion einer Kreishülle abgetastet wird
NO_EFFECT = f"{NO_EFFECT_PREFIX}: check plane, offset and direction."
# Namensraum von ScriptOp (Positivliste): Das Skript sieht nicht das Modul cq, sondern nur diese CadQuery-Klassen,
# `math` und die Builtins unten. check_script (d2c/cad/sandbox.py) weist zusätzlich Imports, gefährliche Namen und
# Namen mit führendem "_" vor der Ausführung zurück.
SCRIPT_CLASSES = "Workplane Vector Plane Sketch Solid Compound Location Edge Wire Face Shape".split()
SCRIPT_CQ = types.SimpleNamespace(**{name: getattr(cq, name) for name in SCRIPT_CLASSES})
SCRIPT_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "abs min max range len float int str bool round sum sorted enumerate zip list tuple dict set any all "
        "isinstance reversed divmod pow map print"
    ).split()
}


class BuildError(Exception):
    """An operation cannot be turned into geometry."""


class _Model(BaseModel, extra="forbid", use_attribute_docstrings=True):
    """Base class of all operation parts: unknown fields cause errors; attribute docstrings become schema descriptions."""


class SketchPlane(_Model):
    """Sketch plane. XY: normal +Z, u=X, v=Y. XZ: normal -Y, u=X, v=Z. YZ: normal +X, u=Y, v=Z."""

    name: Literal["XY", "XZ", "YZ", "custom"]
    offset: float = 0.0
    """Shift along the plane normal in mm."""
    origin: Point3 | None = None
    """Only for custom: a point on the plane."""
    normal: Point3 | None = None
    """Only for custom: plane normal, any length."""
    xdir: Point3 | None = None
    """Only for custom, optional: direction of u. Default: global X (global Y if the normal runs along X)."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        given = [k for k in ("origin", "normal", "xdir") if getattr(self, k) is not None]
        if self.name != "custom":
            if given:
                raise ValueError(f"Plane {self.name} does not accept these fields: {', '.join(given)}")
        elif self.origin is None or self.normal is None:
            raise ValueError("The plane `custom` requires `origin` and `normal`.")
        else:
            self._frame()  # Weist eine Nullnormale und eine xdir entlang der Normalen zurück
        return self

    def _frame(self) -> tuple[cq.Vector, cq.Vector, cq.Vector]:
        """Ursprung (mit angewendetem Versatz), u-Achse und Normale als globale Einheitsvektoren."""
        if self.name != "custom":
            base = cq.Plane.named(self.name)
            origin, u, n = base.origin, base.xDir, base.zDir
        else:
            n = cq.Vector(*self.normal)
            if n.Length < 1e-9:
                raise ValueError("`normal` must not be the zero vector.")
            n = n.normalized()
            origin = cq.Vector(*self.origin)
            if self.xdir is None:
                u = _across(cq.Vector(1, 0, 0), n) or _across(cq.Vector(0, 1, 0), n)
            else:
                u = _across(cq.Vector(*self.xdir), n)
                if u is None:
                    raise ValueError("`xdir` must be neither the zero vector nor parallel to `normal`.")
        return origin + n * self.offset, u, n

    def workplane(self) -> cq.Workplane:
        origin, u, n = self._frame()
        return cq.Workplane(cq.Plane(origin, u, n))


def _across(v: cq.Vector, n: cq.Vector) -> cq.Vector | None:
    """Anteil von v senkrecht zum Einheitsvektor n, normiert; None, falls keiner vorhanden ist."""
    part = v - n * v.dot(n)
    return part.normalized() if part.Length > 1e-9 else None


def _round_corners(face: cq.Face, vertices: list[cq.Vertex], r: float) -> cq.Face:
    try:
        return face.fillet2D(r, vertices)
    except STD_FAILURES:
        raise BuildError(f"The 2D fillet R{r} does not fit: radius too large or the two edges are tangent there.") from None


class RectProfile(_Model):
    """Rectangle in the (u, v) coordinates of the plane."""

    type: Literal["rect"]
    center: Point2
    w: float = Field(gt=0)
    """Size along u in mm."""
    h: float = Field(gt=0)
    """Size along v in mm."""
    corner_radius: float = Field(default=0.0, ge=0)
    """Radius in mm to round all four corners; 0 leaves them sharp."""

    def face(self, wp: cq.Workplane) -> cq.Face:
        face = cq.Face.makeFromWires(wp.center(*self.center).rect(self.w, self.h).val())
        return _round_corners(face, face.Vertices(), self.corner_radius) if self.corner_radius else face


class CircleProfile(_Model):
    """Circle in the (u, v) coordinates of the plane."""

    type: Literal["circle"]
    center: Point2
    d: float = Field(gt=0)
    """Diameter in mm."""

    def face(self, wp: cq.Workplane) -> cq.Face:
        return cq.Face.makeFromWires(wp.center(*self.center).circle(self.d / 2).val())


class SlotProfile(_Model):
    """Slot with two round ends in the (u, v) coordinates of the plane."""

    type: Literal["slot"]
    center: Point2
    center_distance: float = Field(gt=0)
    """Distance between the centers of the two arcs in mm."""
    width: float = Field(gt=0)
    """Slot width = arc diameter in mm."""
    angle: float = 0.0
    """Rotation angle of the slot axis, measured from +u, in degrees."""

    def face(self, wp: cq.Workplane) -> cq.Face:
        # slot2D erwartet die Gesamtlänge: Abstand der Bogenmittelpunkte plus eine Breite
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
    """Any point of the arc between start and end."""
    to: Point2
    r: float | None = Field(default=None, gt=0)
    """Printed arc radius in mm. Use it whenever the radius is printed: `via` is then projected onto the circle of exactly this radius through start and end, so a rounded `via` cannot distort the radius, and arcs over 180 degrees are possible. `via` must lie within 0.5 mm of that circle (only its side of the chord matters)."""

    def draw(self, wp: cq.Workplane) -> cq.Workplane:
        if self.r is None:
            return wp.threePointArc(self.via, self.to)
        start = wp._findFromPoint(True)
        a, b = (start.x, start.y), self.to
        chord = math.dist(a, b)
        if chord > 2 * self.r + 1e-9:
            raise BuildError(f"The arc with r={self.r:g} is too small for the chord {chord:.3f} mm (r must be at least {chord / 2:.3f}).")
        # Die beiden Kreismittelpunkte durch Anfang und Ende; gewählt wird der, zu dem `via` den Abstand r am besten hält
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        h = math.sqrt(max(self.r**2 - (chord / 2) ** 2, 0.0))
        nu, nv = (a[1] - b[1]) / chord, (b[0] - a[0]) / chord
        centers = [(mid[0] + s * h * nu, mid[1] + s * h * nv) for s in (1, -1)]
        center = min(centers, key=lambda c: abs(math.dist(c, self.via) - self.r))
        off = math.dist(center, self.via)
        if abs(off - self.r) > ARC_R_TOL:
            raise BuildError(f"`via` {self.via} does not fit r={self.r:g}: its distance to the arc centre is {off:.3f} mm.")
        return wp.threePointArc(
            (center[0] + self.r * (self.via[0] - center[0]) / off, center[1] + self.r * (self.via[1] - center[1]) / off), self.to
        )


class RadiusArcSegment(_Model):
    """r > 0: arc clockwise; r < 0: counterclockwise; at most 180 degrees. Prefer arc3."""

    kind: Literal["radius_arc"]
    to: Point2
    r: float
    """Arc radius in mm; must be at least half the distance to `to`."""

    @field_validator("r")
    @classmethod
    def _nonzero(cls, r: float) -> float:
        if r == 0:
            raise ValueError("`r` must not be 0.")
        return r

    def draw(self, wp: cq.Workplane) -> cq.Workplane:
        return wp.radiusArc(self.to, self.r)


Segment = Annotated[Union[LineSegment, Arc3Segment, RadiusArcSegment], Field(discriminator="kind")]


class CornerFillet(_Model):
    """Fillets one corner of a path profile."""

    at: Point2
    """Position (u, v) of the sharp corner; searched within CORNER_TOL (0.5 mm)."""
    r: float = Field(gt=0)
    """Fillet radius in mm."""


class PathProfile(_Model):
    """Contour that begins at `start`, runs through the segments in `segments` and is closed automatically at `start`.

    Tangent transitions: first draw the sharp corner, then round it with `fillets`.
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
            raise BuildError("The path profile encloses no area.")
        for fillet in self.fillets:
            corner = wp.plane.toWorldCoords(fillet.at)
            hits = [v for v in face.Vertices() if (v.Center() - corner).Length <= CORNER_TOL]
            if len(hits) != 1:
                raise BuildError(f"Fillet at {fillet.at}: found {len(hits)} corners within {CORNER_TOL} mm, exactly 1 is required.")
            face = _round_corners(face, hits, fillet.r)
        return face


class HullProfile(_Model):
    """Convex hull of circles: hubs and lugs are joined by straight outer tangents, without fillets."""

    type: Literal["hull"]
    circles: list[Point3] = Field(min_length=2)
    """Circles as (u, v, r) in mm, each with r > 0. A circle inside the hull of the others contributes nothing."""

    @field_validator("circles")
    @classmethod
    def _positive_radius(cls, circles: list[Point3]) -> list[Point3]:
        if any(r <= 0 for _, _, r in circles):
            raise ValueError("Every circle radius must be > 0.")
        return circles

    @functools.cached_property
    def order(self) -> list[int]:
        """Indizes der Kreise auf der Hülle, gegen den Uhrzeigersinn ab +u (ein Kreis kann mehrfach vorkommen)."""
        best = []
        for k in range(HULL_SAMPLES):
            eu, ev = math.cos(2 * math.pi * k / HULL_SAMPLES), math.sin(2 * math.pi * k / HULL_SAMPLES)
            support = [round(u * eu + v * ev + r, 9) for u, v, r in self.circles]  # Durch Rundung werden Gleichstände exakt
            best.append(support.index(max(support)))  # Bei Gleichstand gewinnt der niedrigste Index
        runs = [(i, len(list(group))) for i, group in itertools.groupby(best)]
        if len(runs) > 1 and runs[0][0] == runs[-1][0]:
            runs[0] = (runs[0][0], runs[0][1] + runs.pop()[1])
        # Eine Folge aus einem Musterpunkt ist ein Gleichstand zwischen Kreisen, kein Kreisbogen der Hülle
        order = [i for i, _ in itertools.groupby(i for i, n in runs if n > 1)]
        if len(order) > 1 and order[0] == order[-1]:
            order.pop()
        return order

    def _point(self, i: int, angle: float) -> Point2:
        u, v, r = self.circles[i]
        return u + r * math.cos(angle), v + r * math.sin(angle)

    def _normal(self, i: int, j: int) -> float:
        """Winkel der nach außen gerichteten Normalen der äußeren Tangente von Kreis i nach Kreis j: n . (cj - ci) = ri - rj."""
        (ui, vi, ri), (uj, vj, rj) = self.circles[i], self.circles[j]
        return math.atan2(vj - vi, uj - ui) - math.acos((ri - rj) / math.hypot(uj - ui, vj - vi))

    def outline(self) -> CircleProfile | PathProfile:
        """Die Hülle als exaktes Profil: ein Kreisbogen je Kreis aus order, verbunden durch Tangenten."""
        order = self.order
        if len(order) == 1:
            u, v, r = self.circles[order[0]]
            return CircleProfile(type="circle", center=(u, v), d=2 * r)
        normals = [self._normal(i, j) for i, j in zip(order, order[1:] + order[:1])]  # normals[k] führt von order[k] weg
        segments: list[Arc3Segment | LineSegment] = []
        for k, i in enumerate(order):
            arrive, leave = normals[k - 1], normals[k]
            sweep = (leave - arrive) % (2 * math.pi)
            segments.append(Arc3Segment(kind="arc3", via=self._point(i, arrive + sweep / 2), to=self._point(i, leave)))
            segments.append(LineSegment(kind="line", to=self._point(order[(k + 1) % len(order)], leave)))
        return PathProfile(type="path", start=self._point(order[0], normals[-1]), segments=segments)

    def face(self, wp: cq.Workplane) -> cq.Face:
        return self.outline().face(wp)


Profile = Annotated[Union[RectProfile, CircleProfile, SlotProfile, PathProfile, HullProfile], Field(discriminator="type")]


class OpBase(_Model):
    """Common fields of all operations. Each implements apply(shape) -> (new shape, fields for the build report)."""

    refs: dict[str, str] = {}
    """Maps numeric parameters to dimension IDs, e.g. {"d": "d3", "profile.w": "d7", "positions.1.0": "d9"}.
    The keys are dot-separated paths in the operation; list indices are numbers."""

    @model_validator(mode="after")
    def _check_refs(self) -> Self:
        dumped = self.model_dump()
        for path in self.refs:
            try:
                value = get_path(dumped, path)
            except KeyError:
                raise ValueError(f"The `refs` path {path!r} does not exist.") from None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"The `refs` path {path!r} does not point to a number.")
        return self


def _combine(shape: cq.Shape | None, tool: cq.Shape, mode: Mode) -> cq.Shape:
    if shape is None:
        if mode != "add":
            raise BuildError("The first operation must have mode 'add'.")
        return tool
    if mode == "add":
        return shape.fuse(tool).clean()
    if mode == "cut":
        return shape.cut(tool).clean()
    if mode == "intersect":
        return shape.intersect(tool).clean()
    raise BuildError(f"Unknown mode {mode!r}.")


def _no_effect(before: cq.Shape | None, after: cq.Shape, mode: Mode) -> dict:
    """Hinweisfelder zum Aufbau, wenn ein cut- oder intersect-Vorgang das Volumen unverändert lässt."""
    if before is not None and mode != "add" and before.Volume() - after.Volume() <= 1e-9 * before.Volume():
        return {"warning": NO_EFFECT}
    return {}


def _require(shape: cq.Shape | None) -> cq.Shape:
    if shape is None:
        raise BuildError("A solid must already exist: the first operation must add material.")
    return shape


def _require_full_length(tool: cq.Solid, normal: cq.Vector, distance: float) -> None:
    """Eine Verjüngung kann das Profil vorzeitig zusammenfallen lassen; OCC liefert dann kommentarlos einen kürzeren Körper."""
    heights = [v.Center().dot(normal) for v in tool.Vertices()]
    length = max(heights) - min(heights)
    if abs(length - abs(distance)) > TAPER_LENGTH_TOL:
        raise BuildError(
            f"Taper too large: the profile collapses after {length:.2f} mm. Reduce `taper` or `distance` (requested: {abs(distance):g} mm)."
        )


class ExtrudeOp(OpBase):
    """Extrudes a profile drawn on a sketch plane along the plane normal."""

    op: Literal["extrude"]
    profile: Profile
    plane: SketchPlane
    distance: float
    """Length in mm along the plane normal; negative values run against it."""
    mode: Mode = "add"
    """add: unite with the part; cut: remove from the part; intersect: keep only the common geometry."""
    both: bool = False
    """Extrude symmetrically about the plane. `distance` is then the total thickness."""
    taper: float = Field(default=0.0, gt=-90, lt=90)
    """Taper angle over the full length in degrees; positive values narrow the profile in the extrusion direction.
    Either 0 or at least 0.001 degrees. With `both=True` the taper runs from the face at
    -distance/2 to the face at +distance/2 and is not mirrored at the plane. A taper that makes
    the profile collapse before the end is not allowed."""

    @field_validator("distance")
    @classmethod
    def _nonzero(cls, distance: float) -> float:
        if distance == 0:
            raise ValueError("`distance` must not be 0.")
        return distance

    @field_validator("taper")
    @classmethod
    def _usable_taper(cls, taper: float) -> float:
        if 0 < abs(taper) < MIN_TAPER:  # OCC bringt den Interpreter bei einer so kleinen Verjüngung zum Absturz
            raise ValueError(f"`taper` must be 0 or at least {MIN_TAPER} degrees.")
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


def _step_points(steps: list[tuple[float, float]]) -> list[Point2]:
    """Halbprofil einer abgesetzten Welle in (r, z): auf der Achse beginnen, je Absatz Schulter und Mantel, auf der Achse enden."""
    points, z = [(0.0, 0.0)], 0.0
    for d, length in steps:
        points += [(d / 2, z), (d / 2, z + length)]
        z += length
    return points + [(0.0, z)]


class RevolveOp(OpBase):
    """Revolves a profile drawn on XZ (u = r, v = z) about the global Z axis. Pass points, profile or steps."""

    op: Literal["revolve"]
    points: list[Point2] | None = Field(default=None, min_length=3)
    """Polygon of (r, z) points; closed automatically."""
    profile: PathProfile | None = None
    """Contour of arcs and fillets in (r, z) instead of points."""
    steps: list[tuple[Annotated[float, Field(gt=0)], Annotated[float, Field(gt=0)]]] | None = Field(default=None, min_length=1)
    """Stepped shaft as [[Ø, length], …] from z=0 upwards, e.g. [[30, 50], [45, 20]]; refs on "steps.1.0" is Ø45."""
    angle: float = Field(default=360.0, gt=0, le=360)
    """Rotation angle in degrees, counterclockwise as seen from +Z."""
    mode: Mode = "add"
    """add: unite with the part; cut: remove from the part; intersect: keep only the common geometry."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if sum(x is not None for x in (self.points, self.profile, self.steps)) != 1:
            raise ValueError("Give exactly one of the fields `points`, `profile` or `steps`.")
        if self.steps is not None:
            return self
        if self.points is not None:
            radii = [r for r, _ in self.points]
        else:
            corners = [self.profile.start]
            for s in self.profile.segments:
                corners += [s.via, s.to] if isinstance(s, Arc3Segment) else [s.to]
            radii = [r for r, _ in corners]
        if min(radii) < 0:
            raise ValueError("All r values (first coordinate) must be >= 0.")
        return self

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        profile = self.profile
        if profile is None:
            points = self.points or _step_points(self.steps)
            profile = PathProfile(type="path", start=points[0], segments=[LineSegment(kind="line", to=p) for p in points[1:]])
        face = profile.face(cq.Workplane("XZ"))
        if face.BoundingBox().xmin < -1e-6:
            raise BuildError("The profile crosses the axis of revolution (r < 0): check arc points and the sign of `radius_arc`.")
        tool = cq.Solid.revolve(face, self.angle, (0, 0, 0), (0, 0, 1))
        result = _combine(shape, tool, self.mode)
        return result, _no_effect(shape, result, self.mode)


class PolarPattern(_Model):
    """Holes evenly distributed on a circle."""

    center: Point2
    """Center of the circle (u, v)."""
    pitch_d: float = Field(gt=0)
    """Pitch circle diameter in mm."""
    count: int = Field(ge=1)
    start_angle: float = 0.0
    """Angle of the first hole, measured from +u, in degrees; the others follow at intervals of 360/count."""


def _thread(spec: str) -> tuple[float, float]:
    """Nenndurchmesser und Gewindesteigung für M5 (Regelgewinde) oder M10x1."""
    match = re.fullmatch(r"M(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?", spec)
    if match is None:
        raise ValueError(f"`thread` {spec!r} must look like M5 or M10x1.")
    nominal = float(match[1])
    pitch = float(match[2]) if match[2] else COARSE_PITCH.get(nominal)
    if pitch is None:
        raise ValueError(f"No standard pitch is known for `thread` {spec!r}; give M{match[1]}x<pitch>.")
    return nominal, pitch


class HolesOp(OpBase):
    """Holes that start at a sketch plane and run against its normal (with flip: along the normal). Pass positions or polar."""

    op: Literal["holes"]
    plane: SketchPlane
    """Plane on which the holes start, usually the entry face."""
    positions: list[Point2] = []
    """Hole centers (u, v) in the plane."""
    polar: PolarPattern | None = None
    """Holes on a circle instead of given with `positions`."""
    d: float = Field(gt=0)
    """Hole diameter in mm. With `kind=tapped`: nominal thread diameter."""
    depth: float | None = Field(default=None, gt=0)
    """Drilling depth in mm; null drills through the part."""
    kind: Literal["plain", "cbore", "csk", "tapped"] = "plain"
    """cbore: counterbore, csk: countersink, tapped: threaded hole."""
    cbore_d: float | None = Field(default=None, gt=0)
    """Counterbore diameter in mm."""
    cbore_depth: float | None = Field(default=None, gt=0)
    """Counterbore depth in mm."""
    csk_d: float | None = Field(default=None, gt=0)
    """Countersink diameter at the surface in mm."""
    csk_angle: float = Field(default=90.0, gt=0, lt=180)
    """Included angle of the countersink in degrees."""
    thread: str | None = None
    """Only with `kind=tapped`: M5 or M10x1. Modeled as a plain hole without thread geometry."""
    thread_core: Literal["tap_drill", "minor"] = "tap_drill"
    """Hole diameter for a thread: tap_drill (default) is the tap drill size most CAD hole features model (M3: 2.5, M5: 4.2); minor is the thread core diameter D1 (M3: 2.459, M5: 4.134), for parts modelled that way."""
    flip: bool = False
    """Drill along the plane normal instead of against it."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if bool(self.positions) == (self.polar is not None):
            raise ValueError("Give exactly one of `positions` (non-empty) or `polar`.")
        if (self.kind == "tapped") != (self.thread is not None):
            raise ValueError("`thread` is required for `kind='tapped'` and allowed only there.")
        if self.thread is not None and abs(_thread(self.thread)[0] - self.d) > 1e-9:
            raise ValueError(f"`d` ({self.d}) must equal the nominal diameter of the thread {self.thread}.")
        if (self.kind != "cbore" and (self.cbore_d is not None or self.cbore_depth is not None)) or (
            self.kind != "csk" and self.csk_d is not None
        ):
            raise ValueError("`cbore_d`, `cbore_depth` and `csk_d` are allowed only with `kind='cbore'` or `kind='csk'`.")
        if self.kind == "cbore":
            if self.cbore_d is None or self.cbore_depth is None:
                raise ValueError("`kind='cbore'` requires `cbore_d` and `cbore_depth`.")
            if self.cbore_d <= self.d:
                raise ValueError("`cbore_d` must be greater than `d`.")
            deepest = self.cbore_depth
        elif self.kind == "csk":
            if self.csk_d is None:
                raise ValueError("`kind='csk'` requires `csk_d`.")
            if self.csk_d <= self.d:
                raise ValueError("`csk_d` must be greater than `d`.")
            deepest = self._csk_depth()
        else:
            return self
        if self.depth is not None and deepest >= self.depth:
            raise ValueError("The counterbore/countersink must be shallower than `depth`.")
        return self

    def _csk_depth(self) -> float:
        return (self.csk_d - self.d) / 2 / math.tan(math.radians(self.csk_angle) / 2)

    def _drilled_d(self) -> float:
        """Tatsächlich gebohrter Durchmesser: d; bei einem Gewindeloch der Vorbohr- oder Kerndurchmesser."""
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
        """Länger als das Bauteil: Diagonale des Begrenzungsrahmens plus Abstand zwischen Ebene und Rahmen plus 1 mm."""
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
    """Eine Nahtkante grenzt auf beiden Seiten an dieselbe Fläche, etwa die Schnittlinie eines Zylinders."""
    return any(BRep_Tool.IsClosed_s(edge.wrapped, face.wrapped) for face in edge.ancestors(shape, "Face"))


class EdgeFinishOp(OpBase):
    """Applies a fillet or chamfer to every edge within tol of a near point. Seam edges of round faces are skipped."""

    op: Literal["edge_finish"]
    kind: Literal["fillet", "chamfer"]
    size: float = Field(gt=0)
    """Fillet radius or chamfer length in mm."""
    near: list[Point3] = []
    """Global points (x, y, z) on or near the edges, for example their midpoints."""
    on_plane: SketchPlane | None = None
    """Instead of `near`: every edge lying entirely in this plane, e.g. all edges of a pocket floor or top face; use it for a dimensioned radius or chamfer that applies to all alike edges."""
    tol: float = Field(default=0.5, gt=0)
    """Largest distance in mm between an edge and a near point."""

    @model_validator(mode="after")
    def _one_selection(self) -> Self:
        if bool(self.near) == (self.on_plane is not None):
            raise ValueError("Give exactly one of `near` (non-empty) or `on_plane`.")
        return self

    def _edges(self, shape: cq.Shape) -> list[cq.Edge]:
        if self.on_plane is None:
            points = [cq.Vertex.makeVertex(*p) for p in self.near]
            return [e for e in shape.Edges() if min(e.distance(p) for p in points) <= self.tol and not _is_seam(e, shape)]
        origin, _, n = self.on_plane._frame()
        return [
            e
            for e in shape.Edges()
            if all(abs((e.positionAt(t) - origin).dot(n)) <= PLANE_TOL for t in (0, 0.5, 1)) and not _is_seam(e, shape)
        ]

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        shape = _require(shape)
        edges = self._edges(shape)
        if not edges:
            raise BuildError(
                "No edge lies within `tol` of the `near` points." if self.on_plane is None else "No edge lies in the plane `on_plane`."
            )
        # Die Kanten stammen genau aus diesem Körper; Kanten einer anderen Instanz lassen die Verrundung fehlschlagen
        selected = cq.Workplane(obj=shape).newObject(edges)
        try:
            result = selected.fillet(self.size) if self.kind == "fillet" else selected.chamfer(self.size)
        except STD_FAILURES:
            raise BuildError(
                f"{self.kind} of {self.size} mm failed on {len(edges)} edge(s): is the size too large for the adjacent faces?"
            ) from None
        return result.val(), {"edges_selected": len(edges)}


class ScriptOp(OpBase):
    """Fallback: Python code with `result` (the cq.Workplane built so far), `math` and a narrow namespace.

    Available are `cq.<class>` and the class itself for Workplane, Vector, Plane, Sketch, Solid, Compound,
    Location, Edge, Wire, Face and Shape (not the cq module), as well as `math` and the builtins abs, min, max, range,
    len, float, int, str, bool, round, sum, sorted, enumerate, zip, list, tuple, dict, set, any, all, isinstance,
    reversed, divmod, pow, map, print.
    No imports, no file or export functions (export*, import*) and no names with a leading "_"
    (not even `_` as a loop variable).
    At the end, `result` must be a cq.Workplane holding the solid.
    """

    op: Literal["script"]
    code: str

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        wp = cq.Workplane("XY") if shape is None else cq.Workplane(obj=shape)
        scope = {
            "__builtins__": dict(SCRIPT_BUILTINS),
            "cq": SCRIPT_CQ,
            "math": math,
            "result": wp,
            **{name: getattr(cq, name) for name in SCRIPT_CLASSES},
        }
        exec(compile(self.code, "<script>", "exec"), scope)
        if not isinstance(scope["result"], cq.Workplane):
            raise BuildError("The `script` must leave a cq.Workplane in `result`.")
        return scope["result"].findSolid(), {}


Op = Annotated[Union[ExtrudeOp, RevolveOp, HolesOp, EdgeFinishOp, ScriptOp], Field(discriminator="op")]


def build(ops: list[Op]) -> tuple[cq.Workplane, list[dict]]:
    """Baut die Vorgänge der Reihe nach auf. Gibt das Modell und Hinweise zurück, zum Beispiel {"op_index": 2, "edges_selected": 1}."""
    shape: cq.Shape | None = None
    notes: list[dict] = []
    for index, op in enumerate(ops):
        try:
            shape, extra = op.apply(shape)
        except BuildError as e:
            raise BuildError(f"op {index} ({op.op}): {e}") from None
        except Exception as e:  # CadQuery-, OpenCascade- oder Script-Fehler: angeben, welcher Vorgang fehlgeschlagen ist
            raise BuildError(f"op {index} ({op.op}): {type(e).__name__}: {e}") from e
        if extra:
            notes.append({"op_index": index, **extra})
    if shape is None:
        raise BuildError("No operations given.")
    return cq.Workplane(obj=shape), notes

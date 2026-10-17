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
NO_EFFECT = "Kein Material entfernt: Prüfe Ebene, offset und Richtung."
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
    """Skizzenebene. XY: Normale +Z, u=X, v=Y. XZ: Normale -Y, u=X, v=Z. YZ: Normale +X, u=Y, v=Z."""

    name: Literal["XY", "XZ", "YZ", "custom"]
    offset: float = 0.0
    """Verschiebung entlang der Ebenennormalen in mm."""
    origin: Point3 | None = None
    """Nur für custom: ein Punkt auf der Ebene."""
    normal: Point3 | None = None
    """Nur für custom: Ebenennormale, beliebige Länge."""
    xdir: Point3 | None = None
    """Nur für custom, optional: Richtung von u. Standard: global X (global Y, falls die Normale entlang X verläuft)."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        given = [k for k in ("origin", "normal", "xdir") if getattr(self, k) is not None]
        if self.name != "custom":
            if given:
                raise ValueError(f"Ebene {self.name} akzeptiert diese Felder nicht: {', '.join(given)}")
        elif self.origin is None or self.normal is None:
            raise ValueError("Die Ebene `custom` benötigt `origin` und `normal`.")
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
                raise ValueError("`normal` darf nicht der Nullvektor sein.")
            n = n.normalized()
            origin = cq.Vector(*self.origin)
            if self.xdir is None:
                u = _across(cq.Vector(1, 0, 0), n) or _across(cq.Vector(0, 1, 0), n)
            else:
                u = _across(cq.Vector(*self.xdir), n)
                if u is None:
                    raise ValueError("`xdir` darf weder der Nullvektor sein noch parallel zu `normal` verlaufen.")
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
        raise BuildError(f"Die 2D-Rundung R{r} passt nicht: Radius zu groß oder die beiden Kanten sind dort tangential.") from None


class RectProfile(_Model):
    """Rechteck in den Koordinaten (u, v) der Ebene."""

    type: Literal["rect"]
    center: Point2
    w: float = Field(gt=0)
    """Abmessung entlang u in mm."""
    h: float = Field(gt=0)
    """Abmessung entlang v in mm."""
    corner_radius: float = Field(default=0.0, ge=0)
    """Radius in mm zum Abrunden aller vier Ecken; 0 lässt sie scharf."""

    def face(self, wp: cq.Workplane) -> cq.Face:
        face = cq.Face.makeFromWires(wp.center(*self.center).rect(self.w, self.h).val())
        return _round_corners(face, face.Vertices(), self.corner_radius) if self.corner_radius else face


class CircleProfile(_Model):
    """Kreis in den Koordinaten (u, v) der Ebene."""

    type: Literal["circle"]
    center: Point2
    d: float = Field(gt=0)
    """Durchmesser in mm."""

    def face(self, wp: cq.Workplane) -> cq.Face:
        return cq.Face.makeFromWires(wp.center(*self.center).circle(self.d / 2).val())


class SlotProfile(_Model):
    """Langloch mit zwei runden Enden in den Koordinaten (u, v) der Ebene."""

    type: Literal["slot"]
    center: Point2
    center_distance: float = Field(gt=0)
    """Abstand zwischen den Mittelpunkten der beiden Bögen in mm."""
    width: float = Field(gt=0)
    """Langlochbreite = Bogendurchmesser in mm."""
    angle: float = 0.0
    """Drehwinkel der Langlochachse ausgehend von +u in Grad."""

    def face(self, wp: cq.Workplane) -> cq.Face:
        # slot2D takes the overall length: arc centre distance plus one width
        wire = wp.center(*self.center).slot2D(self.center_distance + self.width, self.width, self.angle).val()
        return cq.Face.makeFromWires(wire)


class LineSegment(_Model):
    """Gerade vom aktuellen Punkt aus."""

    kind: Literal["line"]
    to: Point2

    def draw(self, wp: cq.Workplane) -> cq.Workplane:
        return wp.lineTo(*self.to)


class Arc3Segment(_Model):
    """Kreisbogen vom aktuellen Punkt über `via` bis `to`."""

    kind: Literal["arc3"]
    via: Point2
    """Ein beliebiger Punkt des Bogens zwischen Anfang und Ende."""
    to: Point2

    def draw(self, wp: cq.Workplane) -> cq.Workplane:
        return wp.threePointArc(self.via, self.to)


class RadiusArcSegment(_Model):
    """r > 0: Bogen im Uhrzeigersinn; r < 0: gegen den Uhrzeigersinn; höchstens 180 Grad. Bevorzuge arc3."""

    kind: Literal["radius_arc"]
    to: Point2
    r: float
    """Bogenradius in mm; muss mindestens halb so groß wie der Abstand zu `to` sein."""

    @field_validator("r")
    @classmethod
    def _nonzero(cls, r: float) -> float:
        if r == 0:
            raise ValueError("`r` darf nicht 0 sein.")
        return r

    def draw(self, wp: cq.Workplane) -> cq.Workplane:
        return wp.radiusArc(self.to, self.r)


Segment = Annotated[Union[LineSegment, Arc3Segment, RadiusArcSegment], Field(discriminator="kind")]


class CornerFillet(_Model):
    """Rundet eine Ecke eines Pfadprofils ab."""

    at: Point2
    """Position (u, v) der scharfen Ecke; gesucht innerhalb von CORNER_TOL (0,5 mm)."""
    r: float = Field(gt=0)
    """Rundungsradius in mm."""


class PathProfile(_Model):
    """Kontur, die bei `start` beginnt, den `segments` folgt und automatisch zu `start` geschlossen wird.

    Tangentiale Übergänge: Zeichne zuerst die scharfe Ecke und runde sie danach mit `fillets` ab.
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
            raise BuildError("Das Pfadprofil umschließt keine Fläche.")
        for fillet in self.fillets:
            corner = wp.plane.toWorldCoords(fillet.at)
            hits = [v for v in face.Vertices() if (v.Center() - corner).Length <= CORNER_TOL]
            if len(hits) != 1:
                raise BuildError(
                    f"Rundung bei {fillet.at}: {len(hits)} Ecken innerhalb von {CORNER_TOL} mm gefunden, benötigt wird genau 1.")
            face = _round_corners(face, hits, fillet.r)
        return face


class HullProfile(_Model):
    """Konvexe Hülle von Kreisen: Naben und Laschen werden durch gerade äußere Tangenten verbunden, ohne Rundungen."""

    type: Literal["hull"]
    circles: list[Point3] = Field(min_length=2)
    """Kreise als (u, v, r) in mm, jeweils mit r > 0. Ein Kreis innerhalb der Hülle der anderen trägt nichts bei."""

    @field_validator("circles")
    @classmethod
    def _positive_radius(cls, circles: list[Point3]) -> list[Point3]:
        if any(r <= 0 for _, _, r in circles):
            raise ValueError("Jeder Kreisradius muss > 0 sein.")
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
    """Gemeinsame Felder aller Vorgänge. Jeder implementiert apply(shape) -> (neue Form, Felder für den Aufbau-Bericht)."""

    refs: dict[str, str] = {}
    """Ordnet numerischen Parametern Maß-IDs zu, z. B. {"d": "d3", "profile.w": "d7", "positions.1.0": "d9"}.
    Die Schlüssel sind punktgetrennte Pfade im Vorgang; Listenindizes sind Zahlen."""

    @model_validator(mode="after")
    def _check_refs(self) -> Self:
        dumped = self.model_dump()
        for path in self.refs:
            try:
                value = get_path(dumped, path)
            except KeyError:
                raise ValueError(f"Der `refs`-Pfad {path!r} ist nicht vorhanden.") from None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Der `refs`-Pfad {path!r} verweist nicht auf eine Zahl.")
        return self


def _combine(shape: cq.Shape | None, tool: cq.Shape, mode: Mode) -> cq.Shape:
    if shape is None:
        if mode != "add":
            raise BuildError("Der erste Vorgang muss den Modus 'add' haben.")
        return tool
    if mode == "add":
        return shape.fuse(tool).clean()
    if mode == "cut":
        return shape.cut(tool).clean()
    if mode == "intersect":
        return shape.intersect(tool).clean()
    raise BuildError(f"Unbekannter Modus {mode!r}.")


def _no_effect(before: cq.Shape | None, after: cq.Shape, mode: Mode) -> dict:
    """Build-note fields warning about a cut or intersect that left the volume unchanged."""
    if before is not None and mode != "add" and before.Volume() - after.Volume() <= 1e-9 * before.Volume():
        return {"warning": NO_EFFECT}
    return {}


def _require(shape: cq.Shape | None) -> cq.Shape:
    if shape is None:
        raise BuildError("Ein Volumenkörper muss bereits vorhanden sein: Der erste Vorgang muss Material hinzufügen.")
    return shape


def _require_full_length(tool: cq.Solid, normal: cq.Vector, distance: float) -> None:
    """A draft can collapse the profile early, OCC then returns a shorter solid without complaint."""
    heights = [v.Center().dot(normal) for v in tool.Vertices()]
    length = max(heights) - min(heights)
    if abs(length - abs(distance)) > TAPER_LENGTH_TOL:
        raise BuildError(f"Verjüngung zu groß: Das Profil fällt nach {length:.2f} mm zusammen. "
                         f"Verringere `taper` oder `distance` (angefordert: {abs(distance):g} mm).")


class ExtrudeOp(OpBase):
    """Extrudiert ein auf einer Skizzenebene gezeichnetes Profil entlang der Ebenennormalen."""

    op: Literal["extrude"]
    profile: Profile
    plane: SketchPlane
    distance: float
    """Länge in mm entlang der Ebenennormalen; negative Werte verlaufen entgegen ihr."""
    mode: Mode = "add"
    """add: mit dem Bauteil vereinigen; cut: aus dem Bauteil entfernen; intersect: nur die gemeinsame Geometrie behalten."""
    both: bool = False
    """Symmetrisch zur Ebene extrudieren. `distance` ist dann die Gesamtdicke."""
    taper: float = Field(default=0.0, gt=-90, lt=90)
    """Verjüngungswinkel über die gesamte Länge in Grad; positive Werte verengen das Profil in Extrusionsrichtung.
    Entweder 0 oder mindestens 0,001 Grad. Bei `both=True` verläuft die Verjüngung von der Fläche bei
    -distance/2 zur Fläche bei +distance/2 und wird nicht an der Ebene gespiegelt. Eine Verjüngung, bei der
    das Profil vor dem Ende zusammenfällt, ist unzulässig."""

    @field_validator("distance")
    @classmethod
    def _nonzero(cls, distance: float) -> float:
        if distance == 0:
            raise ValueError("`distance` darf nicht 0 sein.")
        return distance

    @field_validator("taper")
    @classmethod
    def _usable_taper(cls, taper: float) -> float:
        if 0 < abs(taper) < MIN_TAPER:  # OCC crashes the interpreter on a draft this small
            raise ValueError(f"`taper` muss 0 oder mindestens {MIN_TAPER} Grad sein.")
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
    """Rotiert ein auf XZ gezeichnetes Profil (u = r, v = z) um die globale Z-Achse. Übergib points oder profile."""

    op: Literal["revolve"]
    points: list[Point2] | None = Field(default=None, min_length=3)
    """Polygon aus (r, z)-Punkten; wird automatisch geschlossen."""
    profile: PathProfile | None = None
    """Kontur aus Bögen und Rundungen in (r, z) statt aus Punkten."""
    angle: float = Field(default=360.0, gt=0, le=360)
    """Drehwinkel in Grad, gegen den Uhrzeigersinn von +Z aus gesehen."""
    mode: Mode = "add"
    """add: mit dem Bauteil vereinigen; cut: aus dem Bauteil entfernen; intersect: nur die gemeinsame Geometrie behalten."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.points is None) == (self.profile is None):
            raise ValueError("Gib genau eines der Felder `points` oder `profile` an.")
        if self.points is not None:
            radii = [r for r, _ in self.points]
        else:
            corners = [self.profile.start]
            for s in self.profile.segments:
                corners += [s.via, s.to] if isinstance(s, Arc3Segment) else [s.to]
            radii = [r for r, _ in corners]
        if min(radii) < 0:
            raise ValueError("Alle r-Werte (erste Koordinate) müssen >= 0 sein.")
        return self

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        profile = self.profile or PathProfile(
            type="path", start=self.points[0], segments=[LineSegment(kind="line", to=p) for p in self.points[1:]])
        face = profile.face(cq.Workplane("XZ"))
        if face.BoundingBox().xmin < -1e-6:
            raise BuildError("Das Profil kreuzt die Drehachse (r < 0): Prüfe Bogenpunkte und Vorzeichen von `radius_arc`.")
        tool = cq.Solid.revolve(face, self.angle, (0, 0, 0), (0, 0, 1))
        result = _combine(shape, tool, self.mode)
        return result, _no_effect(shape, result, self.mode)


class PolarPattern(_Model):
    """Bohrungen gleichmäßig auf einem Kreis verteilt."""

    center: Point2
    """Mittelpunkt des Kreises (u, v)."""
    pitch_d: float = Field(gt=0)
    """Teilkreisdurchmesser in mm."""
    count: int = Field(ge=1)
    start_angle: float = 0.0
    """Winkel der ersten Bohrung ausgehend von +u in Grad; die übrigen folgen in Abständen von 360/count."""


def _thread(spec: str) -> tuple[float, float]:
    """Nominal diameter and pitch of "M5" (coarse) or "M10x1"."""
    match = re.fullmatch(r"M(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?", spec)
    if match is None:
        raise ValueError(f"`thread` {spec!r} muss wie M5 oder M10x1 aussehen.")
    nominal = float(match[1])
    pitch = float(match[2]) if match[2] else COARSE_PITCH.get(nominal)
    if pitch is None:
        raise ValueError(f"Für `thread` {spec!r} ist keine Regelsteigung bekannt; gib M{match[1]}x<pitch> an.")
    return nominal, pitch


class HolesOp(OpBase):
    """Bohrungen, die von einer Skizzenebene entgegen ihrer Normalen ausgehen (mit flip: entlang der Normalen). Übergib positions oder polar."""

    op: Literal["holes"]
    plane: SketchPlane
    """Ebene, auf der die Bohrungen beginnen, üblicherweise die Eintrittsfläche."""
    positions: list[Point2] = []
    """Mittelpunkte der Bohrungen (u, v) in der Ebene."""
    polar: PolarPattern | None = None
    """Bohrungen auf einem Kreis statt mit `positions` angegeben."""
    d: float = Field(gt=0)
    """Bohrungsdurchmesser in mm. Bei `kind=tapped`: Gewinde-Nenndurchmesser."""
    depth: float | None = Field(default=None, gt=0)
    """Bohrtiefe in mm; null bohrt durch das Bauteil."""
    kind: Literal["plain", "cbore", "csk", "tapped"] = "plain"
    """cbore: zylindrische Senkung, csk: kegelige Senkung, tapped: Gewindebohrung."""
    cbore_d: float | None = Field(default=None, gt=0)
    """Durchmesser der zylindrischen Senkung in mm."""
    cbore_depth: float | None = Field(default=None, gt=0)
    """Tiefe der zylindrischen Senkung in mm."""
    csk_d: float | None = Field(default=None, gt=0)
    """Durchmesser der kegeligen Senkung an der Oberfläche in mm."""
    csk_angle: float = Field(default=90.0, gt=0, lt=180)
    """Eingeschlossener Winkel der kegeligen Senkung in Grad."""
    thread: str | None = None
    """Nur bei `kind=tapped`: M5 oder M10x1. Wird als einfache Bohrung ohne Gewindegeometrie modelliert."""
    thread_core: Literal["tap_drill", "minor"] = "tap_drill"
    """Bohrungsdurchmesser für ein Gewinde: tap_drill (M5: 4.2) oder minor, der Kerndurchmesser des Gewindes (M5: 4.134)."""
    flip: bool = False
    """Entlang der Ebenennormalen statt entgegen ihr bohren."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if bool(self.positions) == (self.polar is not None):
            raise ValueError("Gib genau eines von `positions` (nicht leer) oder `polar` an.")
        if (self.kind == "tapped") != (self.thread is not None):
            raise ValueError("`thread` ist für `kind='tapped'` erforderlich und nur dort zulässig.")
        if self.thread is not None and abs(_thread(self.thread)[0] - self.d) > 1e-9:
            raise ValueError(f"`d` ({self.d}) muss dem Nenndurchmesser des Gewindes {self.thread} entsprechen.")
        if (self.kind != "cbore" and (self.cbore_d is not None or self.cbore_depth is not None)) or (
                self.kind != "csk" and self.csk_d is not None):
            raise ValueError("`cbore_d`, `cbore_depth` und `csk_d` sind nur bei `kind='cbore'` oder `kind='csk'` zulässig.")
        if self.kind == "cbore":
            if self.cbore_d is None or self.cbore_depth is None:
                raise ValueError("`kind='cbore'` benötigt `cbore_d` und `cbore_depth`.")
            if self.cbore_d <= self.d:
                raise ValueError("`cbore_d` muss größer als `d` sein.")
            deepest = self.cbore_depth
        elif self.kind == "csk":
            if self.csk_d is None:
                raise ValueError("`kind='csk'` benötigt `csk_d`.")
            if self.csk_d <= self.d:
                raise ValueError("`csk_d` muss größer als `d` sein.")
            deepest = self._csk_depth()
        else:
            return self
        if self.depth is not None and deepest >= self.depth:
            raise ValueError("Die Senkung muss flacher als `depth` sein.")
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
    """Versieht jede Kante innerhalb von tol zu einem near-Punkt mit einer Rundung oder Fase. Nahtkanten runder Flächen werden übersprungen."""

    op: Literal["edge_finish"]
    kind: Literal["fillet", "chamfer"]
    size: float = Field(gt=0)
    """Rundungsradius oder Fasenlänge in mm."""
    near: list[Point3] = Field(min_length=1)
    """Globale Punkte (x, y, z) auf oder nahe den Kanten, zum Beispiel deren Mittelpunkte."""
    tol: float = Field(default=0.5, gt=0)
    """Größter Abstand in mm zwischen einer Kante und einem nahen Punkt."""

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        shape = _require(shape)
        points = [cq.Vertex.makeVertex(*p) for p in self.near]
        edges = [e for e in shape.Edges() if min(e.distance(p) for p in points) <= self.tol and not _is_seam(e, shape)]
        if not edges:
            raise BuildError("Keine Kante liegt innerhalb von `tol` zu den `near`-Punkten.")
        # the edges come from this very solid: edges of another instance make the fillet fail
        selected = cq.Workplane(obj=shape).newObject(edges)
        try:
            result = selected.fillet(self.size) if self.kind == "fillet" else selected.chamfer(self.size)
        except StdFail_NotDone:
            raise BuildError(f"{self.kind} mit {self.size} mm ist an {len(edges)} Kante(n) fehlgeschlagen: "
                             "Ist die Größe für die angrenzenden Flächen zu groß?") from None
        return result.val(), {"edges_selected": len(edges)}


class ScriptOp(OpBase):
    """Ausweichlösung: Python-Code mit `result` (dem bisherigen cq.Workplane), `cq`, `math` und einigen eingebauten Funktionen.

    Eingebaute Funktionen: abs, min, max, range, len, float, int, round, sum, enumerate, zip, list, tuple, dict, print.
    Keine Imports und keine Namen mit führendem "_" (auch nicht `_` als Schleifenvariable).
    `result` muss am Ende ein cq.Workplane mit dem Volumenkörper enthalten.
    """

    op: Literal["script"]
    code: str

    def apply(self, shape: cq.Shape | None) -> tuple[cq.Shape, dict]:
        wp = cq.Workplane("XY") if shape is None else cq.Workplane(obj=shape)
        scope = {"__builtins__": dict(SCRIPT_BUILTINS), "cq": cq, "math": math, "result": wp}
        exec(compile(self.code, "<script>", "exec"), scope)
        if not isinstance(scope["result"], cq.Workplane):
            raise BuildError("Das `script` muss ein cq.Workplane in `result` hinterlassen.")
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
        raise BuildError("Keine Vorgänge angegeben.")
    return cq.Workplane(obj=shape), notes

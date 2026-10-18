"""Prüfungen nach jedem Aufbau: gültiger Festkörper sowie Maße und Bohrungen.

Die Abnahmeschwellen LINEAR_TOL und AXIS_COS stehen in d2c/spec.py; der STEP-Rückimport liegt in d2c/cad/export.py."""

from collections.abc import Sequence

import cadquery as cq
import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus
from OCP.gp import gp_Cylinder, gp_Dir, gp_Lin, gp_Pnt
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds

from d2c.spec import AXIS_COS, LINEAR_TOL, Expected, ExpectedHole

DIM_TOL = 0.05  # mm: so weit darf ein gedrucktes Maß vom nächsten Maß im Körper abweichen (dims_found)
DIM_KINDS = ("length", "diameter", "radius")  # Maßarten, die dims_found im Körper sucht; Winkel und Zählmaße nicht
NEAR_TOL = 0.5  # mm: so nah muss ein Markierungspunkt an einer Fläche liegen, damit select_faces sie wählt
MAX_AXES = 200  # Zylinderachsen, bis zu denen position_distances alle Paare bildet (ponytail: O(n²); darüber nur Achse zu Ebene)
RAY_LIMIT = 1e6  # mm: halbe Länge des Strahls, mit dem der Durchgangstest einer Bohrung den Körper schneidet

Point = tuple[float, float, float]


def _bbox(shape: cq.Shape) -> cq.BoundBox:
    """Begrenzungsrahmen der exakten Geometrie.

    Shape.BoundingBox() berücksichtigt auch ein am Körper verbliebenes Netz (zum Beispiel nach GLB-Export) und
    überschätzt gekrümmte Teile dann um bis zur Netzabweichung.
    """
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    return cq.BoundBox(box)


def _free_edges(shape: cq.Shape) -> int:
    """Kanten, die nur an eine Fläche grenzen, also Lücken in der Hülle des Körpers.

    Der Konstruktor ohne Toleranz findet auch den Rand einer einzelnen Fläche.
    """
    bounds = ShapeAnalysis_FreeBounds(shape.wrapped, False, True)
    wires = (bounds.GetClosedWires(), bounds.GetOpenWires())
    return sum(len(cq.Shape.cast(w).Edges()) for w in wires if not w.IsNull())


def _is_concave(face: cq.Face, cylinder: gp_Cylinder) -> bool:
    """Gibt an, ob das Material außerhalb der zylindrischen Fläche liegt, wie bei einer Bohrung.

    Die orientierte Normale einer Bohrungsfläche weist zur Achse; bei einem Zapfen oder einer konvexen Rundung weist
    sie von der Achse weg.
    """
    axis = cylinder.Axis()
    direction = cq.Vector(axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z())
    # Mitte des eigenen Parameterbereichs der Fläche: Eine beschnittene Fläche (STEP-Leser erzeugen sie) hat außerhalb davon keinen Punkt.
    u0, u1, v0, v1 = face.uvBounds()
    point = face.positionAt((u0 + u1) / 2, (v0 + v1) / 2)
    offset = point - cq.Vector(axis.Location().X(), axis.Location().Y(), axis.Location().Z())
    radial = offset - direction * offset.dot(direction)
    return face.normalAt().dot(radial) < 0  # ohne Lage: die Normale in derselben Mitte


def _cylinders(shape: cq.Shape) -> list[tuple[gp_Cylinder, bool, tuple[float, float]]]:
    """Alle zylindrischen Flächen des Körpers als Zylinder in globalen Koordinaten mit Konkavität und Achsbereich.

    Der Achsbereich (v0, v1) ist der Parameterbereich der Fläche entlang der Zylinderachse ab deren Ursprung.
    """
    found = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            _, _, v0, v1 = face.uvBounds()
            found.append((cylinder, _is_concave(face, cylinder), (v0, v1)))
    return found


def solid_check(wp: cq.Workplane) -> dict:
    """Das Modell ist ein gültiger, geschlossener Festkörper mit positivem Volumen."""
    shape = wp.val()
    box = _bbox(shape)
    valid, solids = shape.isValid(), len(shape.Solids())
    free_edges, volume = _free_edges(shape), shape.Volume()
    return {
        "valid": valid,
        "solids": solids,
        "free_edges": free_edges,
        "volume": volume,
        "bbox": [box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax],
        "ok": valid and solids == 1 and free_edges == 0 and volume > 0,
    }


def _axis_hits(inter: IntCurvesFace_ShapeIntersector, hole: ExpectedHole) -> list[float]:
    """Die Schnittpunkte der Geraden durch hole.at in Richtung hole.axis mit dem Körper als Strecken in mm ab hole.at, sortiert.

    Eine Bohrung, die den Körper ganz durchläuft, hat auf ihrer Achse keinen Schnittpunkt.
    """
    inter.Perform(gp_Lin(gp_Pnt(*hole.at), gp_Dir(*hole.axis)), -RAY_LIMIT, RAY_LIMIT)
    return sorted(inter.WParameter(k) for k in range(1, inter.NbPnt() + 1))  # gp_Dir ist normiert: Parameter in mm


def _has_entry(hits: list[float], cylinders: list[dict], hole: ExpectedHole) -> bool:
    """Gibt an, ob die Bohrung von außen erreichbar ist: Liegt die Mitte mindestens einer ihrer Zylinderflächen auf der
    Achse zwischen zwei Schnittpunkten mit dem Körper, ist sie ein geschlossener Hohlraum."""
    unit = np.array(hole.axis) / np.linalg.norm(hole.axis)
    for c in cylinders:
        mid = np.array(c["origin"]) + np.array(c["direction"]) * sum(c["span"]) / 2
        t = float(np.dot(mid - np.array(hole.at), unit))
        if not (any(h < t - LINEAR_TOL for h in hits) and any(h > t + LINEAR_TOL for h in hits)):
            return True
    return False


def fits_hole(cylinder: gp_Cylinder, diameter: float, point: gp_Pnt, direction: gp_Dir, tol: float) -> bool:
    """Der Zylinder hat den Durchmesser `diameter` (Abweichung höchstens tol), seine Achse liegt höchstens tol von `point`
    und ist parallel zu `direction`."""
    return (
        abs(2 * cylinder.Radius() - diameter) <= tol
        and gp_Lin(cylinder.Axis()).Distance(point) <= tol
        and abs(cylinder.Axis().Direction().Dot(direction)) >= AXIS_COS
    )


def _check_hole(
    cylinders: list[tuple[gp_Cylinder, bool, tuple[float, float]]],
    hole: ExpectedHole,
    tol: float,
    inter: IntCurvesFace_ShapeIntersector,
) -> dict:
    """Der Eintrag von dimension_check für eine erwartete Bohrung.

    Ein Zylinder ist die Bohrung, wenn er konkav ist, sein Durchmesser und der Abstand von hole.at zu seiner Achse
    innerhalb von tol liegen und seine Achse parallel ist. `actual` ist dieser Zylinder, sonst der (konkave oder
    nicht konkave), dessen Achse am nächsten an hole.at liegt; None, wenn der Körper keine zylindrische Fläche hat.

    through: Die Achse durch hole.at trifft kein Material, die Bohrung läuft also durch den ganzen Körper (None, wenn
    kein Zylinder die Bohrung ist). Eine Sacklochbohrung und eine Bohrung, die ein Steg verschließt, sind beide False.
    entry: Die Bohrung ist auf mindestens einer Seite nach außen offen; ohne entry ist ok False (ein geschlossener
    Hohlraum ist keine Bohrung).
    """
    point, direction = gp_Pnt(*hole.at), gp_Dir(*hole.axis)
    found = [
        {
            "d": 2 * c.Radius(),
            "dist": gp_Lin(c.Axis()).Distance(point),
            "fits": concave and fits_hole(c, hole.d, point, direction, tol),
            "concave": concave,
            "origin": c.Axis().Location().Coord(),
            "direction": c.Axis().Direction().Coord(),
            "span": span,
        }
        for c, concave, span in cylinders
    ]
    hits = [f for f in found if f["fits"]]
    nearest = min(hits or found, key=lambda f: f["dist"], default=None)
    through = entry = None
    if hits:
        along = _axis_hits(inter, hole)
        through, entry = not along, _has_entry(along, hits, hole)
    return {
        "check": "hole",
        "expected": hole.model_dump(exclude={"dim_ids"}),
        "actual": ({"d": nearest["d"], "dist": nearest["dist"], "concave": nearest["concave"]} if nearest else None),
        "ok": bool(hits) and entry is not False,
        "through": through,
        "entry": entry,
        "dim_ids": hole.dim_ids,
    }


def dimension_check(wp: cq.Workplane, expected: Expected, tol: float = LINEAR_TOL) -> list[dict]:
    """Gesamtgröße und jede erwartete Bohrung gegen das Modell, ein Eintrag je Prüfung.

    Die Größe vergleicht die Kantenlängen des Begrenzungsrahmens in Modellkoordinaten. tol (mm) ist die zulässige
    Abweichung von Größe, Bohrungsdurchmesser und Bohrungsposition; ein auf der Toleranzmitte gebautes Modell braucht mehr.
    Verglichen wird das Modell mit `expected`, das der Agent selbst geschrieben hat: Das zeigt, dass das Modell zur
    erfassten Spezifikation passt, nicht dass es der Zeichnung entspricht (dafür dims_found zu den gedruckten Maßen).
    """
    shape = wp.val()
    box = _bbox(shape)
    sizes = (box.xlen, box.ylen, box.zlen)
    checks = [
        {
            "check": f"bbox_{axis}",
            "expected": want,
            "actual": got,
            "ok": abs(got - want) <= tol,
            "dim_ids": [],
        }
        for axis, want, got in zip("xyz", expected.bbox, sizes)
    ]
    cylinders = _cylinders(shape)
    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(shape.wrapped, 1e-6)  # einmal je Prüfung: der Aufbau der Suchstruktur kostet mehr als jeder Strahl
    return checks + [_check_hole(cylinders, hole, tol, inter) for hole in expected.holes]


def _vector(point) -> np.ndarray:
    return np.array([point.X(), point.Y(), point.Z()])


def _axes_and_planes(shape: cq.Shape) -> tuple[np.ndarray, np.ndarray]:
    """Die verschiedenen Zylinderachsen als Zeilen (Richtung, Punkt) und Ebenen als Zeilen (Normale, Punkt) des Körpers.

    Richtung und Normale zeigen in eine Halbebene (erste von null verschiedene Komponente positiv). Der Punkt ist bei
    der Achse ihr Punkt, der dem Ursprung am nächsten liegt, bei der Ebene der Fußpunkt des Ursprungs auf ihr; so haben
    Gegenflächen und die beiden Halbzylinder einer Bohrung genau eine Zeile.
    """
    axes, planes = [], []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        kind = surface.GetType()
        if kind == GeomAbs_Cylinder:
            axis = surface.Cylinder().Axis()
        elif kind == GeomAbs_Plane:
            axis = surface.Plane().Axis()
        else:
            continue
        direction, point = _vector(axis.Direction()), _vector(axis.Location())
        if direction[np.flatnonzero(np.abs(direction) > 1e-9)[0]] < 0:
            direction = -direction
        along = direction * (direction @ point)
        if kind == GeomAbs_Cylinder:
            axes.append(np.concatenate([direction, point - along]))
        else:
            planes.append(np.concatenate([direction, along]))
    return tuple(np.unique(np.round(rows, 6), axis=0) if rows else np.empty((0, 6)) for rows in (axes, planes))


def position_distances(shape: cq.Shape) -> np.ndarray:
    """Lagemaße der Zylinderachsen (Bohrungen, Gewindekernlöcher, Langlochbögen, Zapfen) im Körper in mm, unsortiert.

    Das sind der Abstand einer Achse von einer zu ihr parallelen Ebene (zum Beispiel die Bohrungsmitte von der
    Plattenkante) sowie bei zwei parallelen Achsen ihr Abstand und dessen Anteile in X-, Y- und Z-Richtung, also der
    Abstand von Bohrungsmitte zu Bohrungsmitte entlang einer Hauptrichtung. Ebene und Achse müssen parallel sein, die
    Ebene kann beliebig geneigt liegen; die Anteile gelten nur für die Hauptrichtungen des Koordinatensystems.
    Mit mehr als MAX_AXES verschiedenen Achsen entfallen die Achspaare.
    """
    axes, planes = _axes_and_planes(shape)
    result = []
    if 1 < len(axes) <= MAX_AXES:  # ponytail: alle Paare, O(n²); bei mehr Achsen nur Achse zu Ebene, sonst Paare nach Nachbarschaft bilden
        directions, points = axes[:, :3], axes[:, 3:]
        offset = points[None, :, :] - points[:, None, :]
        across = offset - np.einsum("ijk,ik->ij", offset, directions)[:, :, None] * directions[:, None, :]
        parallel = np.abs(directions @ directions.T) >= AXIS_COS
        result += [np.linalg.norm(across, axis=2)[parallel], np.abs(across)[parallel].ravel()]
    if len(axes) and len(planes):
        normals, origins = planes[:, :3], planes[:, 3:]
        beside = np.abs(axes[:, :3] @ normals.T) < 1e-4  # Achse liegt parallel zur Ebene
        distance = np.abs(axes[:, 3:] @ normals.T - np.einsum("jk,jk->j", normals, origins)[None, :])
        result.append(distance[beside])
    found = np.concatenate(result) if result else np.empty(0)
    return found[found > LINEAR_TOL]  # 0 sind Achse zu sich selbst und Anteile in Achsrichtung


def dimension_candidates(shape: cq.Shape) -> np.ndarray:
    """Alle Längen im fertigen Körper, die ein gedrucktes Maß sein können, sortiert und ohne Dubletten.

    Das sind Radien und Durchmesser der Zylinder, Kugeln, Torus-Querschnitte und Kreiskanten, die Ausdehnungen
    des Hüllquaders, die Länge der Geraden und Kreiskanten, der Abstand paralleler Ebenen und die Lagemaße von
    Zylinderachsen (siehe position_distances).
    """
    values: list[float] = []
    planes: list[tuple[np.ndarray, np.ndarray]] = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        kind = surface.GetType()
        if kind == GeomAbs_Plane:
            axis = surface.Plane().Axis()
            planes.append((_vector(axis.Direction()), _vector(axis.Location())))
        elif kind == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            values += [cylinder.Radius(), 2 * cylinder.Radius()]
        elif kind == GeomAbs_Sphere:
            radius = surface.Sphere().Radius()
            values += [radius, 2 * radius]
        elif kind == GeomAbs_Torus:
            radius = surface.Torus().MinorRadius()
            values += [radius, 2 * radius]
    box = _bbox(shape)
    values += [box.xlen, box.ylen, box.zlen]
    for edge in shape.Edges():
        curve = BRepAdaptor_Curve(edge.wrapped)
        span = curve.LastParameter() - curve.FirstParameter()
        if curve.GetType() == GeomAbs_Line:
            values.append(span)
        elif curve.GetType() == GeomAbs_Circle:
            radius = curve.Circle().Radius()
            values += [radius, 2 * radius, radius * span]
    result = [np.array(values)]
    if len(planes) > 1:
        normals, origins = (np.array(x) for x in zip(*planes))
        along = normals @ origins.T  # along[i, j]: Lage der Ebene j auf der Normalen der Ebene i
        gaps = np.abs(along - np.diag(along)[:, None])
        parallel = np.abs(normals @ normals.T) >= AXIS_COS
        result.append(gaps[parallel])
    result.append(position_distances(shape))
    found = np.unique(np.concatenate(result))
    return found[found > LINEAR_TOL]  # 0 ist der Abstand einer Ebene zu sich selbst und kein Maß


def dims_found(shape: cq.Shape, dimensions: Sequence[dict], tol: float = DIM_TOL) -> dict:
    """Sucht jedes nicht angenommene Längen-, Durchmesser- und Radiusmaß im fertigen Körper.

    dimensions sind {"id", "kind", "value", "text", "assumed"}. Ein Maß gilt als gefunden, wenn eine Länge des Körpers
    (siehe dimension_candidates) höchstens tol (mm) davon abweicht. Winkel, Zählmaße, Fasen, Gewinde und angenommene
    Maße werden nicht gesucht. Ein fehlendes Maß ist ein Hinweis und kein Fehler: Während des Aufbaus fehlen
    Merkmale. Die Kandidatenliste wird einmal je Aufruf berechnet.

    Rückgabe {"found": [{"id", "text", "value", "nearest"}], "missing": [{"id", "text", "value"}]}; nearest ist die
    gefundene Länge des Körpers.
    """
    wanted = [d for d in dimensions if d["kind"] in DIM_KINDS and not d.get("assumed")]
    candidates = dimension_candidates(shape)
    found, missing = [], []
    for d in wanted:
        k = int(np.searchsorted(candidates, d["value"]))
        near = candidates[max(k - 1, 0) : k + 1]
        nearest = float(near[np.argmin(np.abs(near - d["value"]))]) if len(near) else None
        entry = {"id": d["id"], "text": d["text"], "value": d["value"]}
        if nearest is not None and abs(nearest - d["value"]) <= tol:
            found.append(entry | {"nearest": nearest})
        else:
            missing.append(entry)
    return {"found": found, "missing": missing}


def circle_diameters(shape: cq.Shape) -> list[float]:
    """Die Durchmesser aller runden Merkmale des Körpers, auf 0,01 mm gerundet, sortiert und ohne Dubletten.

    Das sind Zylinder, Kugeln, Torus-Querschnitte und alle Kreiskanten (auch die Ränder von Kegeln und Bögen von Konturen):
    das Gegenstück zu den Kreisen, die measure_drawing in einer Ansicht misst.
    """
    values = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        kind = surface.GetType()
        if kind == GeomAbs_Cylinder:
            values.append(surface.Cylinder().Radius())
        elif kind == GeomAbs_Sphere:
            values.append(surface.Sphere().Radius())
        elif kind == GeomAbs_Torus:
            values.append(surface.Torus().MinorRadius())
    for edge in shape.Edges():
        curve = BRepAdaptor_Curve(edge.wrapped)
        if curve.GetType() == GeomAbs_Circle:
            values.append(curve.Circle().Radius())
    return sorted({round(2 * r, 2) for r in values})


def select_faces(shape: cq.Shape, near: Sequence[Point]) -> list[cq.Face]:
    """Die Flächen des Körpers, die höchstens NEAR_TOL von einem der Punkte entfernt liegen."""
    points = [cq.Vertex.makeVertex(*p) for p in near]
    return [face for face in shape.Faces() if any(face.distance(p) <= NEAR_TOL for p in points)]


def metrics(shape: cq.Shape) -> dict:
    """Kennwerte, die unabhängig von der Lage des Bauteils im Raum sind und den Vergleich mit einem Referenzmodell ermöglichen."""
    box = _bbox(shape)
    return {
        "volume": shape.Volume(),
        "bbox_sorted": sorted((box.xlen, box.ylen, box.zlen)),
        "cyl_diameters": sorted({round(2 * c.Radius(), 2) for c, _, _ in _cylinders(shape)}),
    }

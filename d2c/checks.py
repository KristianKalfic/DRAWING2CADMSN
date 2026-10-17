"""Prüfungen nach jedem Aufbau: gültiger Festkörper (R8a), Maße und Bohrungen (R8b), STEP-Export mit Rückimport (R8c).

Die projektweiten Toleranzen betragen linear 0,01 mm, |cos| >= 0,9999 für parallele Achsen und eine relative
Volumenänderung von höchstens 1e-6 nach STEP-Export und -Rückimport."""

from collections.abc import Sequence
from pathlib import Path

import cadquery as cq
from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.gp import gp_Cylinder, gp_Dir, gp_Lin, gp_Pnt
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds

from d2c.spec import Expected, ExpectedHole

LINEAR_TOL = 0.01  # mm
AXIS_COS = 0.9999  # Kleinster |cos|-Wert des Winkels zwischen zwei Achsen, damit sie als parallel gelten
VOLUME_REL_TOL = 1e-6  # Größte relative Volumenänderung beim STEP-Export mit Rückimport

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
    point = face.positionAt(0.5, 0.5)
    offset = point - cq.Vector(axis.Location().X(), axis.Location().Y(), axis.Location().Z())
    radial = offset - direction * offset.dot(direction)
    return face.normalAt(point).dot(radial) < 0


def _cylinders(shape: cq.Shape) -> list[tuple[gp_Cylinder, bool]]:
    """Alle zylindrischen Flächen des Körpers als Zylinder in globalen Koordinaten, jeweils mit ihrer Konkavität."""
    found = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            found.append((cylinder, _is_concave(face, cylinder)))
    return found


def solid_check(wp: cq.Workplane) -> dict:
    """R8a: Das Modell ist ein gültiger, geschlossener Festkörper mit positivem Volumen."""
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


def _check_hole(cylinders: list[tuple[gp_Cylinder, bool]], hole: ExpectedHole) -> dict:
    """Ein Eintrag von dimension_check für eine erwartete Bohrung.

        Ein Zylinder gilt als Bohrung, wenn er konkav ist und sein Durchmesser, der Abstand von hole.at zu seiner Achse
        sowie die Achsrichtung innerhalb der Toleranz liegen. actual ist dieser Zylinder oder andernfalls derjenige
        (konkav oder nicht), dessen Achse am nächsten an hole.at verläuft; None, wenn der Körper keine Zylinderfläche hat.
    """
    point, direction = gp_Pnt(*hole.at), gp_Dir(*hole.axis)
    found = [
        {
            "d": 2 * c.Radius(),
            "dist": gp_Lin(c.Axis()).Distance(point),
            "cos": abs(c.Axis().Direction().Dot(direction)),
            "concave": concave,
        }
        for c, concave in cylinders
    ]
    hits = [
        f
        for f in found
        if f["concave"]
        and abs(f["d"] - hole.d) <= LINEAR_TOL
        and f["dist"] <= LINEAR_TOL
        and f["cos"] >= AXIS_COS
    ]
    nearest = min(hits or found, key=lambda f: f["dist"], default=None)
    return {
        "check": "hole",
        "expected": hole.model_dump(exclude={"dim_ids"}),
        "actual": (
            {"d": nearest["d"], "dist": nearest["dist"], "concave": nearest["concave"]} if nearest else None
        ),
        "ok": bool(hits),
        "dim_ids": hole.dim_ids,
    }


def dimension_check(wp: cq.Workplane, expected: Expected) -> list[dict]:
    """Vergleicht die Gesamtmaße und jede erwartete Bohrung mit dem Modell; ein Eintrag je Prüfung.

        Bei den Maßen werden die Kantenlängen des Begrenzungsrahmens in Modellkoordinaten verglichen.
    """
    shape = wp.val()
    box = _bbox(shape)
    sizes = (box.xlen, box.ylen, box.zlen)
    checks = [
        {
            "check": f"bbox_{axis}",
            "expected": want,
            "actual": got,
            "ok": abs(got - want) <= LINEAR_TOL,
            "dim_ids": [],
        }
        for axis, want, got in zip("xyz", expected.bbox, sizes)
    ]
    cylinders = _cylinders(shape)
    return checks + [_check_hole(cylinders, hole) for hole in expected.holes]


def select_faces(shape: cq.Shape, near: Sequence[Point], tol: float = 0.5) -> list[cq.Face]:
    """Die Flächen des Körpers, die höchstens tol von einem der Punkte entfernt liegen."""
    points = [cq.Vertex.makeVertex(*p) for p in near]
    return [face for face in shape.Faces() if any(face.distance(p) <= tol for p in points)]


def _export_step(shape: cq.Shape, path: str, marked: Sequence[tuple[str, Sequence[cq.Face]]]) -> None:
    """Schreibt den Körper als STEP mit stahlfarbenem Grundkörper und orangefarbenen, nach ihrer Markierung benannten Flächen."""
    asm = cq.Assembly(shape, name=Path(path).stem, color=cq.Color(0.6, 0.67, 0.69))
    for mark_id, faces in marked:
        for face in faces:
            asm.addSubshape(face, name=mark_id, color=cq.Color(1.0, 0.5, 0.0))
    asm.export(path, exportType="STEP")  # Explizit: Ein Name mit *.stp würde nicht erkannt


def step_roundtrip(wp: cq.Workplane, path: str, marks: Sequence[dict] = ()) -> dict:
    """Schreibt das Modell als STEP, liest es beim Rückimport wieder ein und vergleicht das Ergebnis.

        marks hat die Form {"id": str, "near": [(x, y, z), ...]}: Die Flächen in der Nähe der Punkte einer Markierung
        werden eingefärbt und nach ihrer id benannt. Findet eine Markierung keine Fläche, ist das Ergebnis nicht ok.
    """
    shape = wp.val()
    selected = [(m["id"], select_faces(shape, m["near"])) for m in marks]
    _export_step(shape, path, selected)

    back = cq.Compound.makeCompound(cq.importers.importStep(path).vals())
    valid, solids = back.isValid(), len(back.Solids())
    volume = shape.Volume()
    volume_rel_diff = abs(back.Volume() - volume) / abs(volume)
    mark_report = [{"id": mark_id, "faces": len(faces)} for mark_id, faces in selected]
    ok = valid and solids == 1 and volume_rel_diff <= VOLUME_REL_TOL
    return {
        "path": path,
        "reimport_valid": valid,
        "solids": solids,
        "volume_rel_diff": volume_rel_diff,
        "marks": mark_report,
        "ok": ok and all(m["faces"] > 0 for m in mark_report),
    }


def metrics(shape: cq.Shape) -> dict:
    """Kennwerte, die unabhängig von der Lage des Bauteils im Raum sind und den Vergleich mit einem Referenzmodell ermöglichen."""
    box = _bbox(shape)
    return {
        "volume": shape.Volume(),
        "bbox_sorted": sorted((box.xlen, box.ylen, box.zlen)),
        "cyl_diameters": sorted({round(2 * c.Radius(), 2) for c, _ in _cylinders(shape)}),
    }

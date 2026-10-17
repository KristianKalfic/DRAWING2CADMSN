"""Checks run after every build: valid solid (R8a), size and holes (R8b), STEP round trip (R8c).

The tolerances are the project-wide ones: 0.01 mm linear, |cos| >= 0.9999 for parallel axes and
1e-6 relative volume change through a STEP export and re-import.
"""

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
AXIS_COS = 0.9999  # smallest |cos| of the angle between two axes that count as parallel
VOLUME_REL_TOL = 1e-6  # largest relative volume change through the STEP round trip

Point = tuple[float, float, float]


def _bbox(shape: cq.Shape) -> cq.BoundBox:
    """Bounding box of the exact geometry.

    Shape.BoundingBox() also reads a mesh left on the shape (by a GLB export, say) and then
    overshoots curved parts by up to the mesh deflection.
    """
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    return cq.BoundBox(box)


def _free_edges(shape: cq.Shape) -> int:
    """Edges that border only one face, i.e. the gaps in the skin of the shape.

    The constructor without a tolerance is the one that also finds the boundary of a single face.
    """
    bounds = ShapeAnalysis_FreeBounds(shape.wrapped, False, True)
    wires = (bounds.GetClosedWires(), bounds.GetOpenWires())
    return sum(len(cq.Shape.cast(w).Edges()) for w in wires if not w.IsNull())


def _is_concave(face: cq.Face, cylinder: gp_Cylinder) -> bool:
    """Whether the material of the cylindrical face lies outside, as around a hole.

    The oriented normal of a hole surface points to the axis, that of a pin or convex round away from it.
    """
    axis = cylinder.Axis()
    direction = cq.Vector(axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z())
    point = face.positionAt(0.5, 0.5)
    offset = point - cq.Vector(axis.Location().X(), axis.Location().Y(), axis.Location().Z())
    radial = offset - direction * offset.dot(direction)
    return face.normalAt(point).dot(radial) < 0


def _cylinders(shape: cq.Shape) -> list[tuple[gp_Cylinder, bool]]:
    """Every cylindrical face of the shape as a cylinder in global coordinates, with its concavity."""
    found = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            found.append((cylinder, _is_concave(face, cylinder)))
    return found


def solid_check(wp: cq.Workplane) -> dict:
    """R8a: the model is one valid closed solid with positive volume."""
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
    """The dimension_check entry of one expected hole.

    A cylinder is the hole if it is concave and its diameter, the distance of hole.at from its axis
    and the direction of its axis are all within tolerance. `actual` is that cylinder, or else the
    one (concave or not) whose axis runs closest to hole.at; None if the shape has no cylindrical face.
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
    """R8b: overall size and every expected hole against the model, one entry per check.

    The size compares the edge lengths of the bounding box in model coordinates.
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
    """The faces of the shape that lie within tol of any of the points."""
    points = [cq.Vertex.makeVertex(*p) for p in near]
    return [face for face in shape.Faces() if any(face.distance(p) <= tol for p in points)]


def _export_step(shape: cq.Shape, path: str, marked: Sequence[tuple[str, Sequence[cq.Face]]]) -> None:
    """Write the shape as STEP with a steel-colored body and orange faces named after their mark."""
    asm = cq.Assembly(shape, name=Path(path).stem, color=cq.Color(0.6, 0.67, 0.69))
    for mark_id, faces in marked:
        for face in faces:
            asm.addSubshape(face, name=mark_id, color=cq.Color(1.0, 0.5, 0.0))
    asm.export(path, exportType="STEP")  # explicit: a *.stp name would not be recognized


def step_roundtrip(wp: cq.Workplane, path: str, marks: Sequence[dict] = ()) -> dict:
    """R8c: write the model as STEP, read it back and compare.

    marks are {"id": str, "near": [(x, y, z), ...]}: the faces near a mark's points are colored and
    named after its id. A mark that finds no face makes the result not ok.
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
    """Figures that do not depend on where the part sits in space, to compare a model with a reference."""
    box = _bbox(shape)
    return {
        "volume": shape.Volume(),
        "bbox_sorted": sorted((box.xlen, box.ylen, box.zlen)),
        "cyl_diameters": sorted({round(2 * c.Radius(), 2) for c, _ in _cylinders(shape)}),
    }

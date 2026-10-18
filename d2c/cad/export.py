"""Export für CAD-Systeme und die Webansicht: STEP AP242 mit semantischer PMI und Prüfmarkierungen, GLB und einem PMI-Leser.

Eine Prüfmarkierung färbt die Flächen nahe ihren Punkten orange, benennt sie "CHECK <id>" und legt sie
auf den Layer ASSUMED_CHECK. Gewindebohrungen werden grün. Maße werden als
semantische PMI abgelegt: DIMENSIONAL_SIZE für einen Durchmesser, DIMENSIONAL_LOCATION für den Abstand
zwischen zwei Flächen. Form- und Lagetoleranzen werden als GEOMETRIC_TOLERANCE mit ihren Bezügen (DATUM,
DATUM_FEATURE, DATUM_SYSTEM) geschrieben. Zeichen in der Datei werden in ASCII umgesetzt, da OCCT andere Zeichen roh als
UTF-8 schreibt.
"""

import json
import re
import struct
import unicodedata
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import cadquery as cq
import OCP.XCAFDimTolObjects as xdo
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Controller
from OCP.TCollection import TCollection_ExtendedString as ES
from OCP.TCollection import TCollection_HAsciiString as HS
from OCP.TColStd import TColStd_HArray1OfReal
from OCP.TDataStd import TDataStd_IntegerArray, TDataStd_Name
from OCP.TDF import TDF_Attribute, TDF_AttributeIterator, TDF_ChildIterator, TDF_Label, TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopoDS import TopoDS_Shape
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import (
    XCAFDoc_ColorTool,
    XCAFDoc_ColorType,
    XCAFDoc_Datum,
    XCAFDoc_Dimension,
    XCAFDoc_DimTolTool,
    XCAFDoc_DocumentTool,
    XCAFDoc_GeomTolerance,
    XCAFDoc_LayerTool,
    XCAFDoc_ShapeTool,
)

from d2c.cad.checks import Point, fits_hole, position_distances, select_faces
from d2c.spec import AXIS_COS, LINEAR_TOL, VOLUME_REL_TOL, Dimension, PartSpec

RGB = tuple[float, float, float]
STEEL: RGB = (0.6, 0.67, 0.69)
ORANGE: RGB = (1.0, 0.5, 0.0)  # Flächen einer Prüfmarkierung
GREEN: RGB = (0.396, 0.941, 0.451)  # Gewindeflächen
MARK_LAYER = "ASSUMED_CHECK"
GLB_LINEAR_TOL = 0.01  # mm: größte Abweichung des GLB-Netzes von der Form
GLB_ANGULAR_TOL = 0.1  # rad: größter Winkel zwischen benachbarten Netzflächen im GLB
SCHEMA = "AP242DIS"  # OCCT-Bezeichnung für AP242; "AP242" allein wird abgelehnt

_DIM_TYPES = {
    "diameter": xdo.XCAFDimTolObjects_DimensionType_Size_Diameter,
    "distance": xdo.XCAFDimTolObjects_DimensionType_Location_LinearDistance,
}
_GEOM_TYPES = {
    "flatness": xdo.XCAFDimTolObjects_GeomToleranceType_Flatness,
    "straightness": xdo.XCAFDimTolObjects_GeomToleranceType_Straightness,
    "roundness": xdo.XCAFDimTolObjects_GeomToleranceType_CircularityOrRoundness,
    "cylindricity": xdo.XCAFDimTolObjects_GeomToleranceType_Cylindricity,
    "parallelism": xdo.XCAFDimTolObjects_GeomToleranceType_Parallelism,
    "perpendicularity": xdo.XCAFDimTolObjects_GeomToleranceType_Perpendicularity,
    "angularity": xdo.XCAFDimTolObjects_GeomToleranceType_Angularity,
    "concentricity": xdo.XCAFDimTolObjects_GeomToleranceType_Concentricity,
    "symmetry": xdo.XCAFDimTolObjects_GeomToleranceType_Symmetry,
    "circular_runout": xdo.XCAFDimTolObjects_GeomToleranceType_CircularRunout,
    "total_runout": xdo.XCAFDimTolObjects_GeomToleranceType_TotalRunout,
    "position": xdo.XCAFDimTolObjects_GeomToleranceType_Position,
    "line_profile": xdo.XCAFDimTolObjects_GeomToleranceType_ProfileOfLine,
    "surface_profile": xdo.XCAFDimTolObjects_GeomToleranceType_ProfileOfSurface,
}
_COLOR_TYPES = (
    XCAFDoc_ColorType.XCAFDoc_ColorGen,
    XCAFDoc_ColorType.XCAFDoc_ColorSurf,
    XCAFDoc_ColorType.XCAFDoc_ColorCurv,
)
_FOLD = str.maketrans(
    {
        "Ø": "dia ",
        "ø": "dia ",
        "⌀": "dia ",
        "°": " deg",
        "±": "+-",
        "×": "x",
        "ß": "ss",
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "–": "-",
        "—": "-",
        "−": "-",
    }
)
# XCAFDoc_Dimension speichert [Bohrung, Grundabmaß, Genauigkeitsgrad] auf diesem untergeordneten Dokumentknoten. GetClassOfTolerance
# gibt diese Ausgabeparameter unter OCP nicht zurück; der Leser bezieht sie daher von hier.
_CLASS_TAG = 4


def _ascii(text: str) -> str:
    """Der Text in einer Zeile; Ø, ° und Ähnliches werden ausgeschrieben, Umlaute als ae, oe, ue, andere Akzente entfallen."""
    folded = unicodedata.normalize("NFKD", text.translate(_FOLD)).encode("ascii", "ignore").decode()
    return " ".join(folded.split())


def _hex(rgb: RGB) -> str:
    # OCCT speichert Farben linear; beim Zurücklesen entstehen Rundungsabweichungen, daher vor dem Skalieren runden.
    return "#" + "".join(f"{round(round(c, 4) * 255):02x}" for c in rgb)


def _num(x: float) -> str:
    return f"{round(x, 3) + 0.0:g}"  # + 0.0 wandelt -0.0 in 0.0 um


def _new_doc() -> TDocStd_Document:
    doc = TDocStd_Document(ES("MDTV-XCAF"))
    XCAFApp_Application.GetApplication_s().NewDocument(ES("MDTV-XCAF"), doc)
    return doc


def _attribute(label: TDF_Label, cls: type[TDF_Attribute]) -> TDF_Attribute | None:
    """Das Attribut eines Typs auf einem Label oder None. `TDF_Label.FindAttribute` füllt sein Handle unter OCP nicht aus."""
    attributes = TDF_AttributeIterator(label)
    while attributes.More():
        if isinstance(attributes.Value(), cls):
            return attributes.Value()
        attributes.Next()
    return None


def _labels(sequence: TDF_LabelSequence) -> list[TDF_Label]:
    return [sequence.Value(i) for i in range(1, sequence.Length() + 1)]


def _cylinder_faces(shape: cq.Shape, hole: dict, tol: float = LINEAR_TOL) -> list[cq.Face]:
    """Die zylindrischen Flächen der Bohrung {"d", "at", "axis"}: ihr Durchmesser, eine Achse durch `at`, ihre Richtung.

    Durchmesser und Abstand der Achse von `at` dürfen um tol abweichen.
    """
    point, direction = gp_Pnt(*hole["at"]), gp_Dir(*hole["axis"])
    faces = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        if fits_hole(surface.Cylinder(), hole["d"], point, direction, tol):
            faces.append(face)
    return faces


MARK_REACH = 5.0  # mm: so weit darf ein Markierungspunkt neben jeder Fläche liegen und trifft noch die nächstliegende


def mark_faces(shape: cq.Shape, mark: dict) -> list[cq.Face]:
    """Die Flächen einer Markierung: die festgelegten `face_ids` (Indizes in `shape.Faces()`, siehe `pin_marks`), sonst die Flächen
    nahe ihren Punkten; trifft kein Punkt eine Fläche, die nächstliegende, wenn sie höchstens MARK_REACH entfernt ist. Ein
    Punkt weit neben dem Teil markiert nichts, und der Export scheitert an der Prüfung "Marked faces"."""
    if "face_ids" in mark:
        faces = shape.Faces()
        return [faces[i] for i in mark["face_ids"]]
    near = select_faces(shape, mark["near"])
    if near or not mark["near"]:
        return near
    vertices = [cq.Vertex.makeVertex(*p) for p in mark["near"]]
    distance, face = min(((min(f.distance(v) for v in vertices), f) for f in shape.Faces()), key=lambda pair: pair[0])
    return [face] if distance <= MARK_REACH else []


def _review_faces(shape: cq.Shape, marks: Sequence[dict], threads: Sequence[dict]) -> tuple[dict[cq.Face, list[str]], list[cq.Face]]:
    """Zu färbende Flächen: zuerst markierte Flächen mit allen IDs der dort treffenden Markierungen (orange),
    danach nicht markierte Gewindeflächen (grün)."""
    marked: dict[cq.Face, list[str]] = {}
    for mark in marks:
        for face in mark_faces(shape, mark):
            marked.setdefault(face, []).append(mark["id"])
    threaded = dict.fromkeys(face for thread in threads for face in _cylinder_faces(shape, thread))
    return marked, [face for face in threaded if face not in marked]


def _fit_class(fit: str | None) -> tuple | None:
    """(Bohrung, Grundabmaß, Genauigkeitsgrad) einer ISO-286-Passung wie "H7" oder "g6", oder None, falls XCAF sie nicht abbilden kann.

    OCCT 7.9.3 schreibt den Genauigkeitsgrad um zwei Stufen zu hoch (IT7 als '9'); deshalb wird der Aufzählungswert
    um eine IT-Stufe niedriger gesetzt: DimensionGrade(6) ergibt '7'. IT1 (geschrieben als '01') ist so nicht
    erreichbar; verfügbar sind IT2 bis IT18.
    """
    match = re.fullmatch(r"([A-Za-z]{1,2})(\d{1,2})", fit or "")
    if match is None:
        return None
    variance = getattr(xdo, f"XCAFDimTolObjects_DimensionFormVariance_{match[1].upper()}", None)
    grade = int(match[2])
    if variance is None or not 2 <= grade <= 18:
        return None
    return match[1].isupper(), variance, xdo.XCAFDimTolObjects_DimensionGrade(grade - 1)


def _reals(*values: float) -> TColStd_HArray1OfReal:
    array = TColStd_HArray1OfReal(1, len(values))
    for i, value in enumerate(values, 1):
        array.SetValue(i, value)
    return array


class _Writer:
    """Ein XCAF-Dokument mit dem Bauteil. Flächen erhalten ein Label, sobald Farbe, Name oder Maß eines benötigt."""

    def __init__(self, body: cq.Compound, name: str) -> None:
        self.doc = _new_doc()
        main = self.doc.Main()
        self.shapes = XCAFDoc_DocumentTool.ShapeTool_s(main)
        self.colors = XCAFDoc_DocumentTool.ColorTool_s(main)
        self.layers = XCAFDoc_DocumentTool.LayerTool_s(main)
        self.tolerances = XCAFDoc_DocumentTool.DimTolTool_s(main)
        self.datums: dict[str, TDF_Label] = {}
        self.root = self.shapes.AddShape(body.wrapped, False)
        TDataStd_Name.Set_s(self.root, ES(_ascii(name)))
        self._paint(self.root, STEEL)

    def _paint(self, label: TDF_Label, rgb: RGB) -> None:
        color = Quantity_Color(*rgb, Quantity_TOC_sRGB)
        self.colors.SetColor(label, color, XCAFDoc_ColorType.XCAFDoc_ColorSurf)

    def label(self, face: cq.Face) -> TDF_Label:
        label = self.shapes.AddSubShape(self.root, face.wrapped)  # das bereits zuvor angelegte Label dieser Fläche
        if label.IsNull():
            raise ValueError("A face does not belong to the solid.")
        return label

    def color(self, face: cq.Face, rgb: RGB) -> TDF_Label:
        label = self.label(face)
        self._paint(label, rgb)
        return label

    def mark(self, face: cq.Face, ids: list[str]) -> None:
        label = self.color(face, ORANGE)
        TDataStd_Name.Set_s(label, ES(_ascii("CHECK " + " ".join(ids))))
        self.layers.SetLayer(label, ES(MARK_LAYER))

    def dimension(self, entry: dict) -> None:
        """Durchmesser auf einer Fläche oder Abstand zwischen zwei Flächen, mit Passung oder +- Abweichungen und einem Hinweis."""
        obj = xdo.XCAFDimTolObjects_DimensionObject()
        obj.SetType(_DIM_TYPES[entry["type"]])
        fit = _fit_class(entry.get("fit"))
        if fit is not None:
            obj.SetValue(entry["value"])
            obj.SetClassOfTolerance(*fit)
        elif entry.get("tol_upper") is not None:
            # XCAF speichert [Nennmaß, Betrag der unteren Abweichung, obere Abweichung]
            obj.SetValues(_reals(entry["value"], -entry["tol_lower"], entry["tol_upper"]))
        else:
            obj.SetValue(entry["value"])
        if entry.get("description"):
            obj.AddDescription(HS(_ascii(entry["description"])), HS("note"))  # ein DESCRIPTIVE_REPRESENTATION_ITEM
        label = self.tolerances.AddDimension()
        _attribute(label, XCAFDoc_Dimension).SetObject(obj)
        faces = [self.label(face) for face in entry.get("faces") or [entry["face"]]]
        self.tolerances.SetDimension(*faces, label)

    def datum(self, label: str, face: cq.Face) -> None:
        """Ein Bezug mit Buchstaben auf einer Fläche (DATUM_FEATURE und DATUM)."""
        obj = xdo.XCAFDimTolObjects_DatumObject()
        obj.SetName(HS(label))
        obj.SetPosition(1)  # Ohne Position 1 bis 3 lässt OCCT den Bezug im Bezugssystem einer Toleranz weg.
        node = self.tolerances.AddDatum()
        _attribute(node, XCAFDoc_Datum).SetObject(obj)
        faces = TDF_LabelSequence()
        faces.Append(self.label(face))
        self.tolerances.SetDatum(faces, node)
        self.datums[label] = node

    def geometric_tolerance(self, entry: dict) -> None:
        """Eine Form- oder Lagetoleranz {"kind", "value", "face", "datums"} auf einer Fläche; die Bezüge müssen bereits angelegt sein."""
        obj = xdo.XCAFDimTolObjects_GeomToleranceObject()
        obj.SetType(_GEOM_TYPES[entry["kind"]])
        obj.SetValue(entry["value"] * 0.001)  # OCCT schreibt den Wert immer mit der Einheit Meter; der Leser rechnet in mm zurück.
        node = self.tolerances.AddGeomTolerance()
        _attribute(node, XCAFDoc_GeomTolerance).SetObject(obj)
        self.tolerances.SetGeomTolerance(self.label(entry["face"]), node)
        for label in entry["datums"]:
            self.tolerances.SetDatumToGeomTol(self.datums[label], node)

    def save(self, path: str) -> None:
        STEPControl_Controller.Init_s()  # Die folgenden Einstellungen sind erst nach beiden Init-Aufrufen verfügbar.
        STEPCAFControl_Controller.Init_s()
        previous = Interface_Static.CVal_s("write.step.schema")
        try:
            if not Interface_Static.SetCVal_s("write.step.schema", SCHEMA):
                raise RuntimeError(f"OCCT does not know the STEP schema {SCHEMA}.")
            Interface_Static.SetIVal_s("write.stepcaf.subshapes.name", 1)
            writer = STEPCAFControl_Writer()
            for mode in (writer.SetColorMode, writer.SetNameMode, writer.SetDimTolMode, writer.SetLayerMode):
                mode(True)
            if not writer.Transfer(self.doc, STEPControl_AsIs) or writer.Write(path) != IFSelect_RetDone:
                raise OSError(f"The file {path} could not be written.")
        finally:
            Interface_Static.SetCVal_s("write.step.schema", previous)  # Die Einstellung gilt prozessweit.


def write_step(
    shape: cq.Shape, path: str, *, name: str, marks: Sequence[dict], pmi: Sequence[dict], threads: Sequence[dict], gdt: dict | None = None
) -> dict:
    """Schreibe die Form als STEP AP242 mit Farben, Namen, der Ebene ASSUMED_CHECK und semantischer PMI.

    `marks` enthält {"id", "near": [(x, y, z), ...]}: Die Flächen nahe den Punkten werden orange und
    "CHECK <id>" genannt; trifft eine Fläche mehrere Markierungen, enthält ihr Name alle IDs. `threads`
    enthält {"at", "axis", "d"}, wobei d der gebohrte Durchmesser ist: passende Zylinder werden grün.
    PMI-Einträge kommen aus pmi_from (ihre Flächen müssen zu `shape` gehören): {"type": "diameter",
    "face", "value", "tol_upper", "tol_lower", "fit"} oder {"type": "distance", "faces": [a, b],
    "value", "tol_upper", "tol_lower", "description"}. Eine Passung hat Vorrang vor Abweichungen.
    Eine von XCAF nicht unterstützte Passung wird weggelassen, das Maß aber trotzdem geschrieben.
    `gdt` kommt aus gdt_from: {"datums": [{"label", "face"}], "tolerances": [{"kind", "value", "face", "datums"}]}.
    """
    # Als Compound-Wurzel: Unter einem Solid mit gesetzter Position findet AddSubShape keine Flächen.
    body = cq.Compound.makeCompound(shape.Solids())
    marked, threaded = _review_faces(body, marks, threads)
    writer = _Writer(body, name)
    for face in threaded:
        writer.color(face, GREEN)
    for face, ids in marked.items():
        writer.mark(face, ids)
    for entry in pmi:
        writer.dimension(entry)
    gdt = gdt or {"datums": [], "tolerances": []}
    for datum in gdt["datums"]:
        writer.datum(datum["label"], datum["face"])
    for entry in gdt["tolerances"]:
        writer.geometric_tolerance(entry)
    writer.save(path)
    return {
        "path": path,
        "schema": SCHEMA,
        "pmi_written": len(pmi),
        "gdt_written": len(gdt["tolerances"]),
        "colored_faces": len(marked) + len(threaded),
    }


def _size(dim: Dimension) -> dict:
    return {"value": dim.value, "tol_upper": dim.tol_upper, "tol_lower": dim.tol_lower}


def _hole_pmi(dims: dict[str, Dimension], shape: cq.Shape, report: dict, tol: float) -> list[dict]:
    entries = []
    for check in report["dims"]:
        if check["check"] != "hole" or not check["ok"]:
            continue
        diameters = [dims[i] for i in check["dim_ids"] if i in dims and dims[i].kind == "diameter"]
        faces = _cylinder_faces(shape, check["expected"], tol)
        if diameters and faces:
            face = max(faces, key=lambda f: f.Area())
            entries.append({"type": "diameter", "face": face, **_size(diameters[0]), "fit": diameters[0].fit, "text": diameters[0].text})
    return entries


ASSUMED_TOL = 0.05  # mm: so weit darf der Abstand zweier Ebenen oder ein Zylinderdurchmesser vom angenommenen Maß abweichen
ASSUMED_KINDS = ("length", "radius", "diameter")  # Maße, für die ein angenommenes PMI geschrieben werden kann
GDT_NEAR = 0.5  # mm: so weit darf der Punkt eines Bezugs oder einer Form- und Lagetoleranz von seiner Fläche entfernt liegen


def _near_faces(shape: cq.Shape, points: Sequence[Point]) -> list[cq.Face]:
    """Die Flächen nahe den Prüfpunkten; trifft kein Punkt eine Fläche, die dem ersten Punkt nächstliegende (Punkt auf einer Kante)."""
    near = select_faces(shape, points)
    if near or not points:
        return near
    vertices = [cq.Vertex.makeVertex(*p) for p in points]
    return [min(shape.Faces(), key=lambda f: min(f.distance(v) for v in vertices))]


def _opposite_planes(shape: cq.Shape, near: list[cq.Face], length: float, tol: float) -> list[cq.Face] | None:
    """Eine getroffene Ebene und die dazu parallele Ebene im ganzen Körper im Abstand `length` (±tol), oder None.

    Mehrere Paare: zuerst solche mit entgegengesetzt zeigenden Normalen (Außenseiten eines Quaders, Wände einer Nut),
    dann der kleinste Abstandsfehler, dann die größere Fläche der kleineren der beiden.
    """
    planes = [(f, f.normalAt(), f.Center(), f.Area()) for f in shape.Faces() if f.geomType() == "PLANE"]
    best = None
    for hit in (p for p in planes if any(p[0].isSame(f) for f in near)):
        for other in planes:
            dot = hit[1].dot(other[1])
            error = abs(abs((other[2] - hit[2]).dot(hit[1])) - length)
            if other[0].isSame(hit[0]) or abs(dot) < AXIS_COS or error > tol:
                continue
            key = (dot > 0, round(error, 6), -min(hit[3], other[3]))
            if best is None or key < best[0]:
                best = (key, [hit[0], other[0]])
    return best[1] if best else None


def _cylinder_of(shape: cq.Shape, near: list[cq.Face], diameter: float, points: Sequence[Point], tol: float) -> cq.Face | None:
    """Die Zylinderfläche mit dem Durchmesser (±tol) unter den getroffenen Flächen, sonst die den Punkten nächste im Körper."""

    def matches(face: cq.Face) -> bool:
        surface = BRepAdaptor_Surface(face.wrapped)
        return surface.GetType() == GeomAbs_Cylinder and abs(2 * surface.Cylinder().Radius() - diameter) <= tol

    hit = [f for f in near if matches(f)]
    if hit:
        return max(hit, key=lambda f: f.Area())
    vertices = [cq.Vertex.makeVertex(*p) for p in points]
    others = [f for f in shape.Faces() if matches(f)] if vertices else []
    return min(others, key=lambda f: min(f.distance(v) for v in vertices), default=None)


def _assumed_pmi(spec: PartSpec, dims: dict[str, Dimension], shape: cq.Shape, tol: float) -> list[dict]:
    """PMI für jedes angenommene Längen-, Radius- oder Durchmessermaß, das ein Prüfeintrag nennt.

    Länge: die von mark_near getroffene Ebene (bei einem Punkt auf einer Kante jede der anliegenden) und die parallele
    Gegenebene im ganzen Körper im Abstand des Maßes. Radius und Durchmesser: die Zylinderfläche am Punkt, als
    Durchmesser-PMI (bei einem Radius mit dem doppelten Wert). Findet sich nichts, fehlt das PMI; `missing_assumed_pmi` meldet das.
    """
    tol = max(tol, ASSUMED_TOL)
    entries = []
    for dim in spec.dimensions:
        if not dim.assumed or dim.kind not in ASSUMED_KINDS:
            continue
        for item in (i for i in spec.review if dim.id in i.refs):
            near = _near_faces(shape, item.mark_near)
            note = f"ASSUMPTION: {item.assumption} - {item.reason} - please check"
            if dim.kind == "length":
                planes = _opposite_planes(shape, near, dim.value, tol)
                entry = {"type": "distance", "faces": planes, **_size(dim)} if planes else None
            else:
                twice = 2 if dim.kind == "radius" else 1
                face = _cylinder_of(shape, near, twice * dim.value, item.mark_near, tol)
                size = {
                    "value": twice * dim.value,
                    "tol_upper": None if dim.tol_upper is None else twice * dim.tol_upper,
                    "tol_lower": None if dim.tol_lower is None else twice * dim.tol_lower,
                }
                entry = {"type": "diameter", "face": face, **size, "fit": dim.fit if twice == 1 else None} if face else None
            if entry:
                entries.append(entry | {"description": note, "dim_id": dim.id, "item_id": item.id})
                break
    return entries


def missing_assumed_pmi(spec: PartSpec, pmi: Sequence[dict]) -> tuple[list[str], list[str]]:
    """(erwartet, fehlend): IDs der angenommenen Maße, die ein PMI haben können, und davon die ohne PMI mit ASSUMPTION-Hinweis.

    `pmi` sind die aus der STEP-Datei gelesenen Maße (read_pmi). Ein Eintrag passt zu einem Maß mit Art und Wert
    (Länge: distance, Radius: diameter mit doppeltem Wert) und wird nur einmal gezählt.
    """
    free = [p for p in pmi if any(d.startswith("ASSUMPTION") for d in p["descriptions"])]
    wanted, missing = [], []
    for dim in (d for d in spec.dimensions if d.assumed and d.kind in ASSUMED_KINDS):
        wanted.append(dim.id)
        kind, value = ("distance", dim.value) if dim.kind == "length" else ("diameter", dim.value * (2 if dim.kind == "radius" else 1))
        match = next((p for p in free if p["type"] == kind and abs(p["value"] - value) < 1e-4), None)
        if match is None:
            missing.append(dim.id)
        else:
            free.remove(match)
    return wanted, missing


def _nearest_face(shape: cq.Shape, point: Point) -> cq.Face:
    """Die Fläche, die dem Punkt am nächsten liegt."""
    vertex = cq.Vertex.makeVertex(*point)
    return min(shape.Faces(), key=lambda f: f.distance(vertex))


def _distance(a: cq.Face, b: cq.Face) -> float | None:
    """Abstand zweier paralleler Ebenen, einer Bohrungsachse von einer zu ihr parallelen Ebene oder zweier paralleler Achsen; sonst None."""
    surfaces = [BRepAdaptor_Surface(f.wrapped) for f in (a, b)]
    kinds = [s.GetType() for s in surfaces]
    if GeomAbs_Plane in kinds:
        plane = surfaces[kinds.index(GeomAbs_Plane)].Plane()
        other = surfaces[1 - kinds.index(GeomAbs_Plane)]
        normal, origin = plane.Axis().Direction(), plane.Location()
        if other.GetType() == GeomAbs_Plane:
            if abs(other.Plane().Axis().Direction().Dot(normal)) < AXIS_COS:
                return None
            where = other.Plane().Location()
        elif other.GetType() == GeomAbs_Cylinder:
            if abs(other.Cylinder().Axis().Direction().Dot(normal)) > 1 - AXIS_COS:
                return None  # die Achse steht nicht parallel zur Ebene
            where = other.Cylinder().Location()
        else:
            return None
        offset = (where.X() - origin.X(), where.Y() - origin.Y(), where.Z() - origin.Z())
        return abs(offset[0] * normal.X() + offset[1] * normal.Y() + offset[2] * normal.Z())
    if kinds == [GeomAbs_Cylinder, GeomAbs_Cylinder]:
        axes = [s.Cylinder().Axis() for s in surfaces]
        if abs(axes[0].Direction().Dot(axes[1].Direction())) < AXIS_COS:
            return None
        return gp_Lin(axes[0]).Distance(axes[1].Location())
    return None


def _located_pmi(spec: PartSpec, shape: cq.Shape, tol: float) -> list[dict]:
    """Ein Abstands-PMI je Längenmaß mit `faces_near`: zwischen den Flächen an den beiden Punkten, wenn ihr Abstand dem Maß
    entspricht (±tol). Sonst entsteht keines; `unplaced_pmi` nennt das Maß."""
    entries = []
    for dim in (d for d in spec.dimensions if d.faces_near):
        faces = [_nearest_face(shape, p) for p in dim.faces_near]
        found = _distance(*faces)
        if found is not None and abs(found - dim.value) <= tol:
            entries.append({"type": "distance", "faces": faces, **_size(dim), "fit": dim.fit, "dim_id": dim.id})
    return entries


def unplaced_pmi(spec: PartSpec, pmi: Sequence[dict], gdt: dict) -> list[str]:
    """IDs der Maße mit `faces_near`, der Bezüge und der Form- und Lagetoleranzen, für die kein PMI entstand."""
    placed = {e.get("dim_id") for e in pmi} | {e["id"] for e in (*gdt["datums"], *gdt["tolerances"])}
    wanted = [d.id for d in spec.dimensions if d.faces_near] + [d.id for d in spec.datums] + [t.id for t in spec.geometric_tolerances]
    return [i for i in wanted if i not in placed]


def gdt_from(spec: PartSpec, shape: cq.Shape) -> dict:
    """Bezüge und Form- und Lagetoleranzen der Spezifikation auf der Fläche, die ihrem Punkt `near` am nächsten liegt.

    Ein Punkt weiter als GDT_NEAR vom Körper trifft keine Fläche: Der Eintrag entfällt, ebenso eine Toleranz, deren Bezug
    keine Fläche fand. Ergebnis für write_step: {"datums": [{"id", "label", "face"}], "tolerances": [{"id", "kind", "value", "face", "datums"}]}.
    """

    def face_at(point: Point) -> cq.Face | None:
        face = _nearest_face(shape, point)
        return face if face.distance(cq.Vertex.makeVertex(*point)) <= GDT_NEAR else None

    datums = [{"id": d.id, "label": d.label, "face": face} for d in spec.datums if (face := face_at(d.near)) is not None]
    placed = {d["label"] for d in datums}
    tolerances = [
        {"id": t.id, "kind": t.kind, "value": t.value, "face": face, "datums": t.datums}
        for t in spec.geometric_tolerances
        if set(t.datums) <= placed and (face := face_at(t.near)) is not None
    ]
    return {"datums": datums, "tolerances": tolerances}


def pmi_from(spec: PartSpec, shape: cq.Shape, report: dict, tol: float = LINEAR_TOL) -> list[dict]:
    """Die PMI-Einträge für write_step: ein Durchmesser je gefundener Bohrung und ein PMI je angenommenem Maß (Länge, Radius, Durchmesser).

    Eine Bohrung zählt, wenn ihre Prüfung in `report["dims"]` den Status ok hat und eines ihrer Maße ein
    Durchmesser ist. Der Durchmesser wird mit Wert, Abweichungen oder Passung auf der Zylinderfläche
    abgelegt. `text`, also die gedruckte Maßangabe, wird mitgeführt, aber nicht geschrieben.

    Zu jedem angenommenen Maß (siehe `_assumed_pmi`) kommt ein Abstand zwischen zwei parallelen Ebenen oder ein
    Durchmesser auf der Zylinderfläche, jeweils mit dem Hinweis
    "ASSUMPTION: <assumption> - <reason> - please check". So steht etwa eine nicht bemaßte Breite als Maß mit Hinweis in der Datei.
    Liegt die Zylinderfläche schon als Bohrung vor, trägt dieser Eintrag den Hinweis.

    Dazu kommt je Längenmaß mit `faces_near` ein Abstand zwischen den beiden Flächen mit Toleranz oder Passung (`_located_pmi`).

    tol ist die zulässige Abweichung der Form von gezeichneten Durchmessern, Bohrungspositionen und Längen, wie bei
    checks.dimension_check: Eine auf der Toleranzmitte gebaute Form braucht mehr als den Standard.
    """
    dims = {d.id: d for d in spec.dimensions}
    entries = _hole_pmi(dims, shape, report, tol) + _located_pmi(spec, shape, tol)
    for assumed in _assumed_pmi(spec, dims, shape, tol):
        same = next(
            (e for e in entries if e["type"] == "diameter" and assumed["type"] == "diameter" and e["face"].isSame(assumed["face"])), None
        )
        if same is None:
            entries.append(assumed)
        else:
            same.setdefault("description", assumed["description"])
            same.setdefault("item_id", assumed["item_id"])
    return entries


def pin_marks(marks: Sequence[dict], pmi: Sequence[dict], shape: cq.Shape) -> list[dict]:
    """Legt die Markierung eines Prüfeintrags auf die Flächen seines PMI fest.

    Hat der Eintrag ein angenommenes Maß mit PMI (`item_id` in `pmi_from`), sind genau diese Flächen seine Markierung: bei
    einer Länge die bemaßte Fläche und ihre Gegenebene, bei Radius und Durchmesser die Zylinderfläche. `mark_near` bleibt dann
    ohne Wirkung, auch wenn ein Punkt auf einer Kante weitere Flächen träfe. Alle anderen Markierungen bleiben unverändert.
    Die Flächen stehen als Indizes in `shape.Faces()` unter `face_ids`, damit sie auch für eine Kopie der Form gelten.
    """
    faces = shape.Faces()
    pinned: dict[str, set[int]] = {}
    for entry in pmi:
        if "item_id" in entry:
            pinned.setdefault(entry["item_id"], set()).update(faces.index(f) for f in entry.get("faces") or [entry["face"]])
    return [m | {"face_ids": sorted(pinned[m["id"]])} if m["id"] in pinned else m for m in marks]


def measure_geometry(shape: cq.Shape) -> dict:
    """{"diameters": [...], "distances": [...]}: Durchmesser aller Zylinderflächen und Längenabstände in mm.

    Die Abstände sind die aller Paare paralleler Ebenen und die Lagemaße der Zylinderachsen (checks.position_distances:
    Achse zu Ebene, Achse zu Achse samt Anteilen in X, Y und Z). Damit prüft `tolerances.compliance` tolerierte Maße
    ohne refs. Ein Fund belegt nur, dass der Körper ein solches Maß enthält, nicht, welches Merkmal es ist (ein
    Verrundungsradius zählt als Zylinder).
    """
    diameters, planes = set(), set()
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            diameters.add(round(2 * surface.Cylinder().Radius(), 6))
        elif surface.GetType() == GeomAbs_Plane:
            axis = surface.Plane().Axis()
            normal = (axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z())
            if next(c for c in normal if abs(c) > 1e-9) < 0:
                normal = tuple(-c for c in normal)  # eine Richtung je Ebene, damit Gegenflächen denselben Wert haben
            where = axis.Location()
            offset = sum(n * c for n, c in zip(normal, (where.X(), where.Y(), where.Z())))
            planes.add((tuple(round(c, 6) for c in normal), round(offset, 6)))
    ordered = sorted(planes)
    distances = {
        round(abs(b[1] - a[1]), 6)
        for k, a in enumerate(ordered)
        for b in ordered[k + 1 :]
        if abs(sum(x * y for x, y in zip(a[0], b[0]))) >= AXIS_COS
    }
    distances |= {round(float(d), 6) for d in position_distances(shape)}
    return {"diameters": sorted(diameters), "distances": sorted(d for d in distances if d > 1e-6)}


def measure_step(path: str) -> dict:
    """measure_geometry für den Körper einer STEP-Datei: so, wie ihn ein anderes Programm beim Öffnen sieht."""
    return measure_geometry(cq.Compound.makeCompound(cq.importers.importStep(path).vals()))


def _patch_materials(path: str) -> None:
    """Macht das Material matt. OCCT schreibt nur `baseColorFactor`; glTF verwendet dann den Standardwert
    `metallicFactor=1.0`, wodurch Betrachter das Bauteil dunkel darstellen."""
    blob = Path(path).read_bytes()
    json_length = struct.unpack_from("<I", blob, 12)[0]
    gltf = json.loads(blob[20 : 20 + json_length])
    for material in gltf["materials"]:
        material["pbrMetallicRoughness"].update(metallicFactor=0.0, roughnessFactor=0.8)
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)  # Der JSON-Block wird mit Leerzeichen auf 4 Byte aufgefüllt.
    rest = blob[20 + json_length :]  # Der BIN-Block bleibt unverändert.
    header = struct.pack("<4sII", b"glTF", 2, 20 + len(text) + len(rest))
    Path(path).write_bytes(header + struct.pack("<I4s", len(text), b"JSON") + text + rest)


def write_glb(shape: cq.Shape, path: str, marks: Sequence[dict], threads: Sequence[dict]) -> dict:
    """Schreibt die Form als GLB und verwendet dieselben Farben wie in der STEP-Datei. Gibt den Inhalt von `marks.json` zurück, der daneben gespeichert wird.

    glTF speichert keine Flächennamen; daher ordnet `marks.json` die Farbe markierter Flächen den IDs der
    Markierungen zu, die eine Fläche treffen: {"#ff8000": ["rv1", ...]}. Ohne Treffer enthält die Datei eine leere Zuordnung ({}).
    """
    body = shape.copy()  # Der Export erzeugt ein Netz direkt auf der Form und könnte sonst die Bounding Box des Aufrufers verändern.
    marked, threaded = _review_faces(body, marks, threads)
    assembly = cq.Assembly(body, name=Path(path).stem, color=cq.Color(*STEEL))
    for face in threaded:
        assembly.addSubshape(face, name="thread", color=cq.Color(*GREEN))
    for face, ids in marked.items():
        assembly.addSubshape(face, name="CHECK " + " ".join(ids), color=cq.Color(*ORANGE))
    assembly.export(path, tolerance=GLB_LINEAR_TOL, angularTolerance=GLB_ANGULAR_TOL)
    _patch_materials(path)
    hit = {i for ids in marked.values() for i in ids}
    mapping = {_hex(ORANGE): [m["id"] for m in marks if m["id"] in hit]} if hit else {}
    Path(path).with_name("marks.json").write_text(json.dumps(mapping, indent=2))
    return mapping


def _describe(topods: TopoDS_Shape) -> str:
    """Kurzbezeichnung der Geometrie einer PMI-Fläche, z. B. "plane z=10" oder "cylinder d=16 at (0, 0, 12.5)"."""
    shape = cq.Shape.cast(topods)
    kind = shape.ShapeType().lower()
    if kind != "face":
        return kind
    kind, center = shape.geomType().lower(), shape.Center()
    if kind == "plane":
        normal = shape.normalAt()
        axis = max("xyz", key=lambda a: abs(getattr(normal, a)))
        if abs(getattr(normal, axis)) >= AXIS_COS:
            return f"plane {axis}={_num(getattr(center, axis))}"
    elif kind == "cylinder":
        kind += f" d={_num(2 * BRepAdaptor_Surface(shape.wrapped).Cylinder().Radius())}"
    return f"{kind} at ({_num(center.x)}, {_num(center.y)}, {_num(center.z)})"


def _fit_of(dimension: TDF_Label) -> str:
    array = _attribute(dimension.FindChild(_CLASS_TAG, False), TDataStd_IntegerArray).Array()
    hole, variance, grade = (array.Value(i) for i in range(array.Lower(), array.Upper() + 1))
    letters = xdo.XCAFDimTolObjects_DimensionFormVariance(variance).name.rsplit("_", 1)[1]
    # Der Leser ordnet '7' IT7 zu, dessen Enum-Wert 8 ist.
    return (letters if hole else letters.lower()) + str(grade - 1)


def _dimension(label: TDF_Label) -> dict | None:
    """Ein Maß im Format von `read_pmi` oder None für Einträge ohne Zahlenwert (Darstellungen, allgemeine Labels)."""
    obj = _attribute(label, XCAFDoc_Dimension).GetObject()
    if obj.GetValues() is None:
        return None
    plus_minus = obj.IsDimWithPlusMinusTolerance()
    first, second = TDF_LabelSequence(), TDF_LabelSequence()
    XCAFDoc_DimTolTool.GetRefShapeLabel_s(label, first, second)
    kind = obj.GetType()
    return {
        "type": next((name for name, t in _DIM_TYPES.items() if t == kind), kind.name.split("Type_", 1)[1]),
        "value": obj.GetValue(),
        "tol_upper": obj.GetUpperTolValue() if plus_minus else None,
        "tol_lower": 0.0 - obj.GetLowerTolValue() if plus_minus else None,  # XCAF speichert den Betrag.
        "fit": _fit_of(label) if obj.IsDimWithClassOfTolerance() else None,
        "descriptions": [obj.GetDescription(i).ToCString() for i in range(obj.NbDescriptions())],
        "faces": [_describe(XCAFDoc_ShapeTool.GetShape_s(f)) for f in _labels(first) + _labels(second)],
    }


def _faces_of(label: TDF_Label) -> list[str]:
    first, second = TDF_LabelSequence(), TDF_LabelSequence()
    XCAFDoc_DimTolTool.GetRefShapeLabel_s(label, first, second)
    return [_describe(XCAFDoc_ShapeTool.GetShape_s(f)) for f in _labels(first) + _labels(second)]


def _datum_name(label: TDF_Label) -> str:
    return _attribute(label, XCAFDoc_Datum).GetObject().GetName().ToCString()


def _geometric_tolerance(label: TDF_Label) -> dict:
    """Eine Form- oder Lagetoleranz im Format von `read_pmi`."""
    obj = _attribute(label, XCAFDoc_GeomTolerance).GetObject()
    datums = TDF_LabelSequence()
    XCAFDoc_DimTolTool.GetDatumWithObjectOfTolerLabels_s(label, datums)
    kind = obj.GetType()
    return {
        "type": next((name for name, t in _GEOM_TYPES.items() if t == kind), kind.name.split("Type_", 1)[1]),
        "value": round(obj.GetValue(), 6),  # Meter-Umrechnung im Leser hinterlässt Gleitkommareste
        "datums": [_datum_name(d) for d in _labels(datums)],
        "faces": _faces_of(label),
    }


def _colors_of(label: TDF_Label) -> set[str]:
    found = set()
    for kind in _COLOR_TYPES:
        color = Quantity_Color()
        if XCAFDoc_ColorTool.GetColor_s(label, kind, color):
            found.add(_hex(color.Values(Quantity_TOC_sRGB)))
    return found


def _shape_labels(shapes: XCAFDoc_ShapeTool) -> list[TDF_Label]:
    """Alle Form-Labels des Dokuments samt untergeordneten Labels, also auch der Flächen."""
    roots = TDF_LabelSequence()
    shapes.GetShapes(roots)
    found = []
    for root in _labels(roots):
        found.append(root)
        children = TDF_ChildIterator(root, True)
        while children.More():
            found.append(children.Value())
            children.Next()
    return found


def _layers(tool: XCAFDoc_LayerTool) -> dict[str, int]:
    labels = TDF_LabelSequence()
    tool.GetLayerLabels(labels)
    layers = {}
    for label in _labels(labels):
        name, on_layer = ES(), TDF_LabelSequence()
        tool.GetLayer(label, name)
        XCAFDoc_LayerTool.GetShapesOfLayer_s(label, on_layer)
        layers[name.ToExtString()] = on_layer.Length()
    return layers


def read_pmi(path: str) -> dict:
    """Angaben, die ein CAD-System in einer STEP-Datei findet: Schema, semantische Maße, Farben und Ebenen.

    Das Ergebnis ist JSON-serialisierbar. `dimensions` enthält {"type", "value", "tol_upper", "tol_lower",
    "fit", "descriptions", "faces"}: `type` ist wie in `write_step` "diameter" oder "distance", sonst
    ein OCCT-Name wie "Size_Radius". Abweichungen sind vorzeichenbehaftet; ohne Toleranz ist der Wert None;
    `fit` hat etwa den Wert "H7". `colors` ordnet "#rrggbb" der Anzahl der Formen (Körper, Flächen, Kanten)
    mit dieser Farbe zu; `layers` ordnet Ebenennamen der Zahl ihrer Formen zu. `geometric_tolerances` enthält
    {"type", "value", "datums", "faces"} je Form- und Lagetoleranz (type wie in GEOMETRIC_KINDS der Spezifikation),
    `datums` {"label", "faces"} je Bezug.
    """
    STEPCAFControl_Controller.Init_s()
    doc = _new_doc()
    reader = STEPCAFControl_Reader()
    for mode in (reader.SetColorMode, reader.SetGDTMode, reader.SetLayerMode):
        mode(True)
    if reader.ReadFile(path) != IFSelect_RetDone or not reader.Transfer(doc):
        raise ValueError(f"The STEP file {path} could not be read.")
    main = doc.Main()
    dimensions, geometric, datums = TDF_LabelSequence(), TDF_LabelSequence(), TDF_LabelSequence()
    tool = XCAFDoc_DocumentTool.DimTolTool_s(main)
    tool.GetDimensionLabels(dimensions)
    tool.GetGeomToleranceLabels(geometric)
    tool.GetDatumLabels(datums)
    labels = _shape_labels(XCAFDoc_DocumentTool.ShapeTool_s(main))
    with open(path, encoding="utf-8", errors="replace") as step:
        schema = re.search(r"FILE_SCHEMA\s*\(\(\s*'(\w+)", step.read(8192))[1]
    return {
        "schema": schema,
        "dimensions": [d for d in map(_dimension, _labels(dimensions)) if d is not None],
        "geometric_tolerances": [_geometric_tolerance(t) for t in _labels(geometric)],
        "datums": [{"label": _datum_name(d), "faces": _faces_of(d)} for d in _labels(datums)],
        "colors": dict(Counter(c for label in labels for c in _colors_of(label))),
        "layers": _layers(XCAFDoc_DocumentTool.LayerTool_s(main)),
    }


VOLUME_EPS = 1e-9  # relative Genauigkeit der adaptiven Volumenintegration im Rückimport-Vergleich


def precise_volume(shape: cq.Shape) -> float:
    """Volumen mit adaptiver Integration. Die Standardintegration von OCC (Shape.Volume) liegt bei Verrundungen an
    Kegel- und Bogenkanten (Splineflächen) um einige 1e-5 daneben; das ist größer als VOLUME_REL_TOL und würde einen
    genauen Rückimport als Volumenänderung melden."""
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props, VOLUME_EPS)
    return props.Mass()


def step_roundtrip(
    wp: cq.Workplane, path: str, marks: Sequence[dict] = (), pmi: Sequence[dict] = (), threads: Sequence[dict] = (), gdt: dict | None = None
) -> dict:
    """Schreibt das Modell als STEP AP242, liest es zurück und vergleicht.

    marks sind {"id": str, "near": [(x, y, z), ...]}: Die Flächen nahe den Punkten einer Markierung werden eingefärbt
    und nach ihrer id benannt. Findet eine Markierung keine Fläche, ist das Ergebnis nicht ok. pmi und threads gehen an
    write_step (grüne Gewindeflächen, semantische Maße), ebenso gdt (Form- und Lagetoleranzen); aus der Datei müssen ebenso
    viele Maße und Form- und Lagetoleranzen zurückgelesen werden, wie geschrieben wurden.
    """
    shape = wp.val()
    selected = [(m["id"], mark_faces(shape, m)) for m in marks]
    written = write_step(shape, path, name=Path(path).stem, marks=marks, pmi=pmi, threads=threads, gdt=gdt)

    back = cq.Compound.makeCompound(cq.importers.importStep(path).vals())
    valid, solids = back.isValid(), len(back.Solids())
    volume = precise_volume(shape)
    volume_rel_diff = abs(precise_volume(back) - volume) / abs(volume)
    mark_report = [{"id": mark_id, "faces": len(faces)} for mark_id, faces in selected]
    back_pmi = read_pmi(path)
    pmi_report = {"written": written["pmi_written"], "read_back": len(back_pmi["dimensions"])}
    gdt_back = len(back_pmi.get("geometric_tolerances", []))
    if written["gdt_written"] or gdt_back:  # Form- und Lagetoleranzen nur im Bericht, wenn es welche gibt
        pmi_report |= {
            "gdt_written": written["gdt_written"],
            "gdt_read_back": gdt_back,
            "gdt_ids": [t["id"] for t in (gdt or {}).get("tolerances", []) if "id" in t],
        }
    same = pmi_report["read_back"] == pmi_report["written"] and gdt_back == written["gdt_written"]
    ok = valid and solids == 1 and volume_rel_diff <= VOLUME_REL_TOL and same
    return {
        "path": path,
        "reimport_valid": valid,
        "solids": solids,
        "volume_rel_diff": volume_rel_diff,
        "marks": mark_report,
        "pmi": pmi_report,
        "ok": ok and all(m["faces"] > 0 for m in mark_report),
    }

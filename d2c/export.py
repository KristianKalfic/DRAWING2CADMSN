"""Export für NX und die Webansicht: STEP AP242 mit semantischer PMI und Prüfmarkierungen, GLB und einem PMI-Leser.

Eine Prüfmarkierung färbt die Flächen nahe ihren Punkten orange, benennt sie "PRUEFEN <id>" und legt sie
auf den Layer ASSUMED_CHECK. Gewindebohrungen werden wie in den BCT-Referenzen grün. Maße werden als
semantische PMI abgelegt: DIMENSIONAL_SIZE für einen Durchmesser, DIMENSIONAL_LOCATION für den Abstand
zwischen zwei Flächen. Zeichen in der Datei werden in ASCII umgesetzt, da OCCT andere Zeichen roh als
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
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Controller
from OCP.TCollection import TCollection_ExtendedString as ES
from OCP.TCollection import TCollection_HAsciiString as HS
from OCP.TColStd import TColStd_HArray1OfReal
from OCP.TDataStd import TDataStd_IntegerArray, TDataStd_Name
from OCP.TDF import TDF_AttributeIterator, TDF_ChildIterator, TDF_Label, TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopoDS import TopoDS_Shape
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import (
    XCAFDoc_ColorTool,
    XCAFDoc_ColorType,
    XCAFDoc_Dimension,
    XCAFDoc_DimTolTool,
    XCAFDoc_DocumentTool,
    XCAFDoc_LayerTool,
    XCAFDoc_ShapeTool,
)

from d2c.checks import AXIS_COS, LINEAR_TOL, select_faces
from d2c.ops import Op
from d2c.spec import Dimension, PartSpec

RGB = tuple[float, float, float]
STEEL: RGB = (0.6, 0.67, 0.69)
ORANGE: RGB = (1.0, 0.5, 0.0)  # Flächen einer Prüfmarkierung
GREEN: RGB = (0.396, 0.941, 0.451)  # Gewindeflächen wie in den BCT-Referenzen
MARK_LAYER = "ASSUMED_CHECK"
SCHEMA = "AP242DIS"  # OCCT-Bezeichnung für AP242; "AP242" allein wird abgelehnt

_DIM_TYPES = {
    "diameter": xdo.XCAFDimTolObjects_DimensionType_Size_Diameter,
    "distance": xdo.XCAFDimTolObjects_DimensionType_Location_LinearDistance,
}
_COLOR_TYPES = (
    XCAFDoc_ColorType.XCAFDoc_ColorGen,
    XCAFDoc_ColorType.XCAFDoc_ColorSurf,
    XCAFDoc_ColorType.XCAFDoc_ColorCurv,
)
_FOLD = str.maketrans(
    {"Ø": "dia ", "ø": "dia ", "⌀": "dia ", "°": " deg", "±": "+-", "×": "x", "ß": "ss", "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "–": "-", "—": "-", "−": "-"}
)
# XCAFDoc_Dimension speichert [Bohrung, Grundabmaß, Genauigkeitsgrad] auf diesem untergeordneten Dokumentknoten. GetClassOfTolerance
# gibt diese Ausgabeparameter unter OCP nicht zurück; der Leser bezieht sie daher von hier.
_CLASS_TAG = 4


def _ascii(text: str) -> str:
    """The text on one line, with Ø, ° and the like spelled out, umlauts as ae, oe, ue and other accents dropped."""
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


def _attribute(label: TDF_Label, cls: type):
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
    """The cylindrical faces of the hole {"d", "at", "axis"}: its diameter, an axis through `at`, its direction.

    The diameter and the distance of the axis from `at` may be off by tol.
    """
    point, direction = gp_Pnt(*hole["at"]), gp_Dir(*hole["axis"])
    faces = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        if (
            abs(2 * cylinder.Radius() - hole["d"]) <= tol
            and gp_Lin(cylinder.Axis()).Distance(point) <= tol
            and abs(cylinder.Axis().Direction().Dot(direction)) >= AXIS_COS
        ):
            faces.append(face)
    return faces


def _review_faces(
    shape: cq.Shape, marks: Sequence[dict], threads: Sequence[dict]
) -> tuple[dict[cq.Face, list[str]], list[cq.Face]]:
    """Zu färbende Flächen: zuerst markierte Flächen mit allen IDs der dort treffenden Markierungen (orange),
    danach nicht markierte Gewindeflächen (grün)."""
    marked: dict[cq.Face, list[str]] = {}
    for mark in marks:
        for face in select_faces(shape, mark["near"]):
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
        self.root = self.shapes.AddShape(body.wrapped, False)
        TDataStd_Name.Set_s(self.root, ES(_ascii(name)))
        self._paint(self.root, STEEL)

    def _paint(self, label: TDF_Label, rgb: RGB) -> None:
        color = Quantity_Color(*rgb, Quantity_TOC_sRGB)
        self.colors.SetColor(label, color, XCAFDoc_ColorType.XCAFDoc_ColorSurf)

    def label(self, face: cq.Face) -> TDF_Label:
        label = self.shapes.AddSubShape(self.root, face.wrapped)  # das bereits zuvor angelegte Label dieser Fläche
        if label.IsNull():
            raise ValueError("Eine Fläche gehört nicht zum Körper.")
        return label

    def color(self, face: cq.Face, rgb: RGB) -> TDF_Label:
        label = self.label(face)
        self._paint(label, rgb)
        return label

    def mark(self, face: cq.Face, ids: list[str]) -> None:
        label = self.color(face, ORANGE)
        TDataStd_Name.Set_s(label, ES(_ascii("PRUEFEN " + " ".join(ids))))
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

    def save(self, path: str) -> None:
        STEPControl_Controller.Init_s()  # Die folgenden Einstellungen sind erst nach beiden Init-Aufrufen verfügbar.
        STEPCAFControl_Controller.Init_s()
        previous = Interface_Static.CVal_s("write.step.schema")
        try:
            if not Interface_Static.SetCVal_s("write.step.schema", SCHEMA):
                raise RuntimeError(f"OCCT kennt das STEP-Schema {SCHEMA} nicht.")
            Interface_Static.SetIVal_s("write.stepcaf.subshapes.name", 1)
            writer = STEPCAFControl_Writer()
            for mode in (writer.SetColorMode, writer.SetNameMode, writer.SetDimTolMode, writer.SetLayerMode):
                mode(True)
            if not writer.Transfer(self.doc, STEPControl_AsIs) or writer.Write(path) != IFSelect_RetDone:
                raise OSError(f"Die Datei {path} konnte nicht geschrieben werden.")
        finally:
            Interface_Static.SetCVal_s("write.step.schema", previous)  # Die Einstellung gilt prozessweit.


def write_step(
    shape: cq.Shape, path: str, *, name: str, marks: Sequence[dict], pmi: Sequence[dict], threads: Sequence[dict]
) -> dict:
    """Schreibe die Form als STEP AP242 mit Farben, Namen, der Ebene ASSUMED_CHECK und semantischer PMI.

    `marks` enthält {"id", "near": [(x, y, z), ...]}: Die Flächen nahe den Punkten werden orange und
    "PRUEFEN <id>" genannt; trifft eine Fläche mehrere Markierungen, enthält ihr Name alle IDs. `threads`
    enthält {"at", "axis", "d"}, wobei d der gebohrte Durchmesser ist: passende Zylinder werden grün.
    PMI-Einträge kommen aus pmi_from (ihre Flächen müssen zu `shape` gehören): {"type": "diameter",
    "face", "value", "tol_upper", "tol_lower", "fit"} oder {"type": "distance", "faces": [a, b],
    "value", "tol_upper", "tol_lower", "description"}. Eine Passung hat Vorrang vor Abweichungen.
    Eine von XCAF nicht unterstützte Passung wird weggelassen, das Maß aber trotzdem geschrieben.
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
    writer.save(path)
    return {"path": path, "schema": SCHEMA, "pmi_written": len(pmi), "colored_faces": len(marked) + len(threaded)}


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
            entries.append({"type": "diameter", "face": face, **_size(diameters[0]), "fit": diameters[0].fit,
                            "text": diameters[0].text})
    return entries


def _parallel_planes(faces: list[cq.Face], length: float, tol: float) -> list[cq.Face] | None:
    """The two planar faces among `faces` if they are parallel and `length` (give or take tol) apart, else None."""
    planes = [face for face in faces if face.geomType() == "PLANE"]
    if len(planes) != 2:
        return None
    first, second = planes
    parallel = abs(first.normalAt().dot(second.normalAt())) >= AXIS_COS
    gap = abs((second.Center() - first.Center()).dot(first.normalAt()))
    return planes if parallel and abs(gap - length) <= tol else None


def _assumed_pmi(spec: PartSpec, dims: dict[str, Dimension], shape: cq.Shape, tol: float) -> list[dict]:
    entries = []
    for item in spec.review:
        lengths = [dims[i] for i in item.refs if i in dims and dims[i].assumed and dims[i].kind == "length"]
        if item.issue != "missing" or len(lengths) != 1:
            continue
        planes = _parallel_planes(select_faces(shape, item.mark_near), lengths[0].value, tol)
        if planes:
            note = f"ANNAHME: {item.assumption} - {item.reason} - bitte prüfen"
            entries.append({"type": "distance", "faces": planes, **_size(lengths[0]), "description": note})
    return entries


def pmi_from(spec: PartSpec, ops: Sequence[Op], shape: cq.Shape, report: dict, tol: float = LINEAR_TOL) -> list[dict]:
    """The PMI entries for write_step: a diameter per found hole and a distance per assumed length.

    Eine Bohrung zählt, wenn ihre Prüfung in `report["dims"]` den Status ok hat und eines ihrer Maße ein
    Durchmesser ist. Der Durchmesser wird mit Wert, Abweichungen oder Passung auf der Zylinderfläche
    abgelegt. `text`, also die gedruckte Maßangabe, wird mitgeführt, aber nicht geschrieben.

    Ein Prüfeintrag mit issue "missing" und genau einem angenommenen Längenmaß erhält einen Abstand zwischen
    den beiden parallelen Ebenen, die seine `mark_near`-Punkte treffen, sofern deren Abstand dem Maß
    entspricht. Als Hinweis wird
    "ANNAHME: <assumption> - <reason> - bitte prüfen". So bildet BCT beispielsweise die fehlende Breite der Sensoraufnahme ab.

    tol is how far the shape may be off the drawn diameters, hole positions and lengths, as in
    checks.dimension_check: a shape built on the tolerance mid needs more than the default.
    `ops` is part of the planned call and not used: the spec and the report hold everything needed.
    """
    dims = {d.id: d for d in spec.dimensions}
    return _hole_pmi(dims, shape, report, tol) + _assumed_pmi(spec, dims, shape, tol)


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
        assembly.addSubshape(face, name="PRUEFEN " + " ".join(ids), color=cq.Color(*ORANGE))
    assembly.export(path, tolerance=0.01, angularTolerance=0.1)
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
    mit dieser Farbe zu; `layers` ordnet Ebenennamen der Zahl ihrer Formen zu.
    """
    STEPCAFControl_Controller.Init_s()
    doc = _new_doc()
    reader = STEPCAFControl_Reader()
    for mode in (reader.SetColorMode, reader.SetGDTMode, reader.SetLayerMode):
        mode(True)
    if reader.ReadFile(path) != IFSelect_RetDone or not reader.Transfer(doc):
        raise ValueError(f"Die STEP-Datei {path} konnte nicht gelesen werden.")
    main = doc.Main()
    dimensions = TDF_LabelSequence()
    XCAFDoc_DocumentTool.DimTolTool_s(main).GetDimensionLabels(dimensions)
    labels = _shape_labels(XCAFDoc_DocumentTool.ShapeTool_s(main))
    with open(path, encoding="utf-8", errors="replace") as step:
        schema = re.search(r"FILE_SCHEMA\s*\(\(\s*'(\w+)", step.read(8192))[1]
    return {
        "schema": schema,
        "dimensions": [d for d in map(_dimension, _labels(dimensions)) if d is not None],
        "colors": dict(Counter(c for label in labels for c in _colors_of(label))),
        "layers": _layers(XCAFDoc_DocumentTool.LayerTool_s(main)),
    }

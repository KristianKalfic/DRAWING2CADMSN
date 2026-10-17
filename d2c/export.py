"""Export for NX and the web viewer: STEP AP242 with semantic PMI and review colors, GLB, and a PMI reader.

A review mark colors the faces near its points orange, names them "CHECK <id>" and puts them on the
layer ASSUMED_CHECK. Tapped holes turn green, as in the BCT references. Dimensions travel as semantic
PMI: DIMENSIONAL_SIZE for a diameter, DIMENSIONAL_LOCATION for the distance between two faces. Every
string in the file is ASCII, because OCCT writes anything else as raw UTF-8.
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
ORANGE: RGB = (1.0, 0.5, 0.0)  # faces of a review mark
GREEN: RGB = (0.396, 0.941, 0.451)  # thread faces, as in the BCT references
MARK_LAYER = "ASSUMED_CHECK"
SCHEMA = "AP242DIS"  # OCCT's name for AP242; plain "AP242" is rejected

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
    {"Ø": "dia ", "ø": "dia ", "⌀": "dia ", "°": " deg", "±": "+-", "×": "x", "ß": "ss", "–": "-", "—": "-", "−": "-"}
)
# XCAFDoc_Dimension keeps [hole, form variance, grade] on this child label. GetClassOfTolerance drops
# those out-arguments under OCP, so the reader takes them from here.
_CLASS_TAG = 4


def _ascii(text: str) -> str:
    """The text on one line, with Ø, ° and the like spelled out and accents dropped."""
    folded = unicodedata.normalize("NFKD", text.translate(_FOLD)).encode("ascii", "ignore").decode()
    return " ".join(folded.split())


def _hex(rgb: RGB) -> str:
    # OCCT keeps colors linear, so a color read back is off by float noise: round before scaling
    return "#" + "".join(f"{round(round(c, 4) * 255):02x}" for c in rgb)


def _num(x: float) -> str:
    return f"{round(x, 3) + 0.0:g}"  # + 0.0 turns -0.0 into 0.0


def _new_doc() -> TDocStd_Document:
    doc = TDocStd_Document(ES("MDTV-XCAF"))
    XCAFApp_Application.GetApplication_s().NewDocument(ES("MDTV-XCAF"), doc)
    return doc


def _attribute(label: TDF_Label, cls: type):
    """The attribute of a type on a label, or None. TDF_Label.FindAttribute does not fill its handle under OCP."""
    attributes = TDF_AttributeIterator(label)
    while attributes.More():
        if isinstance(attributes.Value(), cls):
            return attributes.Value()
        attributes.Next()
    return None


def _labels(sequence: TDF_LabelSequence) -> list[TDF_Label]:
    return [sequence.Value(i) for i in range(1, sequence.Length() + 1)]


def _cylinder_faces(shape: cq.Shape, hole: dict) -> list[cq.Face]:
    """The cylindrical faces of the hole {"d", "at", "axis"}: its diameter, an axis through `at`, its direction."""
    point, direction = gp_Pnt(*hole["at"]), gp_Dir(*hole["axis"])
    faces = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        if (
            abs(2 * cylinder.Radius() - hole["d"]) <= LINEAR_TOL
            and gp_Lin(cylinder.Axis()).Distance(point) <= LINEAR_TOL
            and abs(cylinder.Axis().Direction().Dot(direction)) >= AXIS_COS
        ):
            faces.append(face)
    return faces


def _review_faces(
    shape: cq.Shape, marks: Sequence[dict], threads: Sequence[dict]
) -> tuple[dict[cq.Face, list[str]], list[cq.Face]]:
    """What gets colored: the marked faces with the ids of all marks that hit them (orange), then the
    thread faces nobody marked (green)."""
    marked: dict[cq.Face, list[str]] = {}
    for mark in marks:
        for face in select_faces(shape, mark["near"]):
            marked.setdefault(face, []).append(mark["id"])
    threaded = dict.fromkeys(face for thread in threads for face in _cylinder_faces(shape, thread))
    return marked, [face for face in threaded if face not in marked]


def _fit_class(fit: str | None) -> tuple | None:
    """(hole, form variance, grade) of an ISO 286 fit like "H7" or "g6", or None if XCAF cannot hold it.

    OCCT 7.9.3 writes the grade 2 too high (IT7 as '9'), so the enum goes in one IT short: DimensionGrade(6)
    prints '7'. That cannot reach IT1 (written as '01'), which leaves IT2 to IT18.
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
    """An XCAF document holding the part. Faces get a label when a color, name or dimension needs one."""

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
        label = self.shapes.AddSubShape(self.root, face.wrapped)  # the existing label for a face added before
        if label.IsNull():
            raise ValueError("a face is not part of the shape")
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
        """A diameter of one face or a distance between two faces, with a fit or +- deviations, and a note."""
        obj = xdo.XCAFDimTolObjects_DimensionObject()
        obj.SetType(_DIM_TYPES[entry["type"]])
        fit = _fit_class(entry.get("fit"))
        if fit is not None:
            obj.SetValue(entry["value"])
            obj.SetClassOfTolerance(*fit)
        elif entry.get("tol_upper") is not None:
            # XCAF keeps [nominal, magnitude of the lower deviation, upper deviation]
            obj.SetValues(_reals(entry["value"], -entry["tol_lower"], entry["tol_upper"]))
        else:
            obj.SetValue(entry["value"])
        if entry.get("description"):
            obj.AddDescription(HS(_ascii(entry["description"])), HS("note"))  # a DESCRIPTIVE_REPRESENTATION_ITEM
        label = self.tolerances.AddDimension()
        _attribute(label, XCAFDoc_Dimension).SetObject(obj)
        faces = [self.label(face) for face in entry.get("faces") or [entry["face"]]]
        self.tolerances.SetDimension(*faces, label)

    def save(self, path: str) -> None:
        STEPControl_Controller.Init_s()  # the parameters below exist only after both Init calls
        STEPCAFControl_Controller.Init_s()
        previous = Interface_Static.CVal_s("write.step.schema")
        try:
            if not Interface_Static.SetCVal_s("write.step.schema", SCHEMA):
                raise RuntimeError(f"OCCT does not know the STEP schema {SCHEMA}")
            Interface_Static.SetIVal_s("write.stepcaf.subshapes.name", 1)
            writer = STEPCAFControl_Writer()
            for mode in (writer.SetColorMode, writer.SetNameMode, writer.SetDimTolMode, writer.SetLayerMode):
                mode(True)
            if not writer.Transfer(self.doc, STEPControl_AsIs) or writer.Write(path) != IFSelect_RetDone:
                raise OSError(f"cannot write {path}")
        finally:
            Interface_Static.SetCVal_s("write.step.schema", previous)  # the setting is global to the process


def write_step(
    shape: cq.Shape, path: str, *, name: str, marks: Sequence[dict], pmi: Sequence[dict], threads: Sequence[dict]
) -> dict:
    """Write the shape as STEP AP242 with colors, names, the layer ASSUMED_CHECK and semantic PMI.

    marks are {"id", "near": [(x, y, z), ...]}: the faces near the points turn orange and are named
    "CHECK <id>", a face hit by several marks gets all their ids. threads are {"at", "axis", "d"}, d being
    the drilled diameter: the matching cylinders turn green. pmi entries come from pmi_from (their faces
    must belong to `shape`):
    {"type": "diameter", "face", "value", "tol_upper", "tol_lower", "fit"} and
    {"type": "distance", "faces": [a, b], "value", "tol_upper", "tol_lower", "description"}.
    A fit wins over deviations. A fit that XCAF cannot hold is left out, the dimension is still written.
    """
    # a Compound root: below a Solid that carries a location, AddSubShape finds no faces
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


def _hole_pmi(dims: dict[str, Dimension], shape: cq.Shape, report: dict) -> list[dict]:
    entries = []
    for check in report["dims"]:
        if check["check"] != "hole" or not check["ok"]:
            continue
        diameters = [dims[i] for i in check["dim_ids"] if i in dims and dims[i].kind == "diameter"]
        faces = _cylinder_faces(shape, check["expected"])
        if diameters and faces:
            face = max(faces, key=lambda f: f.Area())
            entries.append({"type": "diameter", "face": face, **_size(diameters[0]), "fit": diameters[0].fit,
                            "text": diameters[0].text})
    return entries


def _parallel_planes(faces: list[cq.Face], length: float) -> list[cq.Face] | None:
    """The two planar faces among `faces` if they are parallel and `length` apart, else None."""
    planes = [face for face in faces if face.geomType() == "PLANE"]
    if len(planes) != 2:
        return None
    first, second = planes
    parallel = abs(first.normalAt().dot(second.normalAt())) >= AXIS_COS
    gap = abs((second.Center() - first.Center()).dot(first.normalAt()))
    return planes if parallel and abs(gap - length) <= LINEAR_TOL else None


def _assumed_pmi(spec: PartSpec, dims: dict[str, Dimension], shape: cq.Shape) -> list[dict]:
    entries = []
    for item in spec.review:
        lengths = [dims[i] for i in item.refs if i in dims and dims[i].assumed and dims[i].kind == "length"]
        if item.issue != "missing" or len(lengths) != 1:
            continue
        planes = _parallel_planes(select_faces(shape, item.mark_near), lengths[0].value)
        if planes:
            note = f"ASSUMED: {item.assumption} - {item.reason} - please check"
            entries.append({"type": "distance", "faces": planes, **_size(lengths[0]), "description": note})
    return entries


def pmi_from(spec: PartSpec, ops: Sequence[Op], shape: cq.Shape, report: dict) -> list[dict]:
    """The PMI entries for write_step: a diameter per found hole and a distance per assumed length.

    A hole counts when its check in report["dims"] is ok and one of its dimensions is a diameter. The
    diameter sits on the cylinder face, with the value, deviations or fit of that dimension. `text`, the
    printed callout, goes along but is not written.

    A review item of issue "missing" with exactly one assumed length gets a distance between the two
    parallel planes its mark_near hits, if they are that length apart, with the note "ASSUMED:
    <assumption> - <reason> - please check". That is how BCT carries the missing width of the Sensoraufnahme.

    `ops` is part of the planned call and not used: the spec and the report hold everything needed.
    """
    dims = {d.id: d for d in spec.dimensions}
    return _hole_pmi(dims, shape, report) + _assumed_pmi(spec, dims, shape)


def _patch_materials(path: str) -> None:
    """Make the material matte. OCCT writes only baseColorFactor, glTF then defaults to metallic 1.0 and viewers
    render the part dark."""
    blob = Path(path).read_bytes()
    json_length = struct.unpack_from("<I", blob, 12)[0]
    gltf = json.loads(blob[20 : 20 + json_length])
    for material in gltf["materials"]:
        material["pbrMetallicRoughness"].update(metallicFactor=0.0, roughnessFactor=0.8)
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)  # the JSON chunk is padded with spaces to 4 bytes
    rest = blob[20 + json_length :]  # the BIN chunk stays as it is
    header = struct.pack("<4sII", b"glTF", 2, 20 + len(text) + len(rest))
    Path(path).write_bytes(header + struct.pack("<I4s", len(text), b"JSON") + text + rest)


def write_glb(shape: cq.Shape, path: str, marks: Sequence[dict], threads: Sequence[dict]) -> dict:
    """Write the shape as GLB, colored like the STEP file. Returns the content of marks.json, written next to it.

    glTF drops face names, so marks.json maps the color of the marked faces to the ids of the marks that
    hit a face: {"#ff8000": ["rv1", ...]}. It is empty when no mark hit.
    """
    body = shape.copy()  # exporting meshes the shape in place, which would skew a bounding box of the caller's
    marked, threaded = _review_faces(body, marks, threads)
    assembly = cq.Assembly(body, name=Path(path).stem, color=cq.Color(*STEEL))
    for face in threaded:
        assembly.addSubshape(face, name="thread", color=cq.Color(*GREEN))
    for face, ids in marked.items():
        assembly.addSubshape(face, name="CHECK " + " ".join(ids), color=cq.Color(*ORANGE))
    assembly.export(path, tolerance=0.01, angularTolerance=0.1)
    _patch_materials(path)
    hit = {i for ids in marked.values() for i in ids}
    mapping = {_hex(ORANGE): [m["id"] for m in marks if m["id"] in hit]} if hit else {}
    Path(path).with_name("marks.json").write_text(json.dumps(mapping, indent=2))
    return mapping


def _describe(topods: TopoDS_Shape) -> str:
    """A short name for the geometry of a PMI face: "plane z=10", "cylinder d=16 at (0, 0, 12.5)"."""
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
    # the reader maps '7' to IT7, whose enum value is 8
    return (letters if hole else letters.lower()) + str(grade - 1)


def _dimension(label: TDF_Label) -> dict | None:
    """A dimension as read_pmi reports it, or None for entries without a number (presentations, common labels)."""
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
        "tol_lower": 0.0 - obj.GetLowerTolValue() if plus_minus else None,  # XCAF keeps its magnitude
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
    """Every shape label of the document with the labels below them: the faces."""
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
    """What a CAD system finds in a STEP file: the schema, semantic dimensions, colors and layers.

    The result is JSON-serializable. dimensions are {"type", "value", "tol_upper", "tol_lower", "fit",
    "descriptions", "faces"}: type is "diameter" or "distance" as write_step writes them, otherwise the
    OCCT name such as "Size_Radius"; deviations are signed and None without a tolerance; fit is like "H7".
    colors maps "#rrggbb" to the number of shapes (body, faces, edges) that carry it, layers maps a layer
    name to its number of shapes.
    """
    STEPCAFControl_Controller.Init_s()
    doc = _new_doc()
    reader = STEPCAFControl_Reader()
    for mode in (reader.SetColorMode, reader.SetGDTMode, reader.SetLayerMode):
        mode(True)
    if reader.ReadFile(path) != IFSelect_RetDone or not reader.Transfer(doc):
        raise ValueError(f"cannot read {path} as STEP")
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

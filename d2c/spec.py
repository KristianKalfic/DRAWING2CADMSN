"""PartSpec: erfasste Angaben eines Vision-Modells zu einer Zeichnungsseite, bevor Geometrie aufgebaut wird.

Die Validierung setzt die Prüfpflicht durch: Jedes angenommene Maß und jedes Maß mit niedriger Konfidenz
muss in einem Prüfeintrag aufgeführt sein, damit keine Annahme unbemerkt bleibt.
"""

import re
from collections import Counter
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_validator, model_validator

# Abnahmeschwellen der Prüfungen; Spec 7 ordnet sie den Soll-Werten zu.
LINEAR_TOL = 0.01  # mm
AXIS_COS = 0.9999  # Kleinster |cos|-Wert des Winkels zwischen zwei Achsen, damit sie als parallel gelten
VOLUME_REL_TOL = 1e-6  # Größte relative Volumenänderung beim STEP-Export mit Rückimport

PART_NAME = re.compile(r"[\w-]+")  # Zulässiger Teilname (wird als Ordnername verwendet); gilt mit fullmatch, wie PartSpec.name

_VALUE_TOL = 1e-9  # Größte Abweichung zwischen gedruckter Zahl und `value`, damit sie als gleich gilt
MIN_CONFIDENCE = 0.8  # Maße darunter müssen in einem Prüfeintrag aufgeführt sein

# Einfache Zahlen in einer gedruckten Maßangabe mit "." oder "," als Dezimaltrennzeichen.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


# Ein Maßstab, der nur aus einem Verhältnis besteht: "1/2", "2 : 1", "1,5:1". Texte wie "M 1:2" bleiben unverändert.
_RATIO_ONLY = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s*[:/]\s*(\d+(?:[.,]\d+)?)\s*")


def normalize_scale(text: str) -> str:
    """Bringt ein reines Verhältnis ("1/2", "2 : 1") auf die Form "a:b"; anderen Text lässt es unverändert."""
    match = _RATIO_ONLY.fullmatch(text)
    return ":".join(match.groups()) if match else text


def _prints_value(text: str, value: float) -> bool:
    """True, wenn eine Zahl im gedruckten Text dem Wert entspricht, damit "76,2" nicht als 762 gelesen wird."""
    return any(abs(float(n.replace(",", ".")) - value) <= _VALUE_TOL for n in _NUMBER.findall(text))


class _Strict(BaseModel):
    # NaN oder inf in bbox oder Bohrungsposition ließe spätere Vergleiche unbemerkt bestehen.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(_Strict):
    """Origin of a value: view and grid cell of the drawing."""

    view: str
    # A3: A-F/1-8, A4: A-F/1-4; null bei Zeichnungen ohne Raster.
    cell: str | None = Field(pattern=r"^[A-F][1-8]$")


class Dimension(_Strict):
    """A dimension.

    tol_upper/tol_lower are deviations in mm ("31 -0,1" -> 0 and -0.1),
    fit is an ISO 286 fit class such as h10 or H7.
    """

    id: str
    kind: Literal["length", "diameter", "radius", "angle", "chamfer", "thread", "count"]
    value: float
    text: str
    source: Source | None = None
    confidence: float = Field(ge=0, le=1)
    assumed: bool = False
    assumption_basis: str | None = None
    tol_upper: float | None = None
    tol_lower: float | None = None
    fit: str | None = Field(None, pattern=r"^[A-Za-z]{1,2}\d{1,2}$")
    faces_near: list[tuple[float, float, float]] = []
    """Lengths only: two model points, one on each of the two dimensioned faces (plane or hole, whose axis counts).
    The dimension is then written with its tolerance as semantic PMI between these faces."""


class View(_Strict):
    id: str
    kind: Literal["front", "top", "side", "section", "detail", "iso"]
    scale: str
    note: str = ""

    _scale = field_validator("scale")(normalize_scale)


class Feature(_Strict):
    id: str
    type: Literal["base", "boss", "pocket", "hole", "slot", "fillet", "chamfer", "thread", "revolve", "other"]
    dim_ids: list[str]
    note: str = ""


class ExpectedHole(_Strict):
    """A hole that the finished model must contain.

    d is the diameter, at a point on the hole axis and axis its direction (not the zero vector).
    """

    d: PositiveFloat
    at: tuple[float, float, float]
    axis: tuple[float, float, float]
    dim_ids: list[str]


class Expected(_Strict):
    """Requirements for the finished model. bbox holds (xlen, ylen, zlen) in model coordinates."""

    bbox: tuple[PositiveFloat, PositiveFloat, PositiveFloat]
    holes: list[ExpectedHole] = []


class ReviewItem(_Strict):
    """An assumption or open question for the reviewer.

    mark_near: model points near the affected faces, where color marks are placed.
    """

    id: str
    refs: list[str]
    issue: Literal["missing", "ambiguous", "low_confidence", "conflict", "convention"]
    assumption: str
    reason: str
    mark_near: list[tuple[float, float, float]] = []

    @field_validator("mark_near", mode="before")
    @classmethod
    def _points(cls, value: object) -> object:
        """Erwartet eine Liste von Punkten [[x, y, z], ...]. Eine flache Dreierliste [x, y, z] ist eindeutig ein
        einzelner Punkt und wird verpackt; ein Objekt oder eine andere Form wird mit der richtigen Form abgelehnt."""
        right = "Expected: a list of points, e.g. [[0, -19.5, 175]] (one point per face)"
        if (
            isinstance(value, (list, tuple))
            and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
        ):
            return [value]
        if isinstance(value, dict):
            raise ValueError(f"`mark_near` is an object (keys {sorted(value)}). {right}.")
        if isinstance(value, (list, tuple)) and not all(isinstance(p, (list, tuple)) for p in value):
            raise ValueError(f"`mark_near` contains entries that are not points [x, y, z]. {right}.")
        return value


# Toleranzarten nach ISO 1101. Formtoleranzen haben keinen Bezug, Richtungs-, Orts- (außer Position und Profil) und
# Lauftoleranzen brauchen mindestens einen.
FORM_KINDS = ("flatness", "straightness", "roundness", "cylindricity")
DATUM_KINDS = ("parallelism", "perpendicularity", "angularity", "concentricity", "symmetry", "circular_runout", "total_runout")
GEOMETRIC_KINDS = (*FORM_KINDS, *DATUM_KINDS, "position", "line_profile", "surface_profile")


class Datum(_Strict):
    """A datum: the letter at the datum triangle and a model point on the datum face."""

    id: str
    label: str = Field(pattern=r"^[A-Z]{1,2}$")
    near: tuple[float, float, float]
    source: Source | None = None


class GeometricTolerance(_Strict):
    """A form or position tolerance from a tolerance frame.

    value is the tolerance value in mm, text the frame content as printed, datums the datum letters in frame order
    and near a model point on the toleranced face.
    """

    id: str
    kind: Literal[GEOMETRIC_KINDS]
    value: PositiveFloat
    text: str
    datums: list[str] = []
    near: tuple[float, float, float]
    source: Source | None = None
    confidence: float = Field(ge=0, le=1)


class Note(_Strict):
    """A note from the drawing, taken over unchanged."""

    id: str
    text: str
    source: Source | None


class PartSpec(_Strict):
    """All captured information of a drawing page.

    drawing is relative to /data; the first path component names the reference folder.
    """

    name: str = Field(pattern=f"^{PART_NAME.pattern}$")  # wird als Ordnername verwendet
    drawing: str
    page: int = 1
    title: str
    scale: str
    units: Literal["mm"] = "mm"
    views: list[View]
    dimensions: list[Dimension]
    features: list[Feature]
    notes: list[Note] = []
    datums: list[Datum] = []
    geometric_tolerances: list[GeometricTolerance] = []
    expected: Expected
    review: list[ReviewItem]
    general_tolerance: str | None = Field(None, pattern=r"^ISO 2768-[fmcv]$")  # aus dem Schriftfeld

    _scale = field_validator("scale")(normalize_scale)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        """Meldet alle Verstöße auf einmal und nennt jeweils die betroffene ID."""
        errors = [
            *self._check_ids(),
            *self._check_refs(),
            *self._check_review_texts(),
            *self._check_dimensions(),
            *self._check_holes(),
            *self._check_geometric(),
        ]
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def _check_ids(self) -> list[str]:
        """IDs sind über Ansichten, Maße, Merkmale, Hinweise und Prüfeinträge hinweg eindeutig."""
        groups = (self.views, self.dimensions, self.features, self.notes, self.review, self.datums, self.geometric_tolerances)
        counts = Counter(item.id for group in groups for item in group)
        return [f"Duplicate ID {i!r}." for i, n in counts.items() if n > 1]

    def _check_refs(self) -> list[str]:
        """Alle Verweise in refs, dim_ids und source.view müssen existieren."""
        errors: list[str] = []
        view_ids = {v.id for v in self.views}
        dim_ids = {d.id for d in self.dimensions}
        ref_ids = dim_ids | {item.id for item in (*self.features, *self.notes, *self.datums, *self.geometric_tolerances)}
        for r in self.review:
            errors += [f"`review` {r.id!r}: unknown reference {ref!r}." for ref in r.refs if ref not in ref_ids]
        for f in self.features:
            errors += [f"`feature` {f.id!r}: unknown `dim_id` {i!r}." for i in f.dim_ids if i not in dim_ids]
        for k, hole in enumerate(self.expected.holes):
            errors += [f"`expected.holes[{k}]`: unknown `dim_id` {i!r}." for i in hole.dim_ids if i not in dim_ids]
        for item in (*self.dimensions, *self.notes, *self.datums, *self.geometric_tolerances):
            if item.source is not None and item.source.view not in view_ids:
                kind = type(item).__name__.lower()
                errors.append(f"{kind} {item.id!r}: unknown source view {item.source.view!r}.")
        return errors

    def _check_review_texts(self) -> list[str]:
        """Ein Prüfeintrag nennt die Annahme und deren Begründung."""
        errors: list[str] = []
        for r in self.review:
            if not r.assumption.strip():
                errors.append(f"`review` {r.id!r}: `assumption` is empty.")
            if not r.reason.strip():
                errors.append(f"`review` {r.id!r}: `reason` is empty.")
        return errors

    def _check_dimensions(self) -> list[str]:
        """Prüft jedes Maß auf Prüfvermerk, Grundlage, gedruckten Wert und Toleranzen."""
        reviewed = {ref for r in self.review for ref in r.refs}
        errors: list[str] = []
        for d in self.dimensions:
            errors += _dimension_errors(d, reviewed)
        return errors

    def _check_holes(self) -> list[str]:
        """Bohrungsmerkmale brauchen erwartete Bohrungen, und jede erwartete Bohrung eine Achsrichtung."""
        errors: list[str] = []
        hole_features = [f.id for f in self.features if f.type == "hole"]
        if hole_features and not self.expected.holes:
            errors.append(f"Hole features {hole_features} require at least one entry in `expected.holes`.")
        for k, hole in enumerate(self.expected.holes):
            if not any(hole.axis):
                errors.append(f"`expected.holes[{k}]`: `axis` must not be the zero vector.")
        return errors

    def _check_geometric(self) -> list[str]:
        """Bezugsbuchstaben sind eindeutig und vorhanden; Formtoleranzen ohne, Richtungs- und Lauftoleranzen mit Bezug;
        der gedruckte Rahmen enthält den Wert; eine unsichere Lesung steht in einem Prüfeintrag."""
        errors: list[str] = []
        labels = Counter(d.label for d in self.datums)
        errors += [f"Datum {label!r} is defined more than once." for label, n in labels.items() if n > 1]
        reviewed = {ref for r in self.review for ref in r.refs}
        for t in self.geometric_tolerances:
            where = f"`geometric_tolerances` {t.id!r}"
            errors += [f"{where}: unknown datum {label!r}." for label in t.datums if label not in labels]
            if t.kind in FORM_KINDS and t.datums:
                errors.append(f"{where}: the form tolerance {t.kind} has no datum.")
            if t.kind in DATUM_KINDS and not t.datums:
                errors.append(f"{where}: {t.kind} needs at least one datum in `datums`.")
            if not _prints_value(t.text, t.value):
                errors.append(f"{where}: `text` {t.text!r} contains no number matching `value` {t.value}.")
            if t.confidence < MIN_CONFIDENCE and t.id not in reviewed:
                errors.append(f"{where}: `confidence` {t.confidence} < {MIN_CONFIDENCE}; a review item must list it in `refs`.")
        return errors


def _dimension_errors(d: Dimension, reviewed: set[str]) -> list[str]:
    """Verstöße eines einzelnen Maßes; `reviewed` enthält alle in Prüfeinträgen genannten IDs."""
    errors: list[str] = []
    # Angenommene Maße und Maße mit niedriger Konfidenz müssen geprüft werden.
    if (d.assumed or d.confidence < MIN_CONFIDENCE) and d.id not in reviewed:
        cause = "`assumed=true`" if d.assumed else f"`confidence` {d.confidence} < {MIN_CONFIDENCE}"
        errors.append(f"Dimension {d.id!r}: {cause}; a review item must list it in `refs`.")
    # Eine Annahme benötigt ihre Grundlage.
    if d.assumed and not (d.assumption_basis or "").strip():
        errors.append(f"Dimension {d.id!r}: `assumed=true` requires a non-empty `assumption_basis`.")
    # Der gedruckte Text enthält den Wert.
    if not d.assumed and not _prints_value(d.text, d.value):
        errors.append(
            f"Dimension {d.id!r}: `text` {d.text!r} contains no number matching `value` {d.value}. A calculated "
            "dimension ('50 + 50') is not printed: `assumed=true`, `assumption_basis='calculated from …'`, "
            "plus a review item."
        )
    # Abweichungen treten paarweise auf; lower <= upper.
    if (d.tol_upper is None) != (d.tol_lower is None):
        errors.append(f"Dimension {d.id!r}: `tol_upper` and `tol_lower` must be set together.")
    elif d.tol_upper is not None and d.tol_lower is not None and d.tol_lower > d.tol_upper:
        errors.append(f"Dimension {d.id!r}: `tol_lower` {d.tol_lower} is greater than `tol_upper` {d.tol_upper}.")
    # Ein Abstand braucht genau zwei Flächen.
    if d.faces_near and (d.kind != "length" or len(d.faces_near) != 2):
        errors.append(f"Dimension {d.id!r}: `faces_near` exists only for lengths and with exactly two points, one on each face.")
    return errors

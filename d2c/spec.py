"""PartSpec: what a vision model reads from one drawing page, before any geometry is built.

The validator enforces the review duty: every assumed or low-confidence dimension
must be listed in a review item, so no assumption stays silent.
"""

import re
from collections import Counter
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, model_validator

# Plain numbers in a printed dimension text, with "." or "," as decimal separator.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _prints_value(text: str, value: float) -> bool:
    """True if a number in the printed text equals value, so "76,2" cannot be read as 762."""
    return any(abs(float(n.replace(",", ".")) - value) <= 1e-9 for n in _NUMBER.findall(text))


class _Strict(BaseModel):
    # NaN or inf in a bbox or hole position would make every later comparison pass silently.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(_Strict):
    """Where a value was read: a view and a cell of the drawing frame grid."""

    view: str
    # A3: A-F/1-8, A4: A-F/1-4; null for drawings without a grid
    cell: str | None = Field(pattern=r"^[A-F][1-8]$")


class Dimension(_Strict):
    """One dimension.

    tol_upper/tol_lower are deviations in mm ("31 -0,1" -> 0 and -0.1),
    fit is an ISO 286 class such as h10 or H7.
    """

    id: str
    kind: Literal["length", "diameter", "radius", "angle", "chamfer", "thread", "count"]
    value: float
    text: str
    source: Source | None
    confidence: float = Field(ge=0, le=1)
    assumed: bool = False
    assumption_basis: str | None = None
    tol_upper: float | None = None
    tol_lower: float | None = None
    fit: str | None = Field(None, pattern=r"^[A-Za-z]{1,2}\d{1,2}$")


class View(_Strict):
    id: str
    kind: Literal["front", "top", "side", "section", "detail", "iso"]
    scale: str
    note: str = ""


class Feature(_Strict):
    id: str
    type: Literal["base", "boss", "pocket", "hole", "slot", "fillet", "chamfer", "thread", "revolve", "other"]
    dim_ids: list[str]
    note: str = ""


class ExpectedHole(_Strict):
    """A hole the finished model must have.

    d is the diameter, at a point on the hole axis, axis its direction (not the zero vector).
    """

    d: PositiveFloat
    at: tuple[float, float, float]
    axis: tuple[float, float, float]
    dim_ids: list[str]


class Expected(_Strict):
    """What the finished model must satisfy. bbox is (xlen, ylen, zlen) in model coordinates."""

    bbox: tuple[PositiveFloat, PositiveFloat, PositiveFloat]
    holes: list[ExpectedHole] = []


class ReviewItem(_Strict):
    """An assumption or open point for the human reviewer.

    mark_near: model points close to the affected faces, used for the color marks.
    """

    id: str
    refs: list[str]
    issue: Literal["missing", "ambiguous", "low_confidence", "conflict", "convention"]
    assumption: str
    reason: str
    mark_near: list[tuple[float, float, float]] = []


class Note(_Strict):
    """A note of the drawing, copied verbatim."""

    id: str
    text: str
    source: Source | None


class PartSpec(_Strict):
    """Everything read from one drawing page.

    drawing is relative to /data; its first path component names the reference folder.
    """

    name: str = Field(pattern=r"^[\w-]+$")  # becomes a folder name
    drawing: str
    page: int = 1
    title: str
    scale: str
    units: Literal["mm"] = "mm"
    views: list[View]
    dimensions: list[Dimension]
    features: list[Feature]
    notes: list[Note] = []
    expected: Expected
    review: list[ReviewItem]
    general_tolerance: str | None = Field(None, pattern=r"^ISO 2768-[fmcv]$")  # from the title block

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        """Report every violation at once, each naming the affected id."""
        errors: list[str] = []

        # (a) ids are unique across views, dimensions, features, notes and review items
        groups = (self.views, self.dimensions, self.features, self.notes, self.review)
        counts = Counter(item.id for group in groups for item in group)
        errors += [f"duplicate id {i!r}" for i, n in counts.items() if n > 1]

        # (b) every ref, dim_id and source view exists
        view_ids = {v.id for v in self.views}
        dim_ids = {d.id for d in self.dimensions}
        ref_ids = dim_ids | {item.id for item in (*self.features, *self.notes)}
        for r in self.review:
            errors += [f"review {r.id!r}: unknown ref {ref!r}" for ref in r.refs if ref not in ref_ids]
        for f in self.features:
            errors += [f"feature {f.id!r}: unknown dim_id {i!r}" for i in f.dim_ids if i not in dim_ids]
        for k, hole in enumerate(self.expected.holes):
            errors += [f"expected.holes[{k}]: unknown dim_id {i!r}" for i in hole.dim_ids if i not in dim_ids]
        for item in (*self.dimensions, *self.notes):
            if item.source is not None and item.source.view not in view_ids:
                kind = type(item).__name__.lower()
                errors.append(f"{kind} {item.id!r}: unknown source view {item.source.view!r}")

        # (e) a review item states what was assumed and why
        for r in self.review:
            if not r.assumption.strip():
                errors.append(f"review {r.id!r}: assumption is empty")
            if not r.reason.strip():
                errors.append(f"review {r.id!r}: reason is empty")

        reviewed = {ref for r in self.review for ref in r.refs}
        for d in self.dimensions:
            # (c) assumed or low-confidence dimensions are under review
            if (d.assumed or d.confidence < 0.8) and d.id not in reviewed:
                cause = "assumed=true" if d.assumed else f"confidence {d.confidence} < 0.8"
                errors.append(f"dimension {d.id!r}: {cause}, so a review item must list it in refs")
            # (d) an assumption needs its basis
            if d.assumed and not (d.assumption_basis or "").strip():
                errors.append(f"dimension {d.id!r}: assumed=true needs a non-empty assumption_basis")
            # (f) the printed text contains the value
            if not d.assumed and not _prints_value(d.text, d.value):
                errors.append(f"dimension {d.id!r}: text {d.text!r} has no number equal to value {d.value}")
            # (h) deviations come as a pair, lower <= upper
            if (d.tol_upper is None) != (d.tol_lower is None):
                errors.append(f"dimension {d.id!r}: tol_upper and tol_lower must be set together")
            elif d.tol_upper is not None and d.tol_lower is not None and d.tol_lower > d.tol_upper:
                errors.append(f"dimension {d.id!r}: tol_lower {d.tol_lower} is above tol_upper {d.tol_upper}")

        # (g) a hole feature needs a hole to check against
        hole_features = [f.id for f in self.features if f.type == "hole"]
        if hole_features and not self.expected.holes:
            errors.append(f"hole features {hole_features} need at least one entry in expected.holes")

        # an expected hole needs an axis direction
        for k, hole in enumerate(self.expected.holes):
            if not any(hole.axis):
                errors.append(f"expected.holes[{k}]: axis must not be the zero vector")

        if errors:
            raise ValueError("; ".join(errors))
        return self

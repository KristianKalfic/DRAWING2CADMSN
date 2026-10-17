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
    """Herkunft eines Werts: Ansicht und Rasterfeld der Zeichnung."""

    view: str
    # A3: A-F/1-8, A4: A-F/1-4; null for drawings without a grid
    cell: str | None = Field(pattern=r"^[A-F][1-8]$")


class Dimension(_Strict):
    """Ein Maß.

    tol_upper/tol_lower sind Abweichungen in mm ("31 -0,1" -> 0 und -0.1),
    fit ist eine ISO-286-Passungsklasse wie h10 oder H7.
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
    """Eine Bohrung, die das fertige Modell enthalten muss.

    d ist der Durchmesser, at ein Punkt auf der Bohrungsachse und axis deren Richtung (nicht der Nullvektor).
    """

    d: PositiveFloat
    at: tuple[float, float, float]
    axis: tuple[float, float, float]
    dim_ids: list[str]


class Expected(_Strict):
    """Anforderungen an das fertige Modell. bbox enthält (xlen, ylen, zlen) in Modellkoordinaten."""

    bbox: tuple[PositiveFloat, PositiveFloat, PositiveFloat]
    holes: list[ExpectedHole] = []


class ReviewItem(_Strict):
    """Eine Annahme oder offene Frage für die prüfende Person.

    mark_near: Modellpunkte nahe den betroffenen Flächen, an denen Farbmarkierungen gesetzt werden.
    """

    id: str
    refs: list[str]
    issue: Literal["missing", "ambiguous", "low_confidence", "conflict", "convention"]
    assumption: str
    reason: str
    mark_near: list[tuple[float, float, float]] = []


class Note(_Strict):
    """Ein Hinweis aus der Zeichnung, unverändert übernommen."""

    id: str
    text: str
    source: Source | None


class PartSpec(_Strict):
    """Alle erfassten Angaben einer Zeichnungsseite.

    drawing ist relativ zu /data; die erste Pfadkomponente bezeichnet den Referenzordner.
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
        errors += [f"Doppelte ID {i!r}." for i, n in counts.items() if n > 1]

        # (b) every ref, dim_id and source view exists
        view_ids = {v.id for v in self.views}
        dim_ids = {d.id for d in self.dimensions}
        ref_ids = dim_ids | {item.id for item in (*self.features, *self.notes)}
        for r in self.review:
            errors += [f"`review` {r.id!r}: Unbekannte Referenz {ref!r}." for ref in r.refs if ref not in ref_ids]
        for f in self.features:
            errors += [f"`feature` {f.id!r}: Unbekannte `dim_id` {i!r}." for i in f.dim_ids if i not in dim_ids]
        for k, hole in enumerate(self.expected.holes):
            errors += [f"`expected.holes[{k}]`: Unbekannte `dim_id` {i!r}." for i in hole.dim_ids if i not in dim_ids]
        for item in (*self.dimensions, *self.notes):
            if item.source is not None and item.source.view not in view_ids:
                kind = type(item).__name__.lower()
                errors.append(f"{kind} {item.id!r}: Unbekannte Quellansicht {item.source.view!r}.")

        # (e) a review item states what was assumed and why
        for r in self.review:
            if not r.assumption.strip():
                errors.append(f"`review` {r.id!r}: `assumption` ist leer.")
            if not r.reason.strip():
                errors.append(f"`review` {r.id!r}: `reason` ist leer.")

        reviewed = {ref for r in self.review for ref in r.refs}
        for d in self.dimensions:
            # (c) assumed or low-confidence dimensions are under review
            if (d.assumed or d.confidence < 0.8) and d.id not in reviewed:
                cause = "`assumed=true`" if d.assumed else f"`confidence` {d.confidence} < 0.8"
                errors.append(f"Maß {d.id!r}: {cause}; ein Prüfeintrag muss es in `refs` aufführen.")
            # (d) an assumption needs its basis
            if d.assumed and not (d.assumption_basis or "").strip():
                errors.append(f"Maß {d.id!r}: `assumed=true` benötigt eine nicht leere `assumption_basis`.")
            # (f) the printed text contains the value
            if not d.assumed and not _prints_value(d.text, d.value):
                errors.append(f"Maß {d.id!r}: `text` {d.text!r} enthält keine Zahl, die `value` {d.value} entspricht.")
            # (h) deviations come as a pair, lower <= upper
            if (d.tol_upper is None) != (d.tol_lower is None):
                errors.append(f"Maß {d.id!r}: `tol_upper` und `tol_lower` müssen gemeinsam gesetzt werden.")
            elif d.tol_upper is not None and d.tol_lower is not None and d.tol_lower > d.tol_upper:
                errors.append(f"Maß {d.id!r}: `tol_lower` {d.tol_lower} ist größer als `tol_upper` {d.tol_upper}.")

        # (g) a hole feature needs a hole to check against
        hole_features = [f.id for f in self.features if f.type == "hole"]
        if hole_features and not self.expected.holes:
            errors.append(f"Bohrungsmerkmale {hole_features} benötigen mindestens einen Eintrag in `expected.holes`.")

        # an expected hole needs an axis direction
        for k, hole in enumerate(self.expected.holes):
            if not any(hole.axis):
                errors.append(f"`expected.holes[{k}]`: `axis` darf nicht der Nullvektor sein.")

        if errors:
            raise ValueError("; ".join(errors))
        return self

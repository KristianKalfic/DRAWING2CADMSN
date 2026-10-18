"""Gates des Servers: Er lehnt einen Schritt einmal ab, wenn die Prüfliste oder die Messungen zeigen, dass ein Merkmal fehlt.

Jeder Befund lehnt je Bauteil genau einmal ab; derselbe Befund geht beim nächsten Versuch mit einer Warnung durch,
die in der Prüfliste steht. Die Gates erkennen nur Anzeichen (Formulierungen, Messwerte), nie die Geometrie selbst.
Reine Funktionen und der Zustand der Sitzung, ohne CadQuery und ohne Zeichnungsleser; alle Texte sind für das Modell
bestimmt und englisch.
"""

import math
import re
import unicodedata
from collections import Counter
from typing import NamedTuple

from d2c.spec import Dimension, PartSpec

MIN_CIRCLE = 1.0  # mm: kleinere Kreise sind Maßpunkte und Pfeilspitzen
CIRCLE_TOL = 0.05  # mm: so genau muss ein gemessener Kreis im Modell vorkommen
PITCH_TOL = 0.05  # mm: so genau liegt der Mittelpunkt einer Bohrung auf dem Teilkreis
PITCH_HOLES = 2  # ab so vielen kleineren Kreisen mit dem Mittelpunkt auf ihm gilt ein Kreis als Teilkreis
FILLED_DOT = 4  # ab so vielen Kreisen um denselben Mittelpunkt ist es ein gefüllter Punkt (Pfeilspitze), den die PDF aus Ringen zeichnet
THREAD_CORE = 0.75  # Anteil des Nenndurchmessers, ab dem ein Kreis die Darstellung einer Gewindebohrung sein kann
MENTION_TOL = 0.05  # mm: so genau muss eine im Prüfeintrag genannte Zahl dem Wert entsprechen
FOLLOW_TOL = 0.01  # mm: eine kleinere Änderung eines Soll-Werts zählt nicht

# Wendungen, mit denen eine Prüfliste ein weggelassenes Merkmal bekennt (Deutsch und Englisch).
OMITTED = re.compile(
    r"nicht\s+(?:\w+\s+)?(?:modell?iert|gebaut|abgebildet|umgesetzt)|vereinfach|weggelassen|ausgelassen"
    r"|not\s+(?:\w+\s+)?(?:modell?ed|built)|simplif|omitted",
    re.IGNORECASE,
)
THREAD = re.compile(r"Gewinde|thread|\bM\d", re.IGNORECASE)  # ein Gewinde als Kernloch zu bauen ist vorgesehen
CLAUSE = re.compile(r"(?<=[.;])\s+|[\n;]")  # Satzgrenze: Punkt oder Semikolon mit Leerraum, Zeilenumbruch
# Merkmale, die eine Note der Zeichnung nennen kann und die das Modell als Vorgang tragen muss.
NOTED = {
    "chamfer": re.compile(r"\bFase|chamfer|\d\s*[x×]\s*45", re.IGNORECASE),
    "fillet": re.compile(r"\bR\s*=?\s*\d|Radi(?:us|en)|Rundung|fillet", re.IGNORECASE),
    "draft": re.compile(r"Formschräge|draft|Entformung", re.IGNORECASE),
}
SCRIPT_WORDS = {"chamfer": ("chamfer",), "fillet": ("fillet", "arc", "radius"), "draft": ("taper", "draft")}
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
CLAIM = re.compile(r"\s*([A-Za-zØ])\s*=\s*(\d+(?:[.,]\d+)?)\s*")  # ein Prüfwert von measure_drawing, z. B. "d=19"
CLAIM_KIND = {"Ø": "d", "D": "d", "d": "d", "R": "r", "r": "r"}  # alle anderen Buchstaben (W, B, H, L, …) sind Längen

HINTS = {
    "omission": (
        "Build the omitted features instead of noting them: edge_finish (kind chamfer or fillet, with near points or on_plane), "
        "extrude with taper for draft angles, revolve for turned profiles, add_holes for small and cross holes."
    ),
    "circle": (
        "Add the missing round feature (add_holes, an extrude with a circle profile, a revolve step). If the circle is no feature "
        "of the part (pitch circle, other view scale, hidden line), name its diameter in a review item."
    ),
    "mismatch": (
        'Correct the dimension to the measured value, or add a review item with issue="conflict" that names the dimension in '
        "refs (submit_spec with keep_ops=true). Check first whether the dimension arrow ends at a centre line: then it is "
        "measured from the axis."
    ),
    "missing": (
        "Build the feature, or explain the printed dimension: submit_spec with keep_ops=true and a review item "
        'issue="missing" that names the dimension in refs.'
    ),
}
HINTS["follow"] = (
    "The expected values come from the drawing, not from the model. Repair the ops with edit_op or delete_op until the model meets "
    "the drawing. If the drawing was misread, correct the wrong dimension in `dimensions` (submit_spec with keep_ops=true); "
    "`expected` then follows from it."
)
RETRY = "If the feature cannot be modelled, keep the review entry and call export_step again: the second export passes with a warning in review.md."


class Finding(NamedTuple):
    """key kennzeichnet den Befund (je Bauteil nur einmal ablehnend), kind wählt den Hinweis, text sagt, was fehlt."""

    kind: str
    key: str
    text: str


def _numbers(text: str) -> list[float]:
    return [float(n.replace(",", ".")) for n in NUMBER.findall(text)]


def _mentioned(texts: list[str], value: float) -> bool:
    return any(abs(n - value) <= MENTION_TOL for text in texts for n in _numbers(text))


def _short(text: str, n: int = 90) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _claimed(kind: str, dim: Dimension) -> float | None:
    """Der Wert eines Maßes in der Art eines Prüfwerts von measure_drawing ("d" Durchmesser, "r" Radius, "L" Länge), sonst None."""
    if kind == "d":
        return {"diameter": dim.value, "radius": 2 * dim.value}.get(dim.kind)
    if kind == "r":
        return {"radius": dim.value, "diameter": dim.value / 2}.get(dim.kind)
    return dim.value if dim.kind == "length" else None


def _near(value: float | None, other: float) -> bool:
    return value is not None and abs(value - other) <= MENTION_TOL


def _round_profile(profile: object) -> bool:
    """Ob ein Profil Rundungen oder Bögen trägt."""
    if not isinstance(profile, dict):
        return False
    kind = profile.get("type")
    if kind == "rect":
        return bool(profile.get("corner_radius"))
    if kind == "path":
        return bool(profile.get("fillets")) or any(s.get("kind") in ("arc3", "radius_arc") for s in profile.get("segments", []))
    return kind in ("slot", "hull")


def _models(op: dict, feature: str) -> bool:
    """Ob die Op das Merkmal tragen kann. Großzügig: Eine Fase kann ein Punkt im Drehprofil oder eine Ecke im Pfadprofil sein."""
    kind, profile = op.get("op"), op.get("profile")
    if kind == "script":
        return any(word in op.get("code", "").lower() for word in SCRIPT_WORDS[feature])
    if feature == "draft":
        return kind == "extrude" and bool(op.get("taper"))
    if feature == "chamfer":
        return (
            (kind == "edge_finish" and op.get("kind") == "chamfer")
            or kind == "revolve"
            or (isinstance(profile, dict) and profile.get("type") == "path")
        )
    return (kind == "edge_finish" and op.get("kind") == "fillet") or _round_profile(profile)


def _admits(text: str) -> bool:
    """Ob ein Text ein weggelassenes Merkmal bekennt. Ein Satz über ein Gewinde zählt nicht: Es wird als Kernloch gebaut."""
    return any(OMITTED.search(clause) and not THREAD.search(clause) for clause in CLAUSE.split(text))


def omissions(spec: PartSpec, ops: list[dict]) -> list[Finding]:
    """Prüfeinträge, die ein weggelassenes Merkmal bekennen, und Fasen, Rundungen oder Formschrägen der Hinweise ohne passenden Vorgang."""
    found = []
    for item in spec.review:
        text = f"{item.assumption} {item.reason}"
        if _admits(text):
            found.append(
                Finding("omission", f"review:{item.id}", f'review item {item.id} admits an omitted feature: "{_short(item.assumption)}"')
            )
    for note in spec.notes:
        if _admits(note.text):
            found.append(Finding("omission", f"note:{note.id}", f'note {note.id} admits an omitted feature: "{_short(note.text)}"'))
        for feature, pattern in NOTED.items():
            if pattern.search(note.text) and not any(_models(op, feature) for op in ops):
                found.append(
                    Finding(
                        "omission",
                        f"note:{note.id}:{feature}",
                        f'note {note.id} "{_short(note.text)}" names a {feature}, but no op models it',
                    )
                )
    return found


def _g(values: list[float]) -> str:
    return "[" + ", ".join(f"{round(v, 2):g}" for v in values) + "]"


def _sizes(spec: PartSpec) -> list[tuple]:
    return sorted((d.id, d.kind, d.value) for d in spec.dimensions)


def followed_model(old: PartSpec, new: PartSpec, solid: dict | None) -> list[Finding]:
    """Soll-Werte, die ohne geändertes Maß auf die Werte des zuletzt gebauten Modells (`solid` des Berichts) gesetzt wurden:
    Hüllquader, der sich ihm nähert, und Bohrungsdurchmesser, der nun einem Durchmesser des Modells entspricht (±CIRCLE_TOL).
    Ein alter Hüllquaderwert unter jedem Längen- und Durchmessermaß ist ein Platzhalter und zählt nicht."""
    if not solid or _sizes(old) != _sizes(new):
        return []
    found = []
    box = solid["bbox"]
    built = [hi - lo for lo, hi in zip(box[:3], box[3:])]
    floor = min((d.value for d in new.dimensions if d.kind in ("length", "diameter")), default=0)
    was, now = old.expected.bbox, new.expected.bbox
    if any(abs(n - o) > FOLLOW_TOL and abs(n - b) < abs(o - b) and o >= floor for o, n, b in zip(was, now, built)):
        text = f"expected bbox changed to the built model's values without a changed dimension: {_g(was)} -> {_g(now)}, model {_g(built)}"
        found.append(Finding("follow", f"follow:bbox:{_g(was)}:{_g(now)}", text))
    circles = solid.get("circle_d") or []
    for i, (o, n) in enumerate(zip(old.expected.holes, new.expected.holes), 1):
        if o.d != n.d and any(abs(n.d - m) <= CIRCLE_TOL for m in circles) and not any(abs(o.d - m) <= CIRCLE_TOL for m in circles):
            text = f"expected hole {i} changed to the built model's value without a changed dimension: Ø{o.d:g} -> Ø{n.d:g}"
            found.append(Finding("follow", f"follow:hole{i}:{o.d:g}:{n.d:g}", text))
    return found


def pitch_circles(circles: list[dict]) -> set[int]:
    """Indizes der Kreise, auf denen die Mittelpunkte mindestens PITCH_HOLES kleinerer Kreise liegen: Teilkreise sind Mittellinien."""
    return {
        i
        for i, c in enumerate(circles)
        if sum(
            o["d_mm"] < c["d_mm"] and abs(math.dist((o["cx"], o["cy"]), (c["cx"], c["cy"])) - c["d_mm"] / 2) <= PITCH_TOL for o in circles
        )
        >= PITCH_HOLES
    }


def filled_dots(circles: list[dict]) -> set[int]:
    """Indizes der Kreise, die zu einem gefüllten Punkt gehören: mindestens FILLED_DOT Kreise um denselben Mittelpunkt (auf 0,05 mm)."""
    centres = Counter((round(c["cx"] / PITCH_TOL), round(c["cy"] / PITCH_TOL)) for c in circles)
    return {i for i, c in enumerate(circles) if centres[(round(c["cx"] / PITCH_TOL), round(c["cy"] / PITCH_TOL))] >= FILLED_DOT}


def measured_diameters(circles: list[dict]) -> list[float]:
    """Die Durchmesser der gemessenen Kreise einer Ansicht, die Merkmale sein können: ohne Maßpunkte, gefüllte Punkte, Teilkreise und Dubletten."""
    skip = pitch_circles(circles) | filled_dots(circles)
    return sorted({round(c["d_mm"], 2) for i, c in enumerate(circles) if c["d_mm"] >= MIN_CIRCLE and i not in skip}, reverse=True)


class Gates:
    """Zustand der Gates einer Sitzung: Messungen der Zeichnungen, Lesestand und die schon abgelehnten Befunde."""

    def __init__(self) -> None:
        # (Zeichnung, Seite) -> Bereich -> gemessene Durchmesser der abgefragten Ansicht
        self.circles: dict[tuple, dict[tuple, list[float]]] = {}
        self.mismatches: dict[tuple, dict[str, dict]] = {}  # (Zeichnung, Seite) -> Prüfwert -> Urteil MISMATCH
        self.bound: dict[str, tuple] = {}  # Bauteil -> (Zeichnung, Seite) seiner Spezifikation
        self.reads: dict[str, set[int]] = {}  # Zeichnung -> Seiten, die render_drawing oder read_drawing_text sahen
        self.sharp: set[str] = set()  # Zeichnungen mit einem Ausschnitt-Render in ausreichender Auflösung
        self.printed: dict[str, set[float]] = {}  # Zeichnung -> die printed_dims aller measure_drawing-Aufrufe
        self.rejected: dict[str, set[str]] = {}  # Bauteil -> Schlüssel der Befunde, die schon einmal abgelehnt haben
        self.notes: dict[str, list[str]] = {}  # Bauteil -> durchgelassene Befunde, im Export als Warnung
        self.followed: dict[str, list[str]] = {}  # Bauteil -> durchgelassene Befunde "Soll folgt dem Modell": bleiben bis zum Export

    @staticmethod
    def _drawing(path: object) -> str:
        return unicodedata.normalize("NFC", str(path))

    def _page(self, path: object, page: int) -> tuple:
        return self._drawing(path), page

    def note_read(self, path: object, page: int, sharp: bool = False) -> None:
        """Merkt, dass die Seite gerendert oder ihr Text gelesen wurde; sharp: ein Ausschnitt mit ausreichender Auflösung."""
        self.reads.setdefault(self._drawing(path), set()).add(page)
        if sharp:
            self.sharp.add(self._drawing(path))

    def pages_read(self, path: object) -> set[int]:
        return self.reads.get(self._drawing(path), set())

    def has_sharp(self, path: object) -> bool:
        return self._drawing(path) in self.sharp

    def printed_dims(self, path: object) -> set[float]:
        return self.printed.get(self._drawing(path), set())

    def note_measure(
        self, path: object, page: int, printed: list[float], view: tuple[float, ...] | None, circles: list[dict], verdicts: list[dict]
    ) -> None:
        """Merkt eine Messung: die gedruckten Werte, die Kreise der abgefragten Ansicht `view` (Bereich; None: nicht merken, etwa bei
        einer ganzen Seite mit Schriftfeld oder ohne Maßstab) und die Urteile von `expect`. Dieselbe Ansicht ersetzt ihre frühere Messung."""
        self.printed.setdefault(self._drawing(path), set()).update(printed)
        key = self._page(path, page)
        if view is not None:
            self.circles.setdefault(key, {})[tuple(view)] = measured_diameters(circles)
        open_ = self.mismatches.setdefault(key, {})
        for v in verdicts:
            if v["status"] == "MISMATCH":
                open_[v["claim"]] = v
            else:
                open_.pop(v["claim"], None)

    def bind(self, part: str, path: object, page: int) -> None:
        """Verknüpft ein Bauteil mit der Zeichnungsseite seiner Spezifikation."""
        self.bound[unicodedata.normalize("NFC", part)] = self._page(path, page)

    def _settle(self, part: str, findings: list[Finding]) -> list[Finding]:
        """Die Befunde, die zum ersten Mal ansprechen; alle werden danach als abgelehnt vermerkt."""
        part = unicodedata.normalize("NFC", part)
        seen = self.rejected.setdefault(part, set())
        fresh = [f for f in findings if f.key not in seen]
        seen.update(f.key for f in findings)
        return fresh

    def read_gates(self, part: str, hard: list[Finding], soft: list[str]) -> list[str]:
        """Die Lesegates von submit_spec. ValueError mit den Texten der Befunde, die zum ersten Mal ansprechen; sonst die durchgelassenen
        Texte (auch die Warnungen in soft), die der Export in die Prüfliste übernimmt."""
        fresh = self._settle(part, hard)
        if fresh:
            raise ValueError(
                "\n".join([*(f.text for f in fresh), *(HINTS[k] for k in dict.fromkeys(f.kind for f in fresh) if k in HINTS)])
                + "\nSubmit the spec again afterwards; in the same state it passes the second time and stays recorded as a warning."
            )
        part = unicodedata.normalize("NFC", part)
        kept = self.followed.setdefault(part, [])
        kept += [f.text for f in hard if f.kind == "follow" and f.text not in kept]
        self.notes[part] = [f.text for f in hard if f.kind != "follow"] + soft + kept
        return self.notes[part]

    def _circles(self, part: str, spec: PartSpec, ops: list[dict], report: dict) -> list[Finding]:
        """Gemessene Kreise ohne Gegenstück im Modell. Eine Gewindebohrung zeigt die Zeichnung zwischen Kern- und Nenndurchmesser."""
        model = (report.get("solid") or {}).get("circle_d")
        threads = [(THREAD_CORE * op["d"], op["d"] + CIRCLE_TOL) for op in ops if op.get("op") == "holes" and op.get("kind") == "tapped"]
        measured = sorted({d for diameters in self.circles.get(self.bound.get(part), {}).values() for d in diameters}, reverse=True)
        texts = [f"{i.assumption} {i.reason}" for i in spec.review]
        return [
            Finding("circle", f"circle:{d:g}", f"drawing circle Ø{d:g} (measured) has no counterpart in the model")
            for d in measured
            if model is not None
            and not any(abs(d - m) <= CIRCLE_TOL for m in model)
            and not any(low <= d <= high for low, high in threads)
            and not _mentioned(texts, d)
        ]

    def _mismatches(self, part: str, spec: PartSpec) -> list[Finding]:
        """Ein MISMATCH von measure_drawing, dessen Maß noch den Prüfwert trägt und von keinem Prüfeintrag `conflict` genannt ist."""
        conflicts = {ref for i in spec.review if i.issue == "conflict" for ref in i.refs}
        found = []
        for claim, verdict in self.mismatches.get(self.bound.get(part), {}).items():
            letter, number = CLAIM.fullmatch(claim).groups()
            kind, value = CLAIM_KIND.get(letter, "L"), float(number.replace(",", "."))
            dims = [d for d in spec.dimensions if _near(_claimed(kind, d), value)]
            if dims and not any(
                d.id in conflicts for d in dims
            ):  # Ein auf den gemessenen Wert korrigiertes Maß hat nicht mehr den Prüfwert.
                text = (
                    f"measure_drawing could not confirm {claim}: nearest measured value {verdict['measured_mm']:g} mm "
                    f"(deviation {verdict['deviation_mm']:+g} mm), but dimension {', '.join(d.id for d in dims)} is neither corrected "
                    'nor named by a review item with issue="conflict"'
                )
                found.append(Finding("mismatch", f"mismatch:{claim}", text))
        return found

    @staticmethod
    def _unexplained(spec: PartSpec, report: dict) -> list[Finding]:
        """Gedruckte Durchmesser und Radien, die im Modell fehlen (dims_found.missing) und kein Prüfeintrag erklärt: weder per id in `refs`
        noch mit dem Wert im Text. Längen zählen nicht: Nutlängen, Senktiefen und Fasenschenkel sind oft keine Fläche des Modells."""
        kinds = {d.id: d.kind for d in spec.dimensions}
        missing = [m for m in (report.get("dims_found") or {}).get("missing") or [] if kinds.get(m["id"]) in ("diameter", "radius")]
        refs = {ref for item in spec.review for ref in item.refs}
        texts = [f"{item.assumption} {item.reason}" for item in spec.review]
        return [
            Finding(
                "missing",
                f"missing:{m['id']}",
                f"printed dimension {m['id']} ({m['text']}) is not found in the model and no review item explains it",
            )
            for m in missing
            if m["id"] not in refs and not _mentioned(texts, m["value"])
        ]

    def screen(self, part: str, spec: PartSpec, ops: list[dict], report: dict) -> tuple[dict | None, list[str]]:
        """Die Gates des Nennexports: (Ablehnung, Warnungen). Ein Befund lehnt je Bauteil einmal ab ({"ok": False, "error", "next"});
        der Export, der danach durchgeht, nennt alle jetzigen Befunde und die der Lesegates als Warnung."""
        part = unicodedata.normalize("NFC", part)
        findings = [
            *omissions(spec, ops),
            *self._circles(part, spec, ops, report),
            *self._mismatches(part, spec),
            *self._unexplained(spec, report),
        ]
        fresh = self._settle(part, findings)
        warnings = [*self.notes.get(part, []), *(f.text for f in findings)]
        if not fresh:
            return None, warnings
        refusal = {
            "ok": False,
            "error": "Export refused, the model probably lacks features: " + "; ".join(f.text for f in fresh) + ".",
            "next": [HINTS[k] for k in dict.fromkeys(f.kind for f in fresh)] + [RETRY],
        }
        return refusal, warnings

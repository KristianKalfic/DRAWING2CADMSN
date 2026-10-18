"""Aus der Zeichnung gelesene Maße und den Maßstab mit der gemessenen Geometrie abgleichen."""

import collections
import math
import re
from collections.abc import Sequence
from os import PathLike

from d2c.drawing.vector.views import extract

STANDARD_SCALES = (0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 4.0, 5.0, 10.0, 20.0)
VERIFY_TOL = 0.05  # mm: größte Abweichung, bis zu der ein Prüfwert als bestätigt gilt
_PARALLEL_TOL = 0.05  # Grad: Linien mit kleinerer Winkeldifferenz gelten als parallel
_MIN_OFFSET = 1e-3  # mm: kleinere Abstände sind Rundung, kein Maß

Candidates = dict[str, list[tuple[float, str]]]  # Art (d, r, L) -> (Wert in mm, Herkunft)


def _round_candidates(res: dict, pool: Candidates) -> None:
    for c in res["circles"]:
        where = f"Circle at ({c['cx']},{c['cy']})"
        pool["d"].append((c["d_mm"], where))
        pool["r"].append((round(c["d_mm"] / 2, 4), where))
    for a in res["arcs"]:
        where = f"Arc {a['sweep']} degrees at ({a['cx']},{a['cy']})"
        pool["r"].append((a["r_mm"], where))
        pool["d"].append((round(2 * a["r_mm"], 4), f"{where} (2r)"))


def _edge_candidates(res: dict, pool: Candidates) -> None:
    for ln in res["lines"]:
        pool["L"].append((ln["length_mm"], f"Line ({ln['x0']},{ln['y0']})-({ln['x1']},{ln['y1']})"))
    for c in res["contours"]:
        x, y = c["bbox_mm"][:2]
        pool["L"].append((c["w_mm"], f"Contour width @({x},{y})"))
        pool["L"].append((c["h_mm"], f"Contour height @({x},{y})"))
    if res["extent_mm"]:
        pool["L"].append((res["extent_mm"][0], "Overall width"))
        pool["L"].append((res["extent_mm"][1], "Overall height"))


def _centre_candidates(res: dict, pool: Candidates) -> None:
    """Abstände zwischen Mittelpunkten sowie zwischen einem Mittelpunkt und einer mindestens 3 mm langen Linie."""
    centres = sorted({(p["cx"], p["cy"]) for p in res["circles"] + res["arcs"]})
    for i, a in enumerate(centres):
        for b in centres[i + 1 :]:
            dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
            for v, name in ((dx, "dx"), (dy, "dy"), (math.hypot(dx, dy), "dist")):
                if v > _MIN_OFFSET:
                    pool["L"].append((round(v, 4), f"Center distance {name} {a}-{b}"))
    for cx, cy in centres:
        for ln in res["lines"]:
            if ln["length_mm"] < 3:
                continue
            dx, dy = ln["x1"] - ln["x0"], ln["y1"] - ln["y0"]
            off = abs(dx * (cy - ln["y0"]) - dy * (cx - ln["x0"])) / math.hypot(dx, dy)
            if off > _MIN_OFFSET:
                line = f"({ln['x0']},{ln['y0']})-({ln['x1']},{ln['y1']})"
                pool["L"].append((round(off, 4), f"Distance from center ({cx},{cy}) to line {line}"))


def _parallel_candidates(res: dict, pool: Candidates) -> None:
    """Abstand zwischen parallelen Linien, die länger als 1 mm sind."""
    lines = [ln for ln in res["lines"] if ln["length_mm"] > 1]
    for i, a in enumerate(lines):
        for b in lines[i + 1 :]:
            if abs((a["angle_deg"] - b["angle_deg"] + 90) % 180 - 90) < _PARALLEL_TOL:
                ang = math.radians(a["angle_deg"])
                dx, dy = b["x0"] - a["x0"], b["y0"] - a["y0"]
                off = abs(-dx * math.sin(ang) + dy * math.cos(ang))
                if off > _MIN_OFFSET:
                    lengths = f"{a['length_mm']}/{b['length_mm']}"
                    pool["L"].append((round(off, 4), f"Distance between parallel lines (lengths {lengths})"))


def _candidates(res: dict) -> Candidates:
    """Alle Durchmesser (d), Radien (r) und Längen (L), die sich aus der Geometrie messen lassen: Kanten, Konturmaße, Mittelpunktabstände sowie Abstände zwischen Mittelpunkten und Linien oder zwischen Linien."""
    pool: Candidates = {"d": [], "r": [], "L": []}
    _round_candidates(res, pool)
    _edge_candidates(res, pool)
    _centre_candidates(res, pool)
    _parallel_candidates(res, pool)
    return pool


_CLAIM = re.compile(r"\s*([drLØDRWBHTAwbhtal])\s*=\s*(\d+(?:[.,]\d+)?)\s*")
# Übliche Kürzel kleiner Modelle: Breite, Höhe, Dicke usw. sind Längen, Ø und D Durchmesser, R Radien.
_CLAIM_KIND = {"Ø": "d", "D": "d", "R": "r", **dict.fromkeys("WBHTAwbhtal", "L")}


def _parse_claim(claim: str) -> tuple[str, float]:
    m = _CLAIM.fullmatch(claim)
    if not m:
        raise ValueError(f"`claim` must have the format 'd=19', 'r=10' or 'L=49'; received: {claim!r}.")
    return _CLAIM_KIND.get(m[1], m[1]), float(m[2].replace(",", "."))


def verify(res: dict, claims: Sequence[str], tol: float = VERIFY_TOL) -> list[dict]:
    """Prüft aus der Zeichnung gelesene Maße anhand der mit extract gemessenen Geometrie.

    Ein Prüfwert lautet in mm "d=19" (Durchmesser), "r=10" (Radius) oder "L=49" (Länge); ein Dezimalkomma ist
    zulässig. Je Prüfwert werden der nächstliegende Messwert (measured_mm, deviation_mm = Messwert - Sollwert,
    matched = Herkunft), der Status "ok" innerhalb tol, "MISMATCH" außerhalb oder "no_candidates", wenn es
    keinen Messwert dieser Art gibt, sowie n_candidates_within_tol zurückgegeben. Eine dichte Ansicht enthält viele
    Längen; ein L-Maß kann dort zufällig passen. Dafür den Bereich auf die Ansicht begrenzen.
    """
    pool = _candidates(res)
    verdicts = []
    for claim in claims:
        kind, value = _parse_claim(claim)
        found = pool[kind]
        verdict = {
            "claim": claim,
            "status": "no_candidates",
            "measured_mm": None,
            "deviation_mm": None,
            "matched": None,
            "n_candidates_within_tol": 0,
        }
        if found:
            measured, matched = min(found, key=lambda c: abs(c[0] - value))
            verdict.update(
                status="ok" if abs(measured - value) <= tol else "MISMATCH",
                measured_mm=measured,
                deviation_mm=round(measured - value, 4),
                matched=matched,
                n_candidates_within_tol=sum(abs(v - value) <= tol for v, _ in found),
            )
        verdicts.append(verdict)
    return verdicts


def _standard(scale: float) -> float | None:
    """Der Standardmaßstab innerhalb von 0,5 % von scale, falls vorhanden."""
    near = min(STANDARD_SCALES, key=lambda t: abs(t - scale) / t)
    return near if abs(near - scale) / near < 0.005 else None


def suggest_scale(
    pdf_path: str | PathLike[str],
    printed_values: Sequence[float],
    page: int = 1,
    region: Sequence[float] | None = None,
    top: int | None = 5,
) -> list[dict]:
    """Ermittelt anhand der aufgedruckten Maße den tatsächlichen Maßstab einer Zeichnung und macht einen falschen Maßstab im Schriftfeld sichtbar.

    Jede gemessene Größe der Seite (in Blatt-mm), geteilt durch einen gedruckten Wert, ergibt einen möglichen
    Maßstab. Jeder gedruckte Wert gibt einmal je unterstütztem Maßstab eine Stimme ab. Mindestens sechs Maße aus den
    Ansichten angeben: Bei wenigen Werten lassen sich Zufallstreffer nicht vom tatsächlichen Maßstab unterscheiden.

    Gibt höchstens `top` Wörterbücher {"scale", "votes", "standard"} zurück, sortiert nach den meisten Stimmen,
    danach nach einem Standardmaßstab vor einem nicht standardisierten Wert und schließlich nach dem Verhältnis
    am nächsten zu 1:1 (verwandte Maßreihen können zwei Verhältnisse mit allen Stimmen ergeben, z. B.
    0.3 = 0.4 * 0.75). "standard" ist der Standardmaßstab innerhalb von 0,5 % von "scale", andernfalls None:
    Ein Maßstab von 0.75 muss als nicht standardisierter Wert sichtbar bleiben. `top=None` gibt jedes Verhältnis zurück.
    """
    if any(v <= 0 for v in printed_values):
        raise ValueError(f"`printed_values` must be positive; received: {list(printed_values)}.")
    pool = _candidates(extract(pdf_path, page, region))
    drawn = [v for kind in ("d", "r", "L") for v, _ in pool[kind]]
    votes: collections.Counter[float] = collections.Counter()
    for printed in printed_values:
        votes.update({round(v / printed, 3) for v in drawn if 0.03 < v / printed < 30})
    ranked = [{"scale": s, "votes": n, "standard": _standard(s)} for s, n in votes.items()]
    ranked.sort(key=lambda e: (-e["votes"], e["standard"] is None, abs(math.log(e["scale"])), e["scale"]))
    return ranked[:top]

"""Bögen eines path-Profils werden auf den Radius der Spezifikation gelegt."""

import math

from d2c.quality.arcs import snap_arcs


def path(*segments, start=(76.0, 41.0)) -> dict:
    return {"op": "extrude", "profile": {"type": "path", "start": list(start), "segments": list(segments)}, "distance": 5}


def radius(start, via, end) -> float:
    (ax, ay), (bx, by), (cx, cy) = start, via, end
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    return math.dist((ux, uy), start)


def test_rounded_via_point_is_moved_onto_the_spec_radius():
    """Ein auf 0,01 mm gerundeter Zwischenpunkt eines Viertelbogens R35 ergibt R34,995; danach liegt er auf R35."""
    op = path({"kind": "arc3", "via": [86.25, 65.75], "to": [111.0, 76.0]}, {"kind": "line", "to": [111.0, 41.0]})
    assert abs(radius((76, 41), (86.25, 65.75), (111, 76)) - 35) > 0.002
    snapped, notes = snap_arcs(op, [35.0, 76.0])
    via = snapped["profile"]["segments"][0]["via"]
    assert abs(radius((76, 41), via, (111, 76)) - 35) < 1e-5
    assert math.dist(via, (86.25, 65.75)) < 0.01  # dieselbe Seite, kaum verschoben
    assert snapped["profile"]["segments"][1] == op["profile"]["segments"][1]
    assert notes == ["profile.segments.0: arc radius 34.9957 → 35 (radius of the spec)"]
    assert op["profile"]["segments"][0]["via"] == [86.25, 65.75]  # die Eingabe bleibt unverändert


def test_arcs_far_from_any_spec_radius_and_exact_arcs_stay():
    op = path({"kind": "arc3", "via": [86.25, 65.75], "to": [111.0, 76.0]})
    assert snap_arcs(op, [30.0]) == (op, [])
    exact = path({"kind": "arc3", "via": [111 - 35 / math.sqrt(2), 41 + 35 / math.sqrt(2)], "to": [111.0, 76.0]})
    assert snap_arcs(exact, [35.0]) == (exact, [])


def test_large_arc_keeps_its_side():
    """Ein Bogen über 270 Grad behält seinen Mittelpunkt: der Zwischenpunkt bleibt auf der fernen Seite des Kreises R10."""
    op = path({"kind": "arc3", "via": [-7.08, -7.08], "to": [0.0, 10.0]}, start=(10.0, 0.0))
    snapped, notes = snap_arcs(op, [10.0])
    via = snapped["profile"]["segments"][0]["via"]
    assert notes and abs(math.hypot(*via) - 10) < 1e-5 and via[0] < 0 and via[1] < 0


def test_other_profiles_and_malformed_input_are_left_to_validation():
    rect = {"op": "extrude", "profile": {"type": "rect", "w": 10, "h": 5}}
    assert snap_arcs(rect, [5.0]) == (rect, [])
    broken = path({"kind": "arc3", "via": "x", "to": [1, 2]})
    assert snap_arcs(broken, [5.0]) == (broken, [])

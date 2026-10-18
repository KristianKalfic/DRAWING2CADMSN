"""Standard-Toleranztabellen aus öffentlichen Normwerten, falls D2C_TABLES keine Datei bereitstellt.

ISO 2768-1 (Allgemeintoleranzen für Längenmaße, Klassen f, m, c, v) und ISO 286-1 (Grenzabmaße für Nennmaße bis 500 mm:
H6 bis H11, h6 bis h11, g6, js6, k6, m6, n6, p6, r6). Die Zeilen entsprechen dem Format der Tabellendateien in D2C_TABLES
(<value over|from=... to|up_to=... upper=... lower=.../>), sodass `d2c.quality.tolerances` sie wie eine eingelesene Datei liest.
"""

import xml.etree.ElementTree as ET
from functools import cache

# Beschriftung der Tabellen der Allgemeintoleranzdatei je Klasse im Schriftfeld
GENERAL_ENTRIES = {
    "ISO 2768-f": "DIN ISO 2768 (fine/fein)",
    "ISO 2768-m": "DIN ISO 2768 (medium/mittel)",
    "ISO 2768-c": "DIN ISO 2768 (coarse/grob)",
    "ISO 2768-v": "DIN ISO 2768 (very coarse/sehr grob)",
}

# ISO 2768-1, Tabelle 1: zulässige Abweichung (±, mm) je Nennmaßbereich; None = in der Norm nicht festgelegt
_GENERAL_RANGES = [(0.5, 3), (3, 6), (6, 30), (30, 120), (120, 400), (400, 1000), (1000, 2000), (2000, 4000)]
_GENERAL = {
    "f": [0.05, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, None],
    "m": [0.1, 0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2],
    "c": [0.2, 0.3, 0.5, 0.8, 1.2, 2, 3, 4],
    "v": [None, 0.5, 1, 1.5, 2.5, 4, 6, 8],
}

# ISO 286-1: Nennmaßbereiche (über, bis) in mm, Toleranzgrade IT in µm und Grundabmaße von Wellen in µm
_RANGES = [
    (0, 3),
    (3, 6),
    (6, 10),
    (10, 18),
    (18, 30),
    (30, 50),
    (50, 80),
    (80, 120),
    (120, 180),
    (180, 250),
    (250, 315),
    (315, 400),
    (400, 500),
]
_IT = {
    6: [6, 8, 9, 11, 13, 16, 19, 22, 25, 29, 32, 36, 40],
    7: [10, 12, 15, 18, 21, 25, 30, 35, 40, 46, 52, 57, 63],
    8: [14, 18, 22, 27, 33, 39, 46, 54, 63, 72, 81, 89, 97],
    9: [25, 30, 36, 43, 52, 62, 74, 87, 100, 115, 130, 140, 155],
    10: [40, 48, 58, 70, 84, 100, 120, 140, 160, 185, 210, 230, 250],
    11: [60, 75, 90, 110, 130, 160, 190, 220, 250, 290, 320, 360, 400],
}
_G_UPPER = [-2, -4, -5, -6, -7, -9, -10, -12, -14, -15, -17, -18, -20]  # g: oberes Abmaß es
_LOWER = {  # k, m, n, p: unteres Abmaß ei (k6 gilt für IT4 bis IT7)
    "k": [0, 1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4, 5],
    "m": [2, 4, 6, 7, 8, 9, 11, 13, 15, 17, 20, 21, 23],
    "n": [4, 8, 10, 12, 15, 17, 20, 23, 27, 31, 34, 37, 40],
    "p": [6, 12, 15, 18, 22, 26, 32, 37, 43, 50, 56, 62, 68],
}
# r hat feinere Bereiche als die übrigen Klassen: (über, bis, ei)
_R_LOWER = [
    (0, 3, 10),
    (3, 6, 15),
    (6, 10, 19),
    (10, 18, 23),
    (18, 30, 28),
    (30, 50, 34),
    (50, 65, 41),
    (65, 80, 43),
    (80, 100, 51),
    (100, 120, 54),
    (120, 140, 63),
    (140, 160, 65),
    (160, 180, 68),
    (180, 200, 77),
    (200, 225, 80),
    (225, 250, 84),
    (250, 280, 94),
    (280, 315, 98),
    (315, 355, 108),
    (355, 400, 114),
    (400, 450, 126),
    (450, 500, 132),
]


def _num(x: float) -> str:
    return f"{round(x, 6):g}"


def _row(parent: ET.Element, over: str, to: str, lower: float, upper: float) -> None:
    ET.SubElement(parent, "value", {"over": over, "to": to, "upper": _num(upper), "lower": _num(lower)})


def _it(grade: int, hi: float) -> float:
    """IT-Wert in mm für den Bereich, der bis `hi` reicht."""
    return _IT[grade][next(i for i, (_, to) in enumerate(_RANGES) if to >= hi)] / 1000


@cache
def toltabs() -> ET.Element:
    """ISO 2768-1 im Format der Allgemeintoleranzdatei (`*toltabs*.xml`)."""
    root = ET.Element("TOLTABS")
    for letter, values in _GENERAL.items():
        table = ET.SubElement(root, "toltable", {"entry": GENERAL_ENTRIES[f"ISO 2768-{letter}"]})
        rows = ET.SubElement(table, "LIN_TOLTAB")
        ET.SubElement(rows, "value", {"from": "0", "up_to": "0.5", "upper": "0", "lower": "0"})
        for k, ((over, to), dev) in enumerate(zip(_GENERAL_RANGES, values)):
            if dev is not None:
                bound = {"from": _num(over)} if k == 0 else {"over": _num(over)}
                ET.SubElement(rows, "value", {**bound, "to": _num(to), "upper": _num(dev), "lower": _num(-dev)})
    return root


@cache
def isolim() -> ET.Element:
    """ISO 286-1 im Format der Grenzmaßdatei (`*isolim*.xml`): ein Element je Passung, eine Zeile je Nennmaßbereich."""
    root = ET.Element("ISOLIM")

    def add(fit: str, spans: list[tuple[float, float]], deviations) -> None:
        table = ET.SubElement(root, fit)
        for (over, to), (lower, upper) in zip(spans, deviations):
            _row(table, _num(over), _num(to), lower, upper)

    for grade in _IT:
        add(f"H{grade}", _RANGES, [(0, _it(grade, to)) for _, to in _RANGES])
        add(f"h{grade}", _RANGES, [(-_it(grade, to), 0) for _, to in _RANGES])
    it6 = [_it(6, to) for _, to in _RANGES]
    add("g6", _RANGES, [(es / 1000 - it, es / 1000) for es, it in zip(_G_UPPER, it6)])
    add("js6", _RANGES, [(-it / 2, it / 2) for it in it6])
    for letter, ei in _LOWER.items():
        add(f"{letter}6", _RANGES, [(e / 1000, e / 1000 + it) for e, it in zip(ei, it6)])
    add("r6", [(a, b) for a, b, _ in _R_LOWER], [(e / 1000, e / 1000 + _it(6, b)) for _, b, e in _R_LOWER])
    return root

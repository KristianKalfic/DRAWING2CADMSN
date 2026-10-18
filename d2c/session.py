"""Vorgangssitzungen: je Bauteil eine PartSpec und Vorgangsliste, im Speicher und unter results/<name>/ abgelegt."""

import functools
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args

from d2c.cad.export import measure_step, missing_assumed_pmi, read_pmi
from d2c.cad.ops import NO_EFFECT
from d2c.cad.sandbox import run_worker
from d2c.config import RUNS, VERSIONS, results_root
from d2c.quality import review, revision
from d2c.quality.arcs import snap_arcs
from d2c.quality.gates import Gates
from d2c.quality.tolerances import ToleranceError, Variant, apply_mode, compliance, limits, mid_value, unbound
from d2c.spec import LINEAR_TOL, PART_NAME, PartSpec

log = logging.getLogger(__name__)


DIAMETER_MATCH_TOL = 1e-6  # mm: So nah gilt ein Bohrdurchmesser als der Nenndurchmesser eines Gewindemaßes
# Prüfungen am Körper selbst: Ihr Scheitern ist nie ein Zwischenstand, sondern ein Fehler im Aufbau.
BODY_CHECKS = frozenset(
    {
        "Valid solid",
        "Number of solids",
        "Free edges",
        "Volume mm³",
        "STEP re-import valid",
        "Solids in STEP re-import",
        "Volume change on STEP re-import",
    }
)
MID_FILES = ("{part}_mid.step", "report_mid.json")  # Dateien, die ein Export auf der Toleranzmitte schreibt
NOMINAL_FILES = ("{part}.step", "{part}.glb", "marks.json", "pmi.json", "report.json", "review.md", "review.json")
KEPT_IN_PLACE = (RUNS, VERSIONS)  # bleiben liegen, wenn ein Teil neu begonnen wird
CHANGES_FILE = "changes.json"  # Änderungsliste der laufenden Revision: {"previous": <Version>, "changes": [...]}


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _per_part(method):
    """Serialisiert Aufrufe für dasselbe Teil (Groß- und Kleinschreibung zählt nicht, auf macOS teilen sich beide den Ordner):
    Die MCP-Bibliothek führt Werkzeuge in Threads aus, und zwei Läufe dürfen eine Vorgangsliste nicht gegenseitig überschreiben."""

    @functools.wraps(method)
    def wrapper(self, subject, *args, **kwargs):
        name = subject.name if isinstance(subject, PartSpec) else str(subject)
        with self._locks.setdefault(unicodedata.normalize("NFC", name).casefold(), threading.RLock()):
            return method(self, subject, *args, **kwargs)

    return wrapper


def _plain_thread_holes(spec: PartSpec, ops: list[dict]) -> list[dict]:
    """build_notes für glatte Bohrungen, die mit dem Nenndurchmesser eines Gewindemaßes der Spezifikation gebohrt wurden."""
    threads = [d for d in spec.dimensions if d.kind == "thread"]
    return [
        {
            "op_index": i,
            "warning": f"The plain hole d={op['d']:g} has the nominal diameter of the thread dimension "
            f"{d.text!r} ({d.id}): a thread is add_holes with kind='tapped' and thread, "
            "not a plain hole",
        }
        for i, op in enumerate(ops)
        if op.get("op") == "holes" and op.get("kind", "plain") == "plain"
        for d in threads
        if abs(op["d"] - d.value) < DIAMETER_MATCH_TOL
    ]


def _tolerance_rows(spec: PartSpec, ops: list[dict], nominal: list[dict], geometry: dict | None = None) -> list[dict]:
    """Die Zeilen der Toleranztabelle der Prüfliste: die Einträge der Einhaltung am Nennmaß, die Toleranzmitte jedes
    Maßes und ob ein Modell auf der Mitte seine Toleranz hält. Dieses Urteil stammt aus den Werten, die die ops auf
    der Mitte erhalten; die Bohrungen zu messen bleibt dem Bericht des Mitte-Exports überlassen. Ein Maß ohne gebundenen
    Wert beurteilt `geometry` (export.measure_geometry des exportierten Körpers); weil nur gebundene Parameter auf die Mitte
    wandern, ist das Mitte-Modell dafür derselbe Körper. "geometry" kennzeichnet ein Urteil am Nennmaß, das vom gemessenen
    Körper statt von einem gebundenen Vorgangswert stammt."""
    dims = {d.id: d for d in spec.dimensions}
    on_mid = {c["dim_id"]: c["in_tolerance"] for c in compliance(spec, ops, {}, "mid", geometry)}
    return [
        {
            "dim_id": c["dim_id"],
            "text": c["text"],
            "nominal": c["nominal"],
            "lower": c["lower"],
            "upper": c["upper"],
            "mid": mid_value(dims[c["dim_id"]], spec.general_tolerance),
            "source": c["source"],
            "in_tolerance_nominal": c["in_tolerance"],
            "in_tolerance_mid": on_mid[c["dim_id"]],
            "geometry": c["geometry"],
        }
        for c in nominal
    ]


def _stored_error_index(report: dict, order: list[int]) -> dict:
    """Übersetzt den Fehlerindex einer verworfenen Änderung in den Index der unveränderten, gespeicherten Liste.

    `order[j]` ist der gespeicherte Index der Op an Stelle j der geänderten Liste. Ohne Übersetzung zeigte der
    Hinweis „edit_op(index=j)“ auf eine andere Op als die fehlerhafte.
    """
    found = re.search(r"\bop (\d+)", report.get("error") or "")
    if not found or int(found[1]) >= len(order):
        return report
    stored = order[int(found[1])]
    error = report["error"][: found.start()] + f"op {stored}" + report["error"][found.end() :]
    return report | {"error": f"{error} (index in the stored list; the change was not applied)", "op_index": stored}


def _flipped(op: dict) -> tuple[dict, str] | None:
    """Die Variante einer Op mit umgekehrter Richtung samt Hinweistext, oder None, wenn die Op keine hat."""
    if op.get("op") == "holes":
        flip = not op.get("flip", False)
        return op | {"flip": flip}, f"flip={str(flip).lower()}, there was no material in the drilling direction"
    if op.get("op") == "extrude" and op.get("mode") == "cut" and not op.get("both", False):
        return op | {"distance": -op["distance"]}, f"distance={-op['distance']:g}, the cut was outside the material"
    return None


def _same_op(a: dict, b: dict) -> bool:
    """Gleiche Vorgänge, unabhängig davon, ob ein Wert als Tupel (frisch validiert) oder Liste (von Datenträger) vorliegt."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _no_effect(report: dict, op_index: int) -> bool:
    return any(n.get("op_index") == op_index and n.get("warning") == NO_EFFECT for n in report["build_notes"])


def _export_warnings(spec: PartSpec, ops: list[dict], variant: str, skipped: list[dict], missing: list[str]) -> list[str]:
    """Hinweise des Exports: angenommene Maße ohne PMI, nicht verschobene Bindungen, tolerierte Maße ohne Bindung, fehlende Tabellenwerte."""
    texts = []
    if missing:
        texts.append(f"{len(missing)} assumed dimensions without PMI in the STEP file: {', '.join(missing)}")
    texts += [f"Binding {s['path']} (op {s['op_index']}) to {s['dim_id']} not shifted: {s['reason']}" for s in skipped]
    loose = unbound(spec, ops, skipped)
    if loose:
        what = "not shifted" if variant == "mid" else 'not shifted with variant="mid"'
        texts.append(f"{len(loose)} toleranced dimensions without binding, {what}: {', '.join(loose)}")
    for dim in spec.dimensions:
        try:
            limits(dim, spec.general_tolerance)
        except ToleranceError as e:
            texts.append(f"Tolerance of {dim.id} cannot be determined, dimension not checked: {e}")
    return texts


def build_spec(spec: PartSpec, ops: list[dict], **export: object) -> dict:
    """Baut die ops gegen `spec` in einem Worker; `export` fügt Schlüssel zur Nutzlast hinzu (export_path, glb_path, pmi_spec, tol)."""
    known = {d.id for d in spec.dimensions}
    for op in ops:
        if not isinstance(op, dict) or not isinstance(op.get("refs", {}), dict):
            raise ValueError('`refs` must be an object mapping op parameter paths to dimension ids, e.g. {"d": "d1"}.')
        for dim_id in op.get("refs", {}).values():
            if dim_id not in known:
                raise ValueError(f"Unknown dimension id '{dim_id}' in `refs`.")
    return run_worker(
        {
            "ops": ops,
            "expected": spec.expected.model_dump(mode="json"),
            "dimensions": [d.model_dump(mode="json", include={"id", "kind", "value", "text", "assumed"}) for d in spec.dimensions],
            "marks": [{"id": r.id, "near": r.mark_near} for r in spec.review if r.mark_near],
            "export_path": None,
        }
        | export
    )


class Session:
    """Spezifikationen, Vorgangslisten und letzte Aufbau-Berichte der Bauteile in diesem Serverlauf samt der gemessenen Zeichnungsseiten."""

    def __init__(self) -> None:
        self.specs: dict[str, PartSpec] = {}
        self.ops: dict[str, list[dict]] = {}
        self.last: dict[str, dict] = {}
        self.measured: set[tuple[str, int]] = set()
        self.gates = Gates()
        self._locks: dict[str, threading.RLock] = {}

    @staticmethod
    def _page_key(path: Path, page: int) -> tuple[str, int]:
        return unicodedata.normalize("NFC", str(path)), page

    def note_measured(self, path: Path, page: int) -> None:
        """Merkt, dass measure_drawing auf dieser Seite der (aufgelösten) Zeichnung lief."""
        self.measured.add(self._page_key(path, page))

    def was_measured(self, path: Path, page: int) -> bool:
        return self._page_key(path, page) in self.measured

    def _folder(self, part: str) -> Path:
        return results_root() / part

    def _load(self, part: str) -> str:
        """Normalisierter Name eines bekannten Bauteils; ein nur auf Datenträger vorhandenes Bauteil wird zuerst geladen."""
        part = unicodedata.normalize("NFC", part)
        if not PART_NAME.fullmatch(part):
            raise KeyError(f"Unknown part '{part}'; call `submit_spec` first.")
        if part not in self.specs:
            folder = self._folder(part)
            if not (folder / "spec.json").is_file():
                raise KeyError(f"Unknown part '{part}'; call `submit_spec` first.")
            self.specs[part], self.ops[part] = self._read(folder)
        return part

    @staticmethod
    def _read(folder: Path) -> tuple[PartSpec, list[dict]]:
        """Spezifikation und Vorgangsliste aus dem Bauteilordner; eine nicht lesbare Datei wird in der Fehlermeldung genannt."""
        name = "spec.json"
        try:
            spec = PartSpec.model_validate_json((folder / name).read_text(encoding="utf-8"))
            name = "ops.json"
            ops_file = folder / name
            return spec, json.loads(ops_file.read_text(encoding="utf-8")) if ops_file.is_file() else []
        except ValueError as e:
            raise ValueError(f"{folder.name}/{name} is not readable ({e}); call `submit_spec` again.") from e

    def _archive(self, part: str) -> str | None:
        """Legt den Stand eines vorhandenen Teils unter versions/<Zeitstempel>/ ab, bevor ein neuer begonnen wird; liefert den Namen
        der Version, None wenn nichts abzulegen war.

        Verschoben wird alles außer den Laufprotokollen und früheren Versionen. Ein Teil ohne STEP hat nichts
        Fertiges: Dort werden nur übrig gebliebene Ergebnisdateien entfernt, damit sie kein altes Modell vortäuschen.
        """
        folder = self._folder(part)
        if not folder.is_dir():
            return None
        old = [p for p in folder.iterdir() if p.name not in KEPT_IN_PLACE]
        if any(p.name in (f"{part}.step", f"{part}_mid.step") for p in old):
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
            target = folder / VERSIONS / stamp
            for n in range(2, 100):  # zwei Neubeginne in derselben Sekunde
                if not target.exists():
                    break
                target = folder / VERSIONS / f"{stamp}-{n}"
            target.mkdir(parents=True)
            for path in old:
                path.rename(target / path.name)
            log.info("State of '%s' archived to %s", part, target.relative_to(folder))
            return target.name
        for name in NOMINAL_FILES + MID_FILES:
            (folder / name.format(part=part)).unlink(missing_ok=True)
        return None

    def _revise(self, part: str, old: PartSpec, spec: PartSpec) -> tuple[list[dict], str | None] | None:
        """Die Änderungsliste einer neuen Spezifikation gegenüber der vorigen Revision und deren Versionsname; None ohne Revision.

        Eine Revision gibt es, wenn das Teil schon ein exportiertes Modell hat. Ändert sich dabei etwas Erfasstes, wandert der
        Stand zuerst nach versions/ (Rev A bleibt erhalten), und die Liste steht in changes.json. Bis zum nächsten Export gilt
        eine Revision als offen: Weitere Einreichungen werden gegen die abgelegte Version verglichen, nicht gegen den Zwischenstand.
        """
        folder = self._folder(part)
        previous, baseline = None, old
        if not (folder / f"{part}.step").is_file():  # offene Revision? Dann steht der Ausgangsstand unter versions/
            try:
                previous = json.loads((folder / CHANGES_FILE).read_text(encoding="utf-8"))["previous"]
                baseline = PartSpec.model_validate_json((folder / VERSIONS / previous / "spec.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, KeyError, TypeError):
                return None
        changes = revision.diff(baseline, spec)
        if changes and previous is None:
            previous = self._archive(part)
        if changes or previous:
            _write_json(folder / CHANGES_FILE, {"previous": previous, "changes": changes})
        return changes, previous

    @staticmethod
    def _changes(folder: Path) -> list[dict]:
        """Die Änderungsliste aus changes.json; leer ohne Revision oder bei unlesbarer Datei."""
        try:
            return json.loads((folder / CHANGES_FILE).read_text(encoding="utf-8"))["changes"]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def _drop_mid(self, part: str) -> None:
        """Entfernt die Dateien eines Toleranzmitte-Exports: Sie passen nicht mehr zu geänderten Vorgängen."""
        for name in MID_FILES:
            (self._folder(part) / name.format(part=part)).unlink(missing_ok=True)

    def _write_state(self, part: str) -> None:
        folder = self._folder(part)
        folder.mkdir(parents=True, exist_ok=True)
        _write_json(folder / "spec.json", self.specs[part].model_dump(mode="json"))
        _write_json(folder / "ops.json", self.ops[part])

    def _build(self, part: str, ops: list[dict], **export: object) -> dict:
        """Baut die ops gegen die Spezifikation des Bauteils; `export` fügt Schlüssel zur Nutzlast hinzu (siehe build_spec)."""
        return build_spec(self.specs[part], ops, **export)

    def _commit(self, part: str, ops: list[dict], op_index: int, fix_direction: bool = False) -> dict:
        """Baut die neue Vorgangsliste auf und übernimmt sie, sofern kein Aufbaufehler auftritt (`ok=false` wird übernommen).

        Mit `fix_direction` wird bei NO_EFFECT der Vorgang `op_index` mit umgekehrter Richtung gebaut; liefert diese
        Variante keine Warnung und keinen Fehler, wird sie statt der Eingabe übernommen (`report["coerced"]`).
        """
        started = time.monotonic()
        report = self._build(part, ops) | {"op_index": op_index}
        flipped = _flipped(ops[op_index]) if fix_direction and not report["error"] and _no_effect(report, op_index) else None
        if flipped:
            variant = [*ops[:op_index], flipped[0], *ops[op_index + 1 :]]
            retry = self._build(part, variant) | {"op_index": op_index}
            if not retry["error"] and not _no_effect(retry, op_index):
                ops, report = variant, retry | {"coerced": [f"op {op_index}: {flipped[1]}"]}
        seconds = time.monotonic() - started
        if report["error"]:
            log.warning("Build of '%s' with %d ops failed after %.1fs: %s", part, len(ops), seconds, report["error"])
        elif not report["ok"]:
            failed = [r["check"] for r in review.validation(report) if not r["ok"]]
            if BODY_CHECKS.intersection(failed):
                log.warning(
                    "Build of '%s' with %d ops after %.1fs without error, but checks failed: %s",
                    part,
                    len(ops),
                    seconds,
                    ", ".join(failed),
                )
            else:  # Fehlende Merkmale sind bis zum letzten Schritt normal.
                log.info("Build of '%s' with %d ops after %.1fs: still open: %s", part, len(ops), seconds, ", ".join(failed))
        else:
            log.info("Build of '%s' with %d ops ok after %.1fs", part, len(ops), seconds)
        if not report["error"]:
            report["build_notes"] += _plain_thread_holes(self.specs[part], ops)
            self.ops[part] = ops
            self.last[part] = report
            self._write_state(part)
            self._drop_mid(part)
        return report

    def _index(self, part: str, index: int) -> None:
        if not 0 <= index < len(self.ops[part]):
            raise IndexError(f"`index` {index} is outside the {len(self.ops[part])} ops.")

    @_per_part
    def submit_spec(self, spec: PartSpec, keep_ops: bool = False) -> dict:
        """Registriert eine Spezifikation, setzt die Vorgänge des Bauteils zurück und schreibt results/<name>/spec.json.

        Ohne keep_ops wandert der Stand eines vorhandenen Bauteils zuerst nach results/<name>/versions/<Zeitstempel>/.

        Mit keep_ops bleiben die Vorgänge eines vorhandenen Bauteils erhalten: Sie werden gegen die neue
        Spezifikation erneut aufgebaut und der Aufbau-Bericht kommt als "report" zurück. Eine unbekannte
        Maß-ID in ihren refs ist ein Fehler und nichts wird geändert.

        Hat das Bauteil schon ein exportiertes Modell, ist das eine neue Revision: Die Antwort enthält "changes"
        (Unterschiede zur vorigen Spezifikation, siehe quality/revision.py) und "previous_version" (der abgelegte
        Stand unter versions/, nur wenn sich etwas geändert hat); beides steht auch in results/<name>/changes.json.
        """
        part = unicodedata.normalize("NFC", spec.name)
        spec = spec.model_copy(update={"name": part})
        ops, report, old, revised = [], None, None, None
        if keep_ops:
            try:
                ops, old = self.ops[self._load(part)], self.specs[part]
            except KeyError:  # noch kein solches Bauteil: nichts zu behalten
                pass
        if ops:
            report = build_spec(spec, ops)  # gegen die neue Spezifikation; die der Sitzung bleibt unverändert
        if not keep_ops:
            self._archive(part)
            (self._folder(part) / CHANGES_FILE).unlink(missing_ok=True)  # eine neue Spezifikation beginnt keine Revision
        else:
            revised = self._revise(part, old, spec) if old is not None else None
            self._drop_mid(part)  # die neue Spezifikation verschiebt die Toleranzmitten
        self.specs[part], self.ops[part] = spec, ops
        self.last.pop(part, None)
        if report and not report["error"]:
            self.last[part] = report
        self._write_state(part)
        result = {"ok": True, "part": part, "dimensions": len(spec.dimensions), "review_items": len(spec.review)}
        if keep_ops:
            result["ops"] = len(ops)
        if revised:
            result |= {"changes": revised[0], "previous_version": revised[1]}
        if report:
            result["report"] = report
            if report["error"]:
                log.warning("Kept ops of '%s' do not build: %s", part, report["error"])
        log.info(
            "Specification '%s': %d dimensions, %d review items%s",
            part,
            len(spec.dimensions),
            len(spec.review),
            f", {len(ops)} ops kept" if keep_ops else "",
        )
        return result

    def _snap(self, part: str, op: dict) -> tuple[dict, list[str]]:
        """Bögen des Vorgangs auf die Radien der Spezifikation (Radiusmaße und halbe Durchmesser), siehe quality/arcs.py."""
        dims = self.specs[part].dimensions
        radii = [d.value for d in dims if d.kind == "radius"] + [d.value / 2 for d in dims if d.kind == "diameter"]
        return snap_arcs(op, radii)

    def _commit_snapped(self, part: str, ops: list[dict], index: int) -> dict:
        """Baut `ops` mit dem auf die Radien gelegten Vorgang `index` auf; jede Verschiebung erscheint unter "coerced"."""
        ops[index], notes = self._snap(part, ops[index])
        report = self._commit(part, ops, index, fix_direction=True)
        if notes:
            report["coerced"] = [f"op {index}: {n}" for n in notes] + report.get("coerced", [])
        return report

    @_per_part
    def apply(self, part: str, op: dict) -> dict:
        part = self._load(part)
        return self._commit_snapped(part, [*self.ops[part], op], len(self.ops[part]))

    @_per_part
    def edit_op(self, part: str, index: int, op: dict) -> dict:
        part = self._load(part)
        self._index(part, index)
        if _same_op(self.ops[part][index], op):
            pending = [r["check"] for r in review.validation(self.last[part]) if not r["ok"]] if part in self.last else []
            return {
                "ok": True,
                "unchanged": True,
                "op_index": index,
                "pending": pending,
                "note": "The operation is identical to the current one: nothing changed and nothing was rebuilt. Open checks belong to "
                "features that are still missing: build the next feature instead of repeating the operation.",
            }
        ops = list(self.ops[part])
        ops[index] = op
        return self._commit_snapped(part, ops, index)

    @_per_part
    def delete_op(self, part: str, index: int) -> dict:
        part = self._load(part)
        self._index(part, index)
        ops = [op for k, op in enumerate(self.ops[part]) if k != index]
        if not ops:  # der letzte Vorgang: das Teil ist wieder leer, ein Aufbau entfällt
            self.ops[part] = []
            self.last.pop(part, None)
            self._write_state(part)
            for name in NOMINAL_FILES + MID_FILES:  # ein früherer Export gehört nicht mehr zu diesem Teil
                (self._folder(part) / name.format(part=part)).unlink(missing_ok=True)
            return {"ok": False, "error": None, "solid": None, "dims": [], "build_notes": [], "roundtrip": None, "op_index": 0}
        return _stored_error_index(self._commit(part, ops, index), [k for k in range(len(self.ops[part])) if k != index])

    @_per_part
    def move_op(self, part: str, index: int, new_index: int) -> dict:
        part = self._load(part)
        self._index(part, index)
        self._index(part, new_index)
        ops = list(self.ops[part])
        ops.insert(new_index, ops.pop(index))
        order = list(range(len(ops)))
        order.insert(new_index, order.pop(index))
        return _stored_error_index(self._commit(part, ops, new_index), order)

    def state(self, part: str) -> dict:
        part = self._load(part)
        spec = self.specs[part]
        summary = {
            "name": part,
            "title": spec.title,
            "scale": spec.scale,
            "dimensions": len(spec.dimensions),
            "review_items": len(spec.review),
            "expected_bbox": spec.expected.bbox,
        }
        return {"spec_summary": summary, "ops": self.ops[part], "last_report": self.last.get(part)}

    @_per_part
    def export(self, part: str, variant: Variant = "nominal") -> dict:
        """Baut mit dem STEP-Export und schreibt die Ergebnisdateien; ein Modell, das nicht ok ist, schreibt keine.

        "nominal" schreibt STEP, GLB, marks.json, pmi.json, Bericht und Prüfliste. "mid" baut das Bauteil mit
        jedem tolerierten Maß auf seiner Toleranzmitte und schreibt <part>_mid.step und report_mid.json.
        Die Toleranztabellen stammen aus D2C_TABLES, sonst aus den Standardtabellen (ISO 2768-1, ISO 286-1).
        Ergebnis: {"ok", "files", "report", "warnings", "shifts", "skipped"}. shifts sind die auf die Mitte verschobenen
        Vorgangswerte, skipped die unberührten Bindungen (der Wert ist kein Vielfaches des Maßes); warnings nennen
        angenommene Maße ohne PMI, tolerierte Maße ohne Bindung und Toleranzen, die den Tabellen fehlen.
        Die Dateien entstehen zuerst in einem temporären Ordner und ersetzen die bisherigen erst, wenn alle Prüfungen
        bestehen: Ein fehlgeschlagener Export lässt das letzte gute Ergebnis unberührt.
        """
        part = self._load(part)
        if variant not in get_args(Variant):
            raise ValueError(f"variant must be one of {', '.join(get_args(Variant))}, not {variant!r}")
        spec, ops, mid = self.specs[part], self.ops[part], variant == "mid"
        folder = self._folder(part)
        folder.mkdir(parents=True, exist_ok=True)
        step = folder / (f"{part}_mid.step" if mid else f"{part}.step")
        built, shifts, skipped = apply_mode(ops, spec, variant)
        # die Maßprüfungen erlauben, was die verschobenen Werte zusammen ergeben können; ein Radius verschiebt einen Durchmesser doppelt so weit
        tol = LINEAR_TOL + 2 * sum(abs(c["factor"] * (c["mid"] - c["nominal"])) for c in shifts)
        work = Path(tempfile.mkdtemp(prefix=".export-", dir=folder))  # im selben Dateisystem: Verschieben ist ein Umbenennen
        try:
            report = self._build(
                part,
                built,
                export_path=str(work / step.name),
                pmi_spec=spec.model_dump(mode="json"),
                tol=tol,
                glb_path=None if mid else str(work / f"{part}.glb"),
            )
            if not report["ok"]:
                failed = [r["check"] for r in review.validation(report) if not r["ok"]]
                log.warning("Export of '%s' (%s) failed: %s", part, variant, report["error"] or ", ".join(failed))
                return {"ok": False, "error": "Check failed", "report": report}
            refusal, gated = (None, []) if mid else self.gates.screen(part, spec, ops, report)
            if refusal:
                log.warning("Export of '%s' refused: %s", part, refusal["error"])
                return refusal
            # Alles Weitere entsteht ebenfalls im temporären Ordner und wird aus der dort liegenden STEP-Datei gemessen.
            geometry = measure_step(str(work / step.name))
            report["compliance"] = compliance(spec, ops, report, variant, geometry)
            pmi = read_pmi(str(work / step.name))["dimensions"]
            assumed, missing = missing_assumed_pmi(spec, pmi)
            report["roundtrip"]["pmi"] |= {"assumed": assumed, "missing": missing}
            warnings = _export_warnings(spec, ops, variant, skipped, missing) + gated
            report |= {"shifts": shifts, "skipped": skipped, "warnings": warnings}
            if mid:
                _write_json(work / "report_mid.json", report)
            else:
                rows = _tolerance_rows(spec, ops, report["compliance"], geometry)
                changes = self._changes(folder)
                _write_json(work / "report.json", report)
                _write_json(work / "review.json", review.to_json(spec, ops, report, rows, pmi, changes))
                (work / "review.md").write_text(review.render(spec, ops, report, rows, pmi, changes), encoding="utf-8")
                self._write_state(part)
            # Reihenfolge ist Absicht: Die STEP-Datei gilt Web und Batch als "fertig" und wird als LETZTE ersetzt. Bricht der
            # Export vorher ab, bleibt das alte STEP stehen und nie ein neues STEP neben einem alten report.json.
            for written in sorted(work.iterdir(), key=lambda p: p.name == step.name):
                os.replace(written, folder / written.name)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        for text in warnings:
            log.warning("Export of '%s' (%s): %s", part, variant, text)
        log.info("Export of '%s' (%s) to %s", part, variant, step)
        extra = {"warnings": warnings, "shifts": shifts, "skipped": skipped}
        if mid:
            files = {"step": str(step), "report": str(folder / "report_mid.json")}
            return {"ok": True, "files": files, "report": report} | extra
        self.last[part] = report
        names = {
            "step": step.name,
            "report": "report.json",
            "review": "review.md",
            "review_json": "review.json",
            "ops": "ops.json",
            "spec": "spec.json",
            "glb": f"{part}.glb",
            "marks": "marks.json",
            "pmi": "pmi.json",
        }
        return {"ok": True, "files": {key: str(folder / name) for key, name in names.items()}, "report": report} | extra

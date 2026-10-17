"""Vorgangssitzungen: je Bauteil eine PartSpec und Vorgangsliste, im Speicher und unter results/<name>/ abgelegt."""

import json
import os
import re
import unicodedata
from pathlib import Path

from d2c import review
from d2c.sandbox import run_worker
from d2c.spec import PartSpec


PART_NAME = re.compile(r"[\w-]+")  # dieselbe Regel wie für PartSpec.name


def results_root() -> Path:
    """Ergebnisordner; D2C_RESULTS wird bei jedem Aufruf neu gelesen."""
    return Path(os.environ.get("D2C_RESULTS", "/app/results"))


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class Session:
    """Spezifikationen, Vorgangslisten und letzte Aufbau-Berichte der Bauteile in diesem Serverlauf."""

    def __init__(self) -> None:
        self.specs: dict[str, PartSpec] = {}
        self.ops: dict[str, list[dict]] = {}
        self.last: dict[str, dict] = {}

    def _folder(self, part: str) -> Path:
        return results_root() / part

    def _load(self, part: str) -> str:
        """Normalisierter Name eines bekannten Bauteils; ein nur auf Datenträger vorhandenes Bauteil wird zuerst geladen."""
        part = unicodedata.normalize("NFC", part)
        if not PART_NAME.fullmatch(part):
            raise KeyError(f"Unbekanntes Bauteil '{part}'; rufe zuerst `submit_spec` auf.")
        if part not in self.specs:
            folder = self._folder(part)
            if not (folder / "spec.json").is_file():
                raise KeyError(f"Unbekanntes Bauteil '{part}'; rufe zuerst `submit_spec` auf.")
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
            raise ValueError(f"{folder.name}/{name} ist nicht lesbar ({e}); rufe `submit_spec` erneut auf.") from e

    def _write_state(self, part: str) -> None:
        folder = self._folder(part)
        folder.mkdir(parents=True, exist_ok=True)
        _write_json(folder / "spec.json", self.specs[part].model_dump(mode="json"))
        _write_json(folder / "ops.json", self.ops[part])

    def _build(self, part: str, ops: list[dict], export_path: Path | None = None) -> dict:
        spec = self.specs[part]
        known = {d.id for d in spec.dimensions}
        for op in ops:
            if not isinstance(op, dict) or not isinstance(op.get("refs", {}), dict):
                raise ValueError(
                    '`refs` muss ein Objekt sein, das Vorgangsparameterpfade Maß-IDs zuordnet, z. B. {"d": "d1"}.')
            for dim_id in op.get("refs", {}).values():
                if dim_id not in known:
                    raise ValueError(f"Unbekannte Maß-ID '{dim_id}' in `refs`.")
        return run_worker({
            "ops": ops,
            "expected": spec.expected.model_dump(mode="json"),
            "marks": [{"id": r.id, "near": r.mark_near} for r in spec.review if r.mark_near],
            "export_path": str(export_path) if export_path else None,
        })

    def _commit(self, part: str, ops: list[dict], op_index: int) -> dict:
        """Baut die neue Vorgangsliste auf und übernimmt sie, sofern kein Aufbaufehler auftritt (`ok=false` wird übernommen)."""
        report = self._build(part, ops) | {"op_index": op_index}
        if not report["error"]:
            self.ops[part] = ops
            self.last[part] = report
            self._write_state(part)
        return report

    def _index(self, part: str, index: int) -> None:
        if not 0 <= index < len(self.ops[part]):
            raise IndexError(f"`index` {index} liegt außerhalb der {len(self.ops[part])} Vorgänge.")

    def submit_spec(self, spec: PartSpec) -> dict:
        """Registriert eine Spezifikation, setzt die Vorgänge des Bauteils zurück und schreibt results/<name>/spec.json."""
        part = unicodedata.normalize("NFC", spec.name)
        self.specs[part] = spec.model_copy(update={"name": part})
        self.ops[part] = []
        self.last.pop(part, None)
        self._write_state(part)
        return {"ok": True, "part": part, "dimensions": len(spec.dimensions), "review_items": len(spec.review)}

    def apply(self, part: str, op: dict) -> dict:
        part = self._load(part)
        return self._commit(part, [*self.ops[part], op], len(self.ops[part]))

    def edit_op(self, part: str, index: int, op: dict) -> dict:
        part = self._load(part)
        self._index(part, index)
        ops = list(self.ops[part])
        ops[index] = op
        return self._commit(part, ops, index)

    def delete_op(self, part: str, index: int) -> dict:
        part = self._load(part)
        self._index(part, index)
        return self._commit(part, [op for k, op in enumerate(self.ops[part]) if k != index], index)

    def move_op(self, part: str, index: int, new_index: int) -> dict:
        part = self._load(part)
        self._index(part, index)
        self._index(part, new_index)
        ops = list(self.ops[part])
        ops.insert(new_index, ops.pop(index))
        return self._commit(part, ops, new_index)

    def state(self, part: str) -> dict:
        part = self._load(part)
        spec = self.specs[part]
        summary = {"name": part, "title": spec.title, "scale": spec.scale, "dimensions": len(spec.dimensions),
                   "review_items": len(spec.review), "expected_bbox": spec.expected.bbox}
        return {"spec_summary": summary, "ops": self.ops[part], "last_report": self.last.get(part)}

    def export(self, part: str) -> dict:
        """Baut das Bauteil samt STEP-Export und schreibt die Ergebnisdateien; bei einem ungültigen Modell wird nichts geschrieben."""
        part = self._load(part)
        folder = self._folder(part)
        folder.mkdir(parents=True, exist_ok=True)
        step = folder / f"{part}.step"
        report = self._build(part, self.ops[part], export_path=step)
        if not report["ok"]:
            for name in (step.name, "report.json", "review.md", "review.json"):
                (folder / name).unlink(missing_ok=True)
            return {"ok": False, "error": "Prüfung fehlgeschlagen", "report": report}
        self.last[part] = report
        self._write_state(part)
        spec, ops = self.specs[part], self.ops[part]
        _write_json(folder / "report.json", report)
        _write_json(folder / "review.json", review.to_json(spec, ops, report))
        (folder / "review.md").write_text(review.render(spec, ops, report), encoding="utf-8")
        names = {"step": step.name, "report": "report.json", "review": "review.md", "review_json": "review.json",
                 "ops": "ops.json", "spec": "spec.json"}
        return {"ok": True, "files": {key: str(folder / name) for key, name in names.items()}, "report": report}

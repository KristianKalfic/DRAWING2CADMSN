"""Op-list sessions: one PartSpec and its list of ops per part, kept in memory and in results/<name>/."""

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import get_args

from d2c import review
from d2c.checks import LINEAR_TOL
from d2c.sandbox import run_worker
from d2c.spec import PartSpec
from d2c.tolerances import NO_TABLES, Variant, apply_mode, compliance, limits, mid_value, tables_found


PART_NAME = re.compile(r"[\w-]+")  # the same rule as PartSpec.name
MID_FILES = ("{part}_mid.step", "report_mid.json")  # what a mid export writes
NOMINAL_FILES = ("{part}.step", "{part}.glb", "marks.json", "pmi.json", "report.json", "review.md", "review.json")


def results_root() -> Path:
    """The results folder, read from D2C_RESULTS on every call; absolute, as the build worker runs elsewhere."""
    return Path(os.environ.get("D2C_RESULTS", "/app/results")).absolute()


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _tolerance_rows(spec: PartSpec, ops: list[dict], nominal: list[dict]) -> list[dict]:
    """The rows of the review's tolerance table: the nominal compliance entries, the tolerance mid of each
    dimension and whether a model on the mid keeps its tolerance. That verdict is taken from the values the ops
    get on the mid; measuring the holes is left to the report of the mid export."""
    dims = {d.id: d for d in spec.dimensions}
    on_mid = {c["dim_id"]: c["in_tolerance"] for c in compliance(spec, ops, {}, "mid")}
    return [
        {"dim_id": c["dim_id"], "text": c["text"], "nominal": c["nominal"], "lower": c["lower"], "upper": c["upper"],
         "mid": mid_value(dims[c["dim_id"]], spec.general_tolerance), "source": c["source"],
         "in_tolerance_nominal": c["in_tolerance"], "in_tolerance_mid": on_mid[c["dim_id"]]}
        for c in nominal
    ]


class Session:
    """The specs, op lists and last build reports of the parts of this server run."""

    def __init__(self) -> None:
        self.specs: dict[str, PartSpec] = {}
        self.ops: dict[str, list[dict]] = {}
        self.last: dict[str, dict] = {}

    def _folder(self, part: str) -> Path:
        return results_root() / part

    def _load(self, part: str) -> str:
        """The normalized name of a known part; a part only on disk is loaded first."""
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
        """The spec and op list stored in a part folder; an unreadable file is named in the error."""
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

    def _build(self, part: str, ops: list[dict], **export: object) -> dict:
        """Build the ops in a worker; `export` adds payload keys (export_path, glb_path, pmi_spec, tol)."""
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
            "export_path": None,
        } | export)

    def _commit(self, part: str, ops: list[dict], op_index: int) -> dict:
        """Build the new op list and adopt it unless the build has an error (ok=false is adopted)."""
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
        """Register a spec, reset the ops of the part and write results/<name>/spec.json."""
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

    def export(self, part: str, variant: Variant = "nominal") -> dict:
        """Build with the STEP export and write the result files; a model that is not ok writes none.

        "nominal" writes the STEP, GLB, marks.json, pmi.json, report and review list. "mid" builds the part with
        every toleranced dimension at its tolerance mid and writes <part>_mid.step and report_mid.json; it needs
        the tolerance tables. Without them the nominal export still works and its compliance is None.
        """
        part = self._load(part)
        if variant not in get_args(Variant):
            raise ValueError(f"variant must be one of {', '.join(get_args(Variant))}, not {variant!r}")
        spec, ops, mid, tables = self.specs[part], self.ops[part], variant == "mid", tables_found()
        if mid and not tables:
            raise ValueError(NO_TABLES)
        if tables:
            for dim in spec.dimensions:
                limits(dim, spec.general_tolerance)  # a class or size the tables lack raises now, before any build
        folder = self._folder(part)
        folder.mkdir(parents=True, exist_ok=True)
        step = folder / (f"{part}_mid.step" if mid else f"{part}.step")
        built, shifts = apply_mode(ops, spec, variant)
        # the dimension checks allow what the shifted values can add up to; a radius moves a diameter twice as far
        tol = LINEAR_TOL + 2 * sum(abs(c["mid"] - c["nominal"]) for c in shifts)
        report = self._build(part, built, export_path=str(step), pmi_spec=spec.model_dump(mode="json"), tol=tol,
                             glb_path=None if mid else str(folder / f"{part}.glb"))
        if not report["ok"]:
            for name in MID_FILES if mid else NOMINAL_FILES + MID_FILES:
                (folder / name.format(part=part)).unlink(missing_ok=True)
            return {"ok": False, "error": "Prüfung fehlgeschlagen", "report": report}
        report["compliance"] = compliance(spec, ops, report, variant) if tables else None
        if mid:
            _write_json(folder / "report_mid.json", report)
            files = {"step": str(step), "report": str(folder / "report_mid.json")}
            return {"ok": True, "files": files, "report": report}
        self.last[part] = report
        self._write_state(part)
        pmi = json.loads((folder / "pmi.json").read_text(encoding="utf-8"))["dimensions"]  # the worker read it back
        rows = _tolerance_rows(spec, ops, report["compliance"]) if tables else None
        _write_json(folder / "report.json", report)
        _write_json(folder / "review.json", review.to_json(spec, ops, report, rows, pmi))
        (folder / "review.md").write_text(review.render(spec, ops, report, rows, pmi), encoding="utf-8")
        names = {"step": step.name, "report": "report.json", "review": "review.md", "review_json": "review.json",
                 "ops": "ops.json", "spec": "spec.json", "glb": f"{part}.glb", "marks": "marks.json",
                 "pmi": "pmi.json"}
        return {"ok": True, "files": {key: str(folder / name) for key, name in names.items()}, "report": report}

"""Op-list sessions: one PartSpec and its list of ops per part, kept in memory and in results/<name>/."""

import json
import os
import re
import unicodedata
from pathlib import Path

from d2c import review
from d2c.sandbox import run_worker
from d2c.spec import PartSpec


PART_NAME = re.compile(r"[\w-]+")  # the same rule as PartSpec.name


def results_root() -> Path:
    """The results folder, read from D2C_RESULTS on every call."""
    return Path(os.environ.get("D2C_RESULTS", "/app/results"))


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
            raise KeyError(f"unknown part '{part}'; call submit_spec first")
        if part not in self.specs:
            folder = self._folder(part)
            if not (folder / "spec.json").is_file():
                raise KeyError(f"unknown part '{part}'; call submit_spec first")
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
            raise ValueError(f"{folder.name}/{name} is unreadable ({e}); call submit_spec again") from e

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
                    'refs must be an object mapping an op parameter path to a dimension id, e.g. {"d": "d1"}')
            for dim_id in op.get("refs", {}).values():
                if dim_id not in known:
                    raise ValueError(f"unknown dimension id '{dim_id}' in refs")
        return run_worker({
            "ops": ops,
            "expected": spec.expected.model_dump(mode="json"),
            "marks": [{"id": r.id, "near": r.mark_near} for r in spec.review if r.mark_near],
            "export_path": str(export_path) if export_path else None,
        })

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
            raise IndexError(f"op index {index} out of range for {len(self.ops[part])} ops")

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

    def export(self, part: str) -> dict:
        """Build with the STEP export and write the result files; a model that is not ok writes none."""
        part = self._load(part)
        folder = self._folder(part)
        folder.mkdir(parents=True, exist_ok=True)
        step = folder / f"{part}.step"
        report = self._build(part, self.ops[part], export_path=step)
        if not report["ok"]:
            for name in (step.name, "report.json", "review.md", "review.json"):
                (folder / name).unlink(missing_ok=True)
            return {"ok": False, "error": "validation failed", "report": report}
        self.last[part] = report
        self._write_state(part)
        spec, ops = self.specs[part], self.ops[part]
        _write_json(folder / "report.json", report)
        _write_json(folder / "review.json", review.to_json(spec, ops, report))
        (folder / "review.md").write_text(review.render(spec, ops, report), encoding="utf-8")
        names = {"step": step.name, "report": "report.json", "review": "review.md", "review_json": "review.json",
                 "ops": "ops.json", "spec": "spec.json"}
        return {"ok": True, "files": {key: str(folder / name) for key, name in names.items()}, "report": report}

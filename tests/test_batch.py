"""Der Stapellauf mit gemocktem run_agent: Teilnamen, Parallelität, Übersicht, Wiederaufnahme, Fehler."""

import asyncio
import csv
import json
import os
import shutil
from pathlib import Path

import pytest
from helpers import DEMO_PDF

from d2c.agent import batch
from d2c.agent.backend import Backend


@pytest.fixture
def folder(tmp_path) -> Path:
    """Zwei Teile in Unterordnern, eine PDF im Wurzelordner und ein Ordner mit zwei PDFs."""
    root = tmp_path / "drawings"
    for rel in ("plate.pdf", "Bracket A/drawing.pdf", "Pair/one.pdf", "Pair/two.pdf", "Deep/Inner/x.pdf", ".hidden/h.pdf"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(DEMO_PDF, root / rel)
    return root


class FakeAgent:
    """Ersetzt run_agent: schreibt wie der Server report.json und spec.json, zählt die gleichzeitigen Läufe."""

    def __init__(self, fail=(), crash=(), delay=0.0):
        self.calls, self.fail, self.crash, self.delay, self.running, self.peak = [], set(fail), set(crash), delay, 0, 0

    async def __call__(self, pdf, part, *, on_event, **options):
        self.calls.append((pdf, part, options))
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            on_event({"type": "phase", "phase": "read"})
            await asyncio.sleep(self.delay)
            if part in self.crash:
                raise RuntimeError("boom")
            if part in self.fail:
                result = {"ok": False, "part": part, "reason": "repeated failing call extrude", "turns": 4, "cost_usd": 0.5}
            else:
                folder = batch.results_root() / part
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "report.json").write_text(json.dumps({"ok": True}))
                (folder / "spec.json").write_text(json.dumps({"review": [{}, {}]}))
                result = {"ok": True, "part": part, "files": {}, "turns": 7, "cost_usd": 0.25}
            on_event({"type": "done"} | result)
            return result
        finally:
            self.running -= 1


def run(folder, tmp_path, agent, **kwargs):
    kwargs.setdefault("backend", Backend())
    return asyncio.run(batch.run_batch(folder, tmp_path / "out", agent=agent, log=lambda line: None, **kwargs))


def test_discover_names_parts_after_folders(folder):
    assert batch.discover(folder) == [
        ("Bracket_A", "Bracket A/drawing.pdf"),
        ("Deep_Inner", "Deep/Inner/x.pdf"),
        ("Pair_one", "Pair/one.pdf"),
        ("Pair_two", "Pair/two.pdf"),
        ("plate", "plate.pdf"),
    ]


def test_discover_makes_names_unique_and_skips_the_results_folder(folder):
    (folder / "plate").mkdir()
    shutil.copy(DEMO_PDF, folder / "plate" / "other.pdf")
    (folder / "out").mkdir()
    shutil.copy(DEMO_PDF, folder / "out" / "skipped.pdf")
    names = [name for name, _ in batch.discover(folder, skip=folder / "out")]
    assert names.count("plate") == 1 and "plate_2" in names and "skipped" not in names


def test_runs_every_part_with_the_semaphore_and_writes_the_overview(folder, tmp_path):
    agent = FakeAgent(delay=0.05)
    rows = run(folder, tmp_path, agent, jobs=2, backend=Backend("m"), max_usd=1)
    assert agent.peak == 2 and len(agent.calls) == 5
    pdf, part, options = agent.calls[0]
    assert (pdf, part) == ("Bracket A/drawing.pdf", "Bracket_A") and options["backend"].model == "m" and options["max_usd"] == 1
    assert [r["part"] for r in rows] == ["Bracket_A", "Deep_Inner", "Pair_one", "Pair_two", "plate"]
    assert all(
        r["status"] == "ok" and r["export_ok"] and r["turns"] == 7 and r["cost_usd"] == 0.25 and r["review_items"] == 2 for r in rows
    )
    out = tmp_path / "out"
    assert json.loads((out / "batch.json").read_text(encoding="utf-8")) == rows
    with (out / "batch.csv").open(encoding="utf-8", newline="") as f:
        table = list(csv.DictReader(f))
    assert tuple(table[0]) == batch.COLUMNS and [r["part"] for r in table] == [r["part"] for r in rows]
    assert table[0]["export_ok"] == "True" and float(table[0]["duration_s"]) >= 0


def test_every_run_leaves_a_trace(folder, tmp_path):
    run(folder, tmp_path, FakeAgent(), jobs=1)
    (path,) = (tmp_path / "out" / "plate" / "runs").glob("*.jsonl")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert (lines[0]["type"], lines[0]["part"], lines[0]["pdf"]) == ("meta", "plate", "plate.pdf")
    assert [line["type"] for line in lines[1:]] == ["phase", "done"]


def test_resume_skips_finished_parts_unless_forced(folder, tmp_path):
    run(folder, tmp_path, FakeAgent(fail={"plate"}), jobs=2)
    again = FakeAgent()
    rows = run(folder, tmp_path, again, jobs=2)
    assert [call[1] for call in again.calls] == ["plate"]  # nur das gescheiterte Teil läuft erneut
    assert all(r["status"] == "ok" for r in rows)
    assert {r["part"]: r["cost_usd"] for r in rows}["Bracket_A"] == 0.25  # die Zeile des früheren Laufs bleibt
    forced = FakeAgent()
    run(folder, tmp_path, forced, jobs=2, force=True)
    assert len(forced.calls) == 5


def test_failure_and_crash_do_not_stop_the_batch(folder, tmp_path):
    rows = {r["part"]: r for r in run(folder, tmp_path, FakeAgent(fail={"Pair_one"}, crash={"plate"}), jobs=3)}
    assert rows["Pair_one"]["status"] == "failed" and rows["Pair_one"]["export_ok"] is False
    assert rows["Pair_one"]["reason"] == "repeated failing call extrude"
    assert rows["plate"]["status"] == "error" and "RuntimeError: boom" in rows["plate"]["reason"]
    assert rows["Bracket_A"]["status"] == "ok"
    (path,) = (tmp_path / "out" / "plate" / "runs").glob("*.jsonl")
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["type"] == "done"


def test_data_folder_for_the_server(folder, tmp_path, monkeypatch):
    monkeypatch.delenv("D2C_DATA", raising=False)
    agent = FakeAgent()
    run(folder, tmp_path, agent)
    assert os.environ["D2C_DATA"] == str(folder.absolute()) and os.environ["D2C_RESULTS"] == str((tmp_path / "out").absolute())
    monkeypatch.setenv("D2C_DATA", str(folder.parent))  # ORDNER liegt im Datenordner: der Pfad ist relativ zu diesem
    agent = FakeAgent()
    run(folder, tmp_path, agent, force=True)
    assert agent.calls[0][0] == "drawings/Bracket A/drawing.pdf"


def test_cli(folder, tmp_path, monkeypatch, capsys):
    seen = {}

    async def fake_batch(root, out, **kwargs):
        seen.update(root=root, out=out, **kwargs)
        return [{"part": "plate", "export_ok": True, "cost_usd": 0.5}, {"part": "x", "export_ok": False}]

    monkeypatch.setattr(batch, "run_batch", fake_batch)
    monkeypatch.setenv("D2C_LLM_API_KEY", "key-123")
    assert batch.main([str(folder), "--jobs", "3", "--model", "m", "--out", "o", "--force", "--max-minutes", "5", "--no-script"]) == 1
    assert (seen["jobs"], seen["backend"].model, seen["force"], seen["max_minutes"], seen["allow_script"]) == (3, "m", True, 5, False)
    assert seen["out"] == Path("o") and seen["backend"].api_key == "key-123" and seen["server"] == "stdio" and seen["max_usd"] is None
    out = capsys.readouterr().out
    assert "1 of 2 parts" in out and "cost per summary 0.50 USD (without 1 parts of unknown cost)" in out
    with pytest.raises(SystemExit):
        batch.main([str(folder / "missing")])
    with pytest.raises(SystemExit):
        batch.main([str(folder), "--jobs", "0"])

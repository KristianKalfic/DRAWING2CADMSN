"""Web-App: Teileliste, Detailansicht, Dateidownloads sowie Ereignisse beim Hochladen und während eines Laufs mit einem tatsächlich exportierten Teil."""

import json
import shutil
import struct
import sys
import threading
import time
from pathlib import Path

import pymupdf
import pytest
from helpers import DEMO_PDF, dimension
from starlette.testclient import TestClient

from d2c.agent.backend import Backend
from d2c.session import Session
from d2c.spec import PartSpec
from d2c.web.app import _agent_backends, _model_choices, create_app

LLM_ENV = {"D2C_LLM_BASE_URL": "http://llm.invalid/v1", "D2C_LLM_MODEL": "m"}
LLM_VARS = (
    "D2C_LLM_BASE_URL",
    "D2C_LLM_MODEL",
    "D2C_LLM_BUILD_MODEL",
    "D2C_LLM_API_KEY",
    "D2C_OLLAMA_URL",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)


SPEC = {
    "name": "plate",
    "drawing": "Demo/plate.pdf",
    "title": "Platte 80 × 50",
    "scale": "1:1",
    "views": [],
    "features": [],
    "dimensions": [
        dimension("w", "length", 80, "80"),
        dimension("d1", "diameter", 8, "Ø8"),
        dimension("t", "length", 10, "", assumed=True, confidence=0.6, assumption_basis="aus der Seitenansicht abgelesen"),
    ],
    "expected": {"bbox": [80, 50, 10], "holes": [{"d": 8, "at": [20, 10, 0], "axis": [0, 0, 1], "dim_ids": ["d1"]}]},
    "review": [
        {"id": "rv1", "refs": ["t"], "issue": "missing", "assumption": "Dicke 10 mm", "reason": "nicht bemaßt", "mark_near": [[0, 0, 10]]}
    ],
    "general_tolerance": "ISO 2768-m",
}
OPS = [
    {
        "op": "extrude",
        "profile": {"type": "rect", "center": [0, 0], "w": 80, "h": 50},
        "plane": {"name": "XY"},
        "distance": 10,
        "mode": "add",
    },
    {"op": "holes", "plane": {"name": "XY", "offset": 10}, "positions": [[20, 10]], "d": 8},
]


@pytest.fixture(scope="module")
def exported(tmp_path_factory) -> Path:
    """Ergebnisordner mit dem Demo-Teil, das in einer echten Sitzung exportiert wurde."""
    results = tmp_path_factory.mktemp("results")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("D2C_RESULTS", str(results))
        session = Session()
        session.submit_spec(PartSpec.model_validate(SPEC))
        for op in OPS:
            session.apply("plate", op)
        assert session.export("plate")["ok"]
    return results


@pytest.fixture
def client(exported, monkeypatch, tmp_path):
    monkeypatch.setenv("D2C_RESULTS", str(exported))
    data = tmp_path / "data"
    (data / "Demo").mkdir(parents=True)
    shutil.copy(DEMO_PDF, data / "Demo" / "plate.pdf")
    monkeypatch.setenv("D2C_DATA", str(data))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    return TestClient(create_app())


def test_parts_list_and_detail(client):
    with client:
        (entry,) = client.get("/api/parts").json()
        assert (entry["name"], entry["title"], entry["review_count"]) == ("plate", "Platte 80 × 50", 1)
        assert entry["issues"] == ["missing"]
        assert "plate.step" in entry["files"] and entry["eval"] is None
        detail = client.get("/api/parts/plate").json()
        assert detail["spec"]["name"] == "plate"
        assert detail["review"]["assumptions"][0]["id"] == "rv1"
        assert detail["report"]["ok"] is True
        missing = client.get("/api/parts/nope")
        assert missing.status_code == 404 and missing.json()["error"] == "Part not found"


def test_part_without_export_lists_no_review(client, monkeypatch, tmp_path):
    folder = tmp_path / "results" / "draft"
    folder.mkdir(parents=True)
    (folder / "spec.json").write_text(json.dumps(SPEC))
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    with client:
        (entry,) = client.get("/api/parts").json()
        detail = client.get("/api/parts/draft").json()
    assert (entry["all_ok"], entry["review_count"], entry["issues"], entry["files"]) == (None, 0, [], ["spec.json"])
    assert detail["review"] is None and detail["spec"]["title"] == "Platte 80 × 50"


def test_eval_row_joins_by_part(client, exported):
    (exported / "EVAL.json").write_text(json.dumps([{"Teil": "other"}, {"Teil": "plate", "MATCH": "ok"}]))
    try:
        with client:
            assert client.get("/api/parts/plate").json()["eval"] == {"Teil": "plate", "MATCH": "ok"}
    finally:
        (exported / "EVAL.json").unlink()


@pytest.mark.parametrize(
    "filename, value",
    [
        ("spec.json", [1]),
        ("review.json", [1]),
        ("review.json", {"assumptions": None}),
        ("review.json", {"assumptions": 3}),
        ("EVAL.json", [None, "Text", {"Teil": "teil", "MATCH": "ok"}]),
    ],
)
def test_invalid_result_structure_does_not_block_parts(tmp_path, monkeypatch, filename, value):
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path))
    folder = tmp_path / "teil"
    folder.mkdir()
    target = tmp_path / filename if filename == "EVAL.json" else folder / filename
    target.write_text(json.dumps(value), encoding="utf-8")
    with TestClient(create_app()) as client:
        response = client.get("/api/parts")
        assert response.status_code == 200
        (entry,) = response.json()
        assert entry["name"] == "teil" and entry["review_count"] == 0
        detail = client.get("/api/parts/teil")
        assert detail.status_code == 200
        if filename == "spec.json":
            assert detail.json()["spec"] is None
        if filename == "EVAL.json":
            assert entry["eval"] == {"Teil": "teil", "MATCH": "ok"}


@pytest.mark.parametrize("drawing", [None, [], 12, ""])
def test_invalid_drawing_reference_returns_german_error(tmp_path, monkeypatch, drawing):
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path))
    folder = tmp_path / "teil"
    folder.mkdir()
    (folder / "spec.json").write_text(json.dumps({"drawing": drawing}), encoding="utf-8")
    with TestClient(create_app()) as client:
        response = client.get("/api/parts/teil/drawing.png")
    assert response.status_code == 404
    assert "No valid drawing" in response.json()["error"]


def test_file_download_and_traversal_rejected(client):
    with client:
        step = client.get("/files/plate/plate.step")
        assert step.status_code == 200 and b"ISO-10303-21" in step.content
        for path in (
            "/files/plate/..%2Fspec.json",
            "/files/plate/%2e%2e%2fplate%2fspec.json",
            "/files/..%2Fplate/spec.json",
            "/files/plate/..",
            "/files/plate/missing.step",
            "/files/plate/%5C..%5Cspec.json",
        ):
            assert client.get(path).status_code == 404, path


def test_drawing_png(client):
    with client:
        response = client.get("/api/parts/plate/drawing.png")
        assert response.status_code == 200 and response.content.startswith(b"\x89PNG")


def test_drawing_png_renders_the_page_of_the_spec(client, monkeypatch, tmp_path):
    results = tmp_path / "results"
    for page in (1, 2, 3):
        (results / f"p{page}").mkdir(parents=True)
        (results / f"p{page}" / "spec.json").write_text(json.dumps({"drawing": "Demo/plate.pdf", "page": page}))
    monkeypatch.setenv("D2C_RESULTS", str(results))
    with client:
        first, second, missing = (client.get(f"/api/parts/p{page}/drawing.png") for page in (1, 2, 3))
    assert first.status_code == second.status_code == 200 and first.content != second.content
    assert missing.status_code == 404 and "`page` 3" in missing.json()["error"]


def fake_agent(events: list[dict], calls: list[dict] | None = None):
    async def agent(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        for event in events:
            kwargs["on_event"](event)
        return {"ok": True}

    return agent


def upload(client, part: str = "newpart", pdf: bytes | None = None, headers: dict | None = None, **extra):
    files = {"pdf": ("any.pdf", DEMO_PDF.read_bytes() if pdf is None else pdf, "application/pdf")}
    return client.post("/api/runs", files=files, data={"part": part} | extra, headers=headers)


def test_run_streams_events(exported, monkeypatch, tmp_path):
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    events = [{"type": "tool_call", "name": "submit_spec"}, {"type": "tool_result", "name": "submit_spec"}, {"type": "done", "ok": True}]
    with TestClient(create_app(fake_agent(events))) as client:
        run_id = upload(client).json()["run_id"]
        with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
            received = [json.loads(line[len("data: ") :]) for line in stream.iter_lines() if line.startswith("data: ")]
    assert received == events


def test_run_appends_done_and_reports_agent_crash(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)

    async def crashing(**kwargs):
        raise RuntimeError("Schnittstelle nicht erreichbar")

    with TestClient(create_app(crashing)) as client:
        run_id = upload(client).json()["run_id"]
        body = client.get(f"/api/runs/{run_id}/events").text
    (last,) = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert last["type"] == "done" and not last["ok"] and "Schnittstelle nicht erreichbar" in last["reason"]
    (logged,) = [r for r in caplog.records if r.name == "d2c.web.app" and r.exc_info]  # der Absturz steht samt Traceback im Protokoll
    assert logged.levelname == "ERROR" and "Schnittstelle nicht erreichbar" in str(logged.exc_info[1])


def test_run_writes_trace_and_serves_it(monkeypatch, tmp_path):
    """Ein Lauf schreibt sein Protokoll nach results/<teil>/runs/; Teil und Ansicht lesen es wieder."""
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    events = [
        {"type": "phase", "phase": "read"},
        {"type": "tool_call", "name": "render_drawing", "args": {"pdf": "uploads/trace.pdf"}},
        {"type": "tool_result", "name": "render_drawing", "ok": True, "text": "ok"},
        {"type": "done", "ok": True},
    ]
    with TestClient(create_app(fake_agent(events))) as client:
        run_id = upload(client, "trace").json()["run_id"]
        client.get(f"/api/runs/{run_id}/events")
        (entry,) = client.get("/api/parts/trace").json()["runs"]
        assert (entry["ok"], entry["events"], entry["part"], entry["pdf"]) == (True, 4, "trace", "uploads/trace.pdf")
        assert entry["started"] and entry["model"] == "m"
        detail = client.get(f"/api/parts/trace/runs/{entry['id']}").json()
        assert client.get("/api/parts/trace/runs/gibtsnicht").status_code == 404
    assert [event["type"] for event in detail["events"]] == ["phase", "tool_call", "tool_result", "done"]
    assert all(isinstance(event.get("at"), (int, float)) for event in detail["events"])


def test_trace_records_a_crashed_run(monkeypatch, tmp_path):
    """Auch ein abgebrochener Lauf steht mit Grund im Protokoll."""
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))

    async def crashing(**kwargs):
        raise RuntimeError("Schnittstelle nicht erreichbar")

    with TestClient(create_app(crashing)) as client:
        run_id = upload(client, "crash").json()["run_id"]
        client.get(f"/api/runs/{run_id}/events")
        (entry,) = client.get("/api/parts/crash").json()["runs"]
    assert entry["ok"] is False and "Schnittstelle nicht erreichbar" in entry["reason"]


def test_run_list_marks_an_unfinished_trace(monkeypatch, tmp_path):
    """Ohne Abschlusszeile gilt der Lauf als abgebrochen; eine abgerissene Zeile wird übersprungen."""
    folder = tmp_path / "results" / "halb" / "runs"
    folder.mkdir(parents=True)
    (folder / "2026-10-17T10-00-00-abcdef12.jsonl").write_text(
        '{"type": "meta", "part": "halb", "started": "2026-10-17T10:00:00+00:00", "pdf": "uploads/halb.pdf", "model": null}\n'
        '{"at": 1.0, "type": "tool_call", "name": "extrude"}\n'
        '{"at": 2.0, "type": "tool_res',
        encoding="utf-8",
    )
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    with TestClient(create_app()) as client:
        (entry,) = client.get("/api/parts/halb").json()["runs"]
        assert (entry["ok"], entry["reason"], entry["events"]) == (None, None, 1)
        events = client.get(f"/api/parts/halb/runs/{entry['id']}").json()["events"]
    assert [event["name"] for event in events] == ["extrude"]


@pytest.mark.parametrize("part_name", ["Träger-1", "Tra\u0308ger-1"])
def test_upload_saved_under_uploads(monkeypatch, tmp_path, part_name):
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    calls: list[dict] = []
    with TestClient(create_app(fake_agent([], calls))) as client:
        run_id = upload(client, part_name, model="custom/m").json()["run_id"]
        client.get(f"/api/runs/{run_id}/events")
    assert (tmp_path / "uploads" / "Träger-1.pdf").read_bytes() == DEMO_PDF.read_bytes()
    assert (calls[0]["pdf"], calls[0]["part"], calls[0]["backend"].model) == ("uploads/Träger-1.pdf", "Träger-1", "m")
    assert calls[0]["backend"].base_url == "http://llm.invalid/v1" and calls[0]["server"] == "stdio"


def test_upload_to_unwritable_data_folder_is_a_clear_error(monkeypatch, tmp_path):
    blocked = tmp_path / "data"
    blocked.write_text("a file, not a folder")
    monkeypatch.setenv("D2C_DATA", str(blocked))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(fake_agent([]))) as client:
        for _ in range(2):  # der Platz wird freigegeben: auch der zweite Versuch meldet den Grund, nicht „belegt“
            response = upload(client, "teil", model="custom/m")
            assert response.status_code == 500 and "could not be saved" in response.json()["error"]


@pytest.mark.parametrize("part, pdf", [("../evil", None), ("a b", None), ("", None), ("ok", b"not a pdf")])
def test_upload_rejects_bad_input(monkeypatch, tmp_path, part, pdf):
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(fake_agent([]))) as client:
        assert upload(client, part, pdf).status_code == 400
    assert not (tmp_path / "uploads").exists()


def test_upload_rejects_file_in_model_field(monkeypatch, tmp_path):
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    calls = []
    with TestClient(create_app(fake_agent([], calls))) as client:
        response = client.post(
            "/api/runs",
            files={
                "pdf": ("teil.pdf", DEMO_PDF.read_bytes(), "application/pdf"),
                "model": ("modell.txt", b"m", "text/plain"),
            },
            data={"part": "teil"},
        )
    assert response.status_code == 400 and "as text" in response.json()["error"]
    assert not calls and not (tmp_path / "uploads").exists()


def set_llm_env(monkeypatch, tmp_path, **env):
    """Nur die gegebenen LLM-Variablen sind gesetzt."""
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    for key in LLM_VARS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


NO_MODEL = "No language model available: start Ollama (`OLLAMA_CONTEXT_LENGTH=64000 ollama serve`), sign in to Claude Code or set `.env`. Without a model, `python -m d2c.demo` shows the flow."


def test_run_without_any_model_is_400_with_guidance(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path)
    monkeypatch.setattr("d2c.web.app._claude_login", lambda: False)
    calls: list[dict] = []
    with TestClient(create_app(fake_agent([], calls))) as client:
        response = upload(client)
    assert response.status_code == 400 and response.json()["error"] == NO_MODEL
    assert not calls and not (tmp_path / "uploads").exists()


def test_run_with_claude_login_and_no_other_settings_starts(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path)
    monkeypatch.setattr("d2c.web.app._claude_login", lambda: True)
    with TestClient(create_app(fake_agent([]))) as client:
        assert "run_id" in upload(client).json()


def test_run_with_model_settings_starts_without_login(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    monkeypatch.setattr("d2c.web.app._claude_login", lambda: False)
    with TestClient(create_app(fake_agent([]))) as client:
        assert "run_id" in upload(client).json()


def test_model_choices_and_their_settings(monkeypatch, tmp_path):
    set_llm_env(
        monkeypatch,
        tmp_path,
        D2C_LLM_BASE_URL="http://llm.invalid",
        D2C_LLM_MODEL="m",
        D2C_LLM_API_KEY="k",
        D2C_OLLAMA_URL="http://127.0.0.1:9",
    )
    with TestClient(create_app(fake_agent([]))) as client:
        values = [m["value"] for m in client.get("/api/models").json()["models"]]
    assert values[:3] == ["claude/claude-sonnet-5-5", "claude/claude-opus-5-5", "claude/claude-haiku-5-5"]
    assert values[3:] == ["custom/m"]  # auf Port 9 antwortet kein Ollama
    assert _agent_backends("claude/claude-haiku-5-5") == (Backend("claude-haiku-5-5"),) * 2
    assert _agent_backends("ollama/qwen3:8b") == (Backend("qwen3:8b", "http://127.0.0.1:9"),) * 2
    assert _agent_backends("custom/z-ai/glm") == (Backend("z-ai/glm", "http://llm.invalid", "k"),) * 2


def test_free_model_builds_with_the_build_model(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, D2C_LLM_BASE_URL="http://llm.invalid", D2C_LLM_MODEL="m", D2C_LLM_BUILD_MODEL="b")
    read, build = _agent_backends("freier-name")
    assert (read, build) == (Backend("freier-name", "http://llm.invalid"), Backend("b", "http://llm.invalid"))


def test_run_with_oauth_token_only_starts_with_model_none(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, CLAUDE_CODE_OAUTH_TOKEN="token")
    calls: list[dict] = []
    with TestClient(create_app(fake_agent([], calls))) as client:
        response = upload(client)
        assert response.status_code == 200
        client.get(f"/api/runs/{response.json()['run_id']}/events")
    assert calls[0]["backend"] == Backend()


def test_run_with_base_url_and_no_model_is_400(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, D2C_LLM_BASE_URL="http://llm.invalid/v1")
    with TestClient(create_app(fake_agent([]))) as client:
        response = upload(client)
    assert response.status_code == 400 and "model" in response.json()["error"]
    assert not (tmp_path / "uploads").exists()


def image_bytes(fmt: str) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 48), False)
    pixmap.set_rect(pixmap.irect, (200, 200, 200))
    return pixmap.tobytes(fmt)


def stored_pdf_pages(monkeypatch, tmp_path, content: bytes, filename: str) -> int:
    """Inhalt unter dem gegebenen Dateinamen hochladen, die Seitenzahl von uploads/scan.pdf zurückgeben."""
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    with TestClient(create_app(fake_agent([]))) as client:
        response = client.post("/api/runs", files={"pdf": (filename, content)}, data={"part": "scan"})
        assert response.status_code == 200, response.text
        client.get(f"/api/runs/{response.json()['run_id']}/events")
    saved = (tmp_path / "uploads" / "scan.pdf").read_bytes()
    assert saved.startswith(b"%PDF")
    with pymupdf.open(stream=saved, filetype="pdf") as pdf:
        return pdf.page_count


def test_upload_png_converted_to_pdf(monkeypatch, tmp_path):
    assert stored_pdf_pages(monkeypatch, tmp_path, image_bytes("png"), "scan.pdf") == 1  # der Endung wird nicht vertraut


def test_upload_jpg_converted_to_pdf(monkeypatch, tmp_path):
    assert stored_pdf_pages(monkeypatch, tmp_path, image_bytes("jpeg"), "scan.png") == 1


def test_upload_pdf_kept_whatever_its_extension(monkeypatch, tmp_path):
    assert stored_pdf_pages(monkeypatch, tmp_path, DEMO_PDF.read_bytes(), "drawing.png") == 2


def test_upload_broken_image_is_400(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    with TestClient(create_app(fake_agent([]))) as client:
        response = upload(client, pdf=b"\x89PNG\r\n\x1a\n" + b"not an image")
    assert response.status_code == 400 and "image" in response.json()["error"]
    assert not (tmp_path / "uploads").exists()


@pytest.mark.parametrize("excess", [1, 200_000], ids=["just_over", "far_over"])
def test_upload_too_large_is_413(monkeypatch, tmp_path, excess):
    """Knapp über der Grenze wird nach dem Lesen der Datei erwischt, weit über aus Content-Length, bevor er ausgespült ist."""
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    with TestClient(create_app(fake_agent([]))) as client:
        response = upload(client, pdf=b"%PDF" + bytes(20 * 1024 * 1024 - 4 + excess))
    assert response.status_code == 413 and "20 MB" in response.json()["error"]
    assert not (tmp_path / "uploads").exists()


def test_app_js_served(client):
    with client:
        response = client.get("/static/app.js")
    assert response.status_code == 200 and "javascript" in response.headers["content-type"]


def test_fonts_served(client):
    with client:
        for name in ("InterTight-wght.woff2", "InterTight-wght-italic.woff2", "InterTight-800.woff2", "JetBrainsMono-wght.woff2"):
            response = client.get(f"/static/vendor/fonts/{name}")
            assert response.status_code == 200 and response.content.startswith(b"wOF2"), name
            assert response.headers["content-type"] == "font/woff2"
        assert client.get("/static/vendor/fonts/OFL-InterTight.txt").status_code == 200
        assert client.get("/static/vendor/fonts/OFL-JetBrainsMono.txt").status_code == 200


def test_index_and_vendored_viewer_served(client):
    with client:
        html = client.get("/").text
        assert '<html lang="en">' in html
        assert "Drop drawing (PDF, PNG, JPG)" in html and "Part name" in html
        assert "Create STEP" in html and 'id="model"' in html and 'aria-label="Parts"' in html
        assert "model-viewer" in html
        assert client.get("/static/vendor/model-viewer.min.js").status_code == 200


def test_claude_choices_follow_the_login(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, D2C_OLLAMA_URL="http://127.0.0.1:9")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    claude = lambda: [m for m in _model_choices() if m["value"].startswith("claude/")]  # noqa: E731
    assert len(claude()) == 3 and all(m["disabled"] for m in claude())
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / ".credentials.json").write_text("{}")
    assert not any(m["disabled"] for m in claude()) and claude()[0]["group"] == "Claude"
    (tmp_path / ".claude" / ".credentials.json").unlink()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token")
    assert not any(m["disabled"] for m in claude())


# ---------------------------------------------------------------- Sicherheit und Betrieb


def test_file_route_does_not_follow_symlinks_out_of_the_part_folder(client, exported, tmp_path):
    secret = tmp_path / "geheim.txt"
    secret.write_text("SECRET")
    folder = exported / "plate"
    (folder / "linked.txt").symlink_to(secret)
    (folder / "inside.txt").symlink_to(folder / "spec.json")  # ein Symlink innerhalb des Ordners bleibt erlaubt
    try:
        with client:
            assert client.get("/files/plate/linked.txt").status_code == 404
            assert client.get("/files/plate/inside.txt").status_code == 200
    finally:
        (folder / "linked.txt").unlink()
        (folder / "inside.txt").unlink()


def blocking_agent(started: threading.Event, release: threading.Event):
    """Ein Ersatz-Agent, der läuft, bis der Test ihn freigibt."""
    import asyncio

    async def agent(**kwargs):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return {"ok": True}

    return agent


def wait_until(condition, seconds: float = 5) -> None:
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "Zeitüberschreitung"
        time.sleep(0.01)


def test_foreign_origin_is_refused_on_write_routes(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    calls: list[dict] = []
    with TestClient(create_app(fake_agent([], calls))) as client:
        foreign = client.post(
            "/api/runs", files={"pdf": ("a.pdf", DEMO_PDF.read_bytes())}, data={"part": "x"}, headers={"Origin": "http://evil.example"}
        )
        assert foreign.status_code == 403
        cross = client.post(
            "/api/runs", files={"pdf": ("a.pdf", DEMO_PDF.read_bytes())}, data={"part": "x"}, headers={"Sec-Fetch-Site": "cross-site"}
        )
        assert cross.status_code == 403
        null = client.post("/api/runs", files={"pdf": ("a.pdf", DEMO_PDF.read_bytes())}, data={"part": "x"}, headers={"Origin": "null"})
        assert null.status_code == 403
        assert not calls and not (tmp_path / "uploads").exists()
        own = client.post(
            "/api/runs",
            files={"pdf": ("a.pdf", DEMO_PDF.read_bytes())},
            data={"part": "x"},
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        )
        assert own.status_code == 200


def test_run_limit_answers_429_and_frees_the_slot(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    monkeypatch.setenv("D2C_MAX_RUNS", "2")
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    started, release = threading.Event(), threading.Event()
    with TestClient(create_app(blocking_agent(started, release))) as client:
        assert upload(client, "eins").status_code == 200
        assert upload(client, "zwei").status_code == 200
        third = upload(client, "drei")
        assert third.status_code == 429 and "2 runs" in third.json()["error"]
        assert not (tmp_path / "uploads" / "drei.pdf").exists()
        release.set()
        wait_until(lambda: all(r["status"] != "running" for r in client.get("/api/runs").json()))
        assert upload(client, "drei").status_code == 200


def test_same_part_twice_answers_409(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    started, release = threading.Event(), threading.Event()
    with TestClient(create_app(blocking_agent(started, release))) as client:
        assert upload(client, "Welle").status_code == 200
        again = upload(client, "welle")  # Groß- und Kleinschreibung zählt nicht
        assert again.status_code == 409 and "already in progress" in again.json()["error"]
        assert upload(client, "andere").status_code == 200
        release.set()


def test_runs_endpoint_lists_active_and_finished_runs(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    started, release = threading.Event(), threading.Event()
    with TestClient(create_app(blocking_agent(started, release))) as client:
        run_id = upload(client, "lang", model="custom/m").json()["run_id"]
        (row,) = client.get("/api/runs").json()
        assert (row["run_id"], row["part"], row["status"], row["model"]) == (run_id, "lang", "running", "m")
        assert row["started"]
        (entry,) = client.get("/api/parts/lang").json()["runs"]
        assert entry["running"] is True and entry["ok"] is None  # nicht "abgebrochen"
        release.set()
        wait_until(lambda: client.get("/api/runs").json()[0]["status"] == "ok")
        assert client.get("/api/parts/lang").json()["runs"][0]["running"] is False


def test_finished_runs_in_memory_are_capped(monkeypatch, tmp_path):
    import d2c.web.app as web

    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    monkeypatch.setattr(web, "KEEP_FINISHED", 2)
    with TestClient(create_app(fake_agent([{"type": "done", "ok": True}]))) as client:
        for n in range(4):
            client.get(f"/api/runs/{upload(client, f'teil{n}').json()['run_id']}/events")
        assert len(client.get("/api/runs").json()) <= 3


def test_unknown_model_is_400_known_one_is_accepted(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV, D2C_OLLAMA_URL="http://127.0.0.1:9")
    monkeypatch.setattr("d2c.web.app._claude_login", lambda: False)
    calls: list[dict] = []
    with TestClient(create_app(fake_agent([], calls))) as client:
        evil = upload(client, model="evil/xx")
        assert evil.status_code == 400 and "Unknown model" in evil.json()["error"]
        assert upload(client, model="claude/claude-sonnet-5-5").status_code == 400  # ohne Anmeldung gesperrt
        run_id = upload(client, model="custom/m").json()["run_id"]
        client.get(f"/api/runs/{run_id}/events")
    assert [call["backend"].model for call in calls] == ["m"]


def test_part_name_length_is_limited(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    with TestClient(create_app(fake_agent([]))) as client:
        assert upload(client, "a" * 300).status_code == 400
        assert upload(client, "ä" * 64).status_code == 200


def png_header(width: int, height: int) -> bytes:
    """Nur Signatur und IHDR: genug für die Größenprüfung, ohne ein riesiges Bild zu erzeugen."""
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x00\x00\x00\x00"


def test_pixel_bomb_is_413_before_conversion(monkeypatch, tmp_path):
    import d2c.web.app as web

    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    monkeypatch.setattr(web.pymupdf, "open", lambda *a, **k: pytest.fail("das Bild wurde entpackt"))
    with TestClient(create_app(fake_agent([]))) as client:
        response = upload(client, pdf=png_header(20000, 20000))
        assert response.status_code == 413 and "megapixels" in response.json()["error"]
        jpeg = b"\xff\xd8\xff\xe0\x00\x04ab\xff\xc0\x00\x0b\x08" + struct.pack(">HH", 30000, 30000) + b"\x01\x01\x11\x00"
        assert upload(client, pdf=jpeg).status_code == 413
    assert not (tmp_path / "uploads").exists()


def test_image_just_under_the_limit_is_converted(monkeypatch, tmp_path):
    assert stored_pdf_pages(monkeypatch, tmp_path, image_bytes("png"), "klein.png") == 1


@pytest.mark.parametrize("content", [b"%PDF-1.4\ngarbage", b"%PDF"], ids=["garbage", "bare_header"])
def test_damaged_pdf_is_400_at_upload(monkeypatch, tmp_path, content):
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    with TestClient(create_app(fake_agent([]))) as client:
        response = upload(client, pdf=content)
    assert response.status_code == 400 and "PDF" in response.json()["error"]
    assert not (tmp_path / "uploads").exists()


def test_encrypted_pdf_is_400_at_upload(monkeypatch, tmp_path):
    set_llm_env(monkeypatch, tmp_path, **LLM_ENV)
    with pymupdf.open(DEMO_PDF) as doc:
        locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="a", owner_pw="b")
    with TestClient(create_app(fake_agent([]))) as client:
        response = upload(client, pdf=locked)
    assert response.status_code == 400 and "password protected" in response.json()["error"]


def test_scale_conflict_only_counts_conflicts_about_the_scale(tmp_path, monkeypatch):
    def part(name, assumptions):
        folder = tmp_path / name
        folder.mkdir(parents=True)
        (folder / "review.json").write_text(json.dumps({"all_ok": True, "assumptions": assumptions}))

    part("massstab", [{"id": "r1", "issue": "conflict", "assumption": "Maßstab 1:1", "reason": "Schriftfeld nennt 1:1, 2:1 gezeichnet"}])
    part("scale", [{"id": "r1", "issue": "conflict", "assumption": "x", "reason": "title block scale differs"}])
    part("anderes", [{"id": "r1", "issue": "conflict", "assumption": "Ø44", "reason": "Zeichnung nennt 45"}])
    part("kein_konflikt", [{"id": "r1", "issue": "missing", "assumption": "Maßstab fehlt", "reason": "-"}])
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path))
    with TestClient(create_app()) as client:
        rows = {row["name"]: row["scale_conflict"] for row in client.get("/api/parts").json()}
    assert rows == {"massstab": True, "scale": True, "anderes": False, "kein_konflikt": False}


def test_part_detail_lists_versions(client, exported):
    versions = exported / "plate" / "versions"
    (versions / "2026-10-17T10-00-00").mkdir(parents=True)
    (versions / "2026-10-17T11-00-00").mkdir()
    try:
        with client:
            detail = client.get("/api/parts/plate").json()
            assert detail["versions"] == ["2026-10-17T11-00-00", "2026-10-17T10-00-00"]
            assert "versions" not in detail["files"]
    finally:
        shutil.rmtree(versions)
    with client:
        assert client.get("/api/parts/plate").json()["versions"] == []


def test_revise_needs_a_stored_part(client, monkeypatch, tmp_path):
    """Eine Revision braucht ein vorhandenes Teil mit Spezifikation und Vorgängen: sonst 400, kein Lauf und kein Upload."""
    calls: list[dict] = []
    with TestClient(create_app(fake_agent([], calls))) as app:
        response = upload(app, "nopart", revise="true")
        assert response.status_code == 400 and "no stored result" in response.json()["error"]
        assert not calls and not (tmp_path / "data" / "uploads").exists()
        assert upload(app, "../plate", revise="true").status_code == 400
        ok = upload(app, "plate", revise="true", model="custom/m")
        assert ok.status_code == 200
        app.get(f"/api/runs/{ok.json()['run_id']}/events")
    assert [c["revise"] for c in calls] == [True]


def test_run_without_revise_passes_false(client, tmp_path):
    calls: list[dict] = []
    with TestClient(create_app(fake_agent([], calls))) as app:
        app.get(f"/api/runs/{upload(app, 'plate', model='custom/m').json()['run_id']}/events")
    assert [c["revise"] for c in calls] == [False]


def test_default_host_is_loopback_and_compose_publishes_on_loopback():
    import re

    source = (Path(__file__).parents[1] / "d2c" / "web" / "__main__.py").read_text()
    assert re.search(r'"--host",\s*default="127\.0\.0\.1"', source)
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text()
    assert '"127.0.0.1:8080:8080"' in compose and compose.count("read_only: true") == 2


def test_frontend_matches_the_server_rules():
    """Teilname mit Unicode-Buchstaben, Maßstabswörter, Auto-Start, Wiederanhängen und Versionszeile stehen in app.js und index.html."""
    static = Path(__file__).parents[1] / "d2c" / "web" / "static"
    js, html = (static / "app.js").read_text(), (static / "index.html").read_text()
    assert r"[^\p{L}\p{N}_-]+" in js and r"^[\p{L}\p{N}_-]+$" in js and r"\w" not in js.split("const NAME_CHARS")[1][:200]
    assert "ma(?:ß|ss)stab|scale" in js and "part.scale_conflict" in js and 'includes("conflict")' not in js
    assert "form.requestSubmit()" in js and 'id="manual"' in html
    assert '"/api/runs"' in js and "attachActiveRun" in js and "earlier versions:" in js
    assert 'budget: "Cost limit reached"' in js
    assert 'id="revise"' in html and 'body.append("revise", "true")' in js and "detail.review?.changes" in js


def test_frontend_tells_a_failed_check_from_a_missing_export():
    """all_ok false heißt exportiert mit einer nicht bestandenen Prüfung, nicht "nicht exportiert"; die Dateiauswahl ist englisch beschriftet."""
    static = Path(__file__).parents[1] / "d2c" / "web" / "static"
    js, html = (static / "app.js").read_text(), (static / "index.html").read_text()
    assert js.count("all_ok != null") == 2 and "CHECK FAILED" in js and "const exported = part.all_ok === true" not in js
    assert '<input type="file" id="file" name="pdf" class="sr-only"' in html
    assert ">Choose file</label>" in html and "No file chosen" in html and "No file chosen" in js


def test_frontend_renders_the_tolerance_table():
    """Der Reiter Toleranzen liest review.tolerances und review.geometric_tolerances: Grenzen, Toleranzmitte und beide Urteile je Maß."""
    js = (Path(__file__).parents[1] / "d2c" / "web" / "static" / "app.js").read_text()
    assert '["tolerances", "Tolerances"]' in js and "tolerances: renderTolerances" in js
    assert "detail.review?.tolerances" in js and '"in_tolerance_nominal"' in js and '"in_tolerance_mid"' in js
    assert "detail.review.geometric_tolerances" in js and "t.in_step" in js
    assert "No tolerance table yet" in js and "No toleranced dimensions" in js


@pytest.mark.parametrize(
    "host, status",
    [
        ("localhost:8080", 200),
        ("127.0.0.1:8080", 200),
        ("[::1]:8080", 200),
        ("localhost", 200),
        ("evil.example", 400),
        ("evil.example:8080", 400),
    ],
)
def test_foreign_host_is_refused_on_get_routes(client, monkeypatch, host, status):
    """DNS-Rebinding: Die Seite einer fremden Domain, die auf 127.0.0.1 zeigt, darf nichts lesen."""
    monkeypatch.delenv("D2C_ALLOWED_HOSTS")
    with TestClient(create_app()) as local:
        assert local.get("/api/parts", headers={"Host": host}).status_code == status


def test_allowed_hosts_variable_adds_a_host(monkeypatch):
    monkeypatch.setenv("D2C_ALLOWED_HOSTS", "d2c.example, other.example")
    with TestClient(create_app()) as local:
        assert local.get("/api/parts", headers={"Host": "d2c.example"}).status_code == 200
        assert local.get("/api/parts", headers={"Host": "evil.example"}).status_code == 400

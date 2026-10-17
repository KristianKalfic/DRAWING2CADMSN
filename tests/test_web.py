"""Web app: part list and detail, file downloads, upload and live run events, on a really exported part."""
import json
import shutil
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from d2c.session import Session
from d2c.spec import PartSpec
from d2c.web.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"
PDF = FIXTURES / "demo_drawing.pdf"
LLM_ENV = {"D2C_LLM_BASE_URL": "http://llm.invalid/v1", "D2C_LLM_MODEL": "m"}


def _dimension(dim_id: str, kind: str, value: float, text: str, **more) -> dict:
    return {"id": dim_id, "kind": kind, "value": value, "text": text, "source": None, "confidence": 0.95} | more


SPEC = {
    "name": "plate", "drawing": "Demo/plate.pdf", "title": "Plate 80x50", "scale": "1:1", "views": [], "features": [],
    "dimensions": [
        _dimension("w", "length", 80, "80"),
        _dimension("d1", "diameter", 8, "Ø8"),
        _dimension("t", "length", 10, "", assumed=True, confidence=0.6, assumption_basis="read from the side view"),
    ],
    "expected": {"bbox": [80, 50, 10], "holes": [{"d": 8, "at": [20, 10, 0], "axis": [0, 0, 1], "dim_ids": ["d1"]}]},
    "review": [{"id": "rv1", "refs": ["t"], "issue": "missing", "assumption": "thickness 10 mm",
                "reason": "not dimensioned", "mark_near": [[0, 0, 10]]}],
    "general_tolerance": "ISO 2768-m",
}
OPS = [
    {"op": "extrude", "profile": {"type": "rect", "center": [0, 0], "w": 80, "h": 50},
     "plane": {"name": "XY"}, "distance": 10, "mode": "add"},
    {"op": "holes", "plane": {"name": "XY", "offset": 10}, "positions": [[20, 10]], "d": 8},
]


@pytest.fixture(scope="module")
def exported(tmp_path_factory) -> Path:
    """results folder with the demo part exported by a real session."""
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
    shutil.copy(PDF, data / "Demo" / "plate.pdf")
    monkeypatch.setenv("D2C_DATA", str(data))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    return TestClient(create_app())


def test_parts_list_and_detail(client):
    with client:
        (entry,) = client.get("/api/parts").json()
        assert (entry["name"], entry["title"], entry["review_count"]) == ("plate", "Plate 80x50", 1)
        assert "plate.step" in entry["files"] and entry["eval"] is None
        detail = client.get("/api/parts/plate").json()
        assert detail["spec"]["name"] == "plate"
        assert detail["review"]["assumptions"][0]["id"] == "rv1"
        assert detail["report"]["ok"] is True
        assert client.get("/api/parts/nope").status_code == 404


def test_eval_row_joins_by_part(client, exported):
    (exported / "EVAL.json").write_text(json.dumps([{"Teil": "other"}, {"Teil": "plate", "MATCH": "ok"}]))
    try:
        with client:
            assert client.get("/api/parts/plate").json()["eval"] == {"Teil": "plate", "MATCH": "ok"}
    finally:
        (exported / "EVAL.json").unlink()


def test_file_download_and_traversal_rejected(client):
    with client:
        step = client.get("/files/plate/plate.step")
        assert step.status_code == 200 and b"ISO-10303-21" in step.content
        for path in ("/files/plate/..%2Fspec.json", "/files/plate/%2e%2e%2fplate%2fspec.json", "/files/..%2Fplate/spec.json",
                     "/files/plate/..", "/files/plate/missing.step", "/files/plate/%5C..%5Cspec.json"):
            assert client.get(path).status_code == 404, path


def test_drawing_png(client):
    with client:
        response = client.get("/api/parts/plate/drawing.png")
        assert response.status_code == 200 and response.content.startswith(b"\x89PNG")


def fake_agent(events: list[dict], calls: list[dict] | None = None):
    async def agent(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        for event in events:
            kwargs["on_event"](event)
        return {"ok": True}

    return agent


def upload(client, part: str = "newpart", pdf: bytes | None = None, **extra):
    files = {"pdf": ("any.pdf", PDF.read_bytes() if pdf is None else pdf, "application/pdf")}
    return client.post("/api/runs", files=files, data={"part": part} | extra)


def test_run_streams_events(exported, monkeypatch, tmp_path):
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    events = [{"type": "tool_call", "name": "submit_spec"}, {"type": "tool_result", "name": "submit_spec"},
              {"type": "done", "ok": True}]
    with TestClient(create_app(fake_agent(events))) as client:
        run_id = upload(client).json()["run_id"]
        with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
            received = [json.loads(line[len("data: "):]) for line in stream.iter_lines() if line.startswith("data: ")]
    assert received == events


def test_run_appends_done_and_reports_agent_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)

    async def crashing(**kwargs):
        raise RuntimeError("endpoint unreachable")

    with TestClient(create_app(crashing)) as client:
        run_id = upload(client).json()["run_id"]
        body = client.get(f"/api/runs/{run_id}/events").text
    (last,) = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert last["type"] == "done" and not last["ok"] and "endpoint unreachable" in last["reason"]


def test_upload_saved_under_uploads(monkeypatch, tmp_path):
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    calls: list[dict] = []
    with TestClient(create_app(fake_agent([], calls))) as client:
        run_id = upload(client, "Träger-1", model="big-model").json()["run_id"]
        client.get(f"/api/runs/{run_id}/events")
    assert (tmp_path / "uploads" / "Träger-1.pdf").read_bytes() == PDF.read_bytes()
    assert (calls[0]["pdf"], calls[0]["part"], calls[0]["model"]) == ("uploads/Träger-1.pdf", "Träger-1", "big-model")
    assert calls[0]["base_url"] == "http://llm.invalid/v1" and calls[0]["server"] == "stdio"


@pytest.mark.parametrize("part, pdf", [("../evil", None), ("a b", None), ("", None), ("ok", b"not a pdf")])
def test_upload_rejects_bad_input(monkeypatch, tmp_path, part, pdf):
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    for key, value in LLM_ENV.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(fake_agent([]))) as client:
        assert upload(client, part, pdf).status_code == 400
    assert not (tmp_path / "uploads").exists()


def test_run_without_llm_endpoint_is_503(monkeypatch, tmp_path):
    monkeypatch.setenv("D2C_DATA", str(tmp_path))
    monkeypatch.delenv("D2C_LLM_BASE_URL", raising=False)
    with TestClient(create_app(fake_agent([]))) as client:
        response = upload(client)
    assert response.status_code == 503 and "D2C_LLM_BASE_URL" in response.json()["error"]


def test_index_and_vendored_viewer_served(client):
    with client:
        assert "model-viewer" in client.get("/").text
        assert client.get("/static/vendor/model-viewer.min.js").status_code == 200

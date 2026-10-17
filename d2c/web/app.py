"""The web app: results of every part, drawing render, and agent runs streamed as server-sent events."""

import asyncio
import json
import mimetypes
import os
import subprocess
import sys
import unicodedata
import urllib.request
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pymupdf
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from d2c.drawing import data_root, render
from d2c.session import PART_NAME, results_root

STATIC = Path(__file__).parent / "static"
MEDIA_TYPES = {".glb": "model/gltf-binary", ".step": "model/step", ".stp": "model/step", ".md": "text/markdown"}
SSE_POLL_SECONDS = 0.05  # ponytail: polling a list, a per-run queue only if many viewers matter
MAX_UPLOAD = 20 * 1024 * 1024
FORM_OVERHEAD = 64 * 1024  # what the other form fields and the multipart framing may add to a maximal upload
IMAGE_SIGNATURES = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpeg"}
CLAUDE_MODELS = (("claude-sonnet-5-5", "Claude Sonnet 5.5"), ("claude-opus-5-5", "Claude Opus 5.5"),
                 ("claude-haiku-5-5", "Claude Haiku 5.5"))
TOO_LARGE = "Die Datei ist größer als 20 MB"

mimetypes.add_type("font/woff2", ".woff2")  # the platform tables lack it, StaticFiles would send text/plain

Agent = Callable[..., Awaitable[dict]]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _safe_name(name: str) -> bool:
    """A single path component: no separator, no "..", no NUL, not empty."""
    return bool(name) and not any(bad in name for bad in ("/", "\\", "\0")) and ".." not in name


def _read_json(path: Path, expected: type[dict] | type[list] = dict) -> dict | list | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, expected) else None
    except (OSError, ValueError):
        return None


def _eval_row(part: str) -> dict | None:
    rows = _read_json(results_root() / "EVAL.json", list) or []
    return next((r for r in rows if isinstance(r, dict) and part in (r.get("Teil"), r.get("part"))), None)


def _summary(folder: Path) -> dict:
    spec = _read_json(folder / "spec.json") or {}
    review = _read_json(folder / "review.json") or {}
    assumptions = review.get("assumptions")
    return {
        "name": folder.name,
        "title": spec.get("title", folder.name),
        "all_ok": review.get("all_ok"),
        "review_count": len(assumptions) if isinstance(assumptions, list) else 0,
        "issues": sorted({a["issue"] for a in assumptions if isinstance(a, dict) and "issue" in a})
        if isinstance(assumptions, list) else [],
        "eval": _eval_row(folder.name),
        "files": sorted(p.name for p in folder.iterdir() if p.is_file()),
    }


def _part_folder(name: str) -> Path | None:
    folder = results_root() / name
    return folder if _safe_name(name) and folder.is_dir() else None


async def index(request: Request) -> Response:
    return FileResponse(STATIC / "index.html")


async def parts(request: Request) -> Response:
    root = results_root()
    folders = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    return JSONResponse([_summary(f) for f in folders])


async def part(request: Request) -> Response:
    folder = _part_folder(request.path_params["name"])
    if folder is None:
        return _error(404, "Teil nicht gefunden")
    return JSONResponse(_summary(folder) | {key: _read_json(folder / f"{key}.json") for key in ("spec", "review", "report")})


async def file(request: Request) -> Response:
    folder = _part_folder(request.path_params["name"])
    name = request.path_params["file"]
    if folder is None or not _safe_name(name) or not (folder / name).is_file():
        return _error(404, "Datei nicht gefunden")
    return FileResponse(folder / name, media_type=MEDIA_TYPES.get(Path(name).suffix))


async def drawing_png(request: Request) -> Response:
    folder = _part_folder(request.path_params["name"])
    spec = _read_json(folder / "spec.json") if folder else None
    if not isinstance(spec, dict) or not isinstance(spec.get("drawing"), str) or not spec["drawing"]:
        return _error(404, "Keine gültige Zeichnung für dieses Teil hinterlegt")
    try:
        png = await run_in_threadpool(render, spec["drawing"], spec.get("page", 1), 100)
    except ValueError as e:
        return _error(404, str(e))
    return Response(png, media_type="image/png")


class Run:
    """One agent run: its events so far and whether it has ended."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.finished = False
        self.task: asyncio.Task | None = None

    async def execute(self, agent: Agent, **kwargs: object) -> None:
        try:
            result = await agent(**kwargs, on_event=self.events.append)
            if not self.events or self.events[-1].get("type") != "done":
                self.events.append({"type": "done"} | result)
        except Exception as e:  # the viewer must see why a run died
            self.events.append({"type": "done", "ok": False, "reason": f"{type(e).__name__}: {e}"})
        finally:
            self.finished = True


def _default_agent(**kwargs: object) -> Awaitable[dict]:
    from d2c.agent import run_agent  # lazy: the web app starts without the agent module

    return run_agent(**kwargs)


def _ollama_url() -> str:
    return (os.environ.get("D2C_OLLAMA_URL") or "http://localhost:11434").rstrip("/")


def _ollama_models() -> list[str]:
    """The models of the Ollama at D2C_OLLAMA_URL, [] if it does not answer within 1.5 s."""
    try:
        with urllib.request.urlopen(_ollama_url() + "/api/tags", timeout=1.5) as response:
            return sorted(m["name"] for m in json.load(response)["models"])
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _claude_login() -> bool:
    """Ob der Agent Claude erreicht: Schlüssel oder Token in der Umgebung oder die Anmeldung von Claude Code auf diesem
    Rechner (unter Linux eine Datei, unter macOS ein Schlüsselbund-Eintrag, der nur gesucht und nicht gelesen wird)."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    if (Path.home() / ".claude" / ".credentials.json").is_file():
        return True
    if sys.platform != "darwin":
        return False
    try:
        return subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials"],
                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _model_choices() -> list[dict]:
    """Was das Modellfeld anbietet: Wert "<backend>/<modell>", Beschriftung, Gruppe und ob die Wahl gesperrt ist."""
    login = _claude_login()
    group = "Claude" if login else "Claude (keine Anmeldung gefunden)"
    choices = [{"value": f"claude/{m}", "label": label, "group": group, "disabled": not login}
               for m, label in CLAUDE_MODELS]
    base_url, model = os.environ.get("D2C_LLM_BASE_URL"), os.environ.get("D2C_LLM_MODEL")
    if base_url and model:
        choices.append({"value": f"custom/{model}", "label": model, "group": base_url})
    return choices + [{"value": f"ollama/{m}", "label": m, "group": "Ollama (lokal)"} for m in _ollama_models() if "embed" not in m]


async def models(request: Request) -> Response:
    return JSONResponse({"models": await run_in_threadpool(_model_choices)})


def _agent_settings(model: str | None) -> dict[str, object]:
    """What the agent gets for a run. A choice "claude/<m>" uses the Claude login or Anthropic key of the agent,
    "ollama/<m>" the Ollama at D2C_OLLAMA_URL; anything else (also "custom/<m>" and a free model name) the endpoint
    of D2C_LLM_BASE_URL, or Anthropic without it. Empty values count as unset."""
    backend, _, name = (model or "").partition("/")
    server = os.environ.get("D2C_MCP_URL", "stdio")
    if backend == "claude" and name:
        return {"model": name, "base_url": None, "api_key": None, "build_model": None, "server": server}
    if backend == "ollama" and name:
        return {"model": name, "base_url": _ollama_url(), "api_key": None, "build_model": None, "server": server}
    return {
        "model": (name if backend == "custom" and name else model) or os.environ.get("D2C_LLM_MODEL") or None,
        "base_url": os.environ.get("D2C_LLM_BASE_URL") or None,
        "api_key": os.environ.get("D2C_LLM_API_KEY") or None,
        "build_model": os.environ.get("D2C_LLM_BUILD_MODEL") or None,
        "server": server,
    }


def _as_pdf(content: bytes) -> bytes:
    """The upload as PDF bytes: a PDF as it is, a PNG or JPG (told by signature, not by extension) as a
    one-page PDF. ValueError with the reason for anything else."""
    if content.startswith(b"%PDF"):
        return content
    kind = next((kind for signature, kind in IMAGE_SIGNATURES.items() if content.startswith(signature)), None)
    if kind is None:
        raise ValueError("Die hochgeladene Datei ist keine PDF-, PNG- oder JPG-Datei")
    try:
        with pymupdf.open(stream=content, filetype=kind) as image:
            return image.convert_to_pdf()
    except (RuntimeError, pymupdf.mupdf.FzErrorBase) as e:  # ponytail: no pixel limit, a PNG bomb within 20 MB can cost memory
        raise ValueError(f"Das hochgeladene Bild konnte nicht gelesen werden: {e}") from e


async def start_run(request: Request) -> Response:
    declared = request.headers.get("content-length", "")
    # ponytail: a body without Content-Length is only checked after it is spooled
    if declared.isdigit() and int(declared) > MAX_UPLOAD + FORM_OVERHEAD:
        return _error(413, TOO_LARGE)
    async with request.form() as form:  # reads the whole body first: an answer sent before it ends can reset the connection
        upload, model = form.get("pdf"), form.get("model") or None
        name = unicodedata.normalize("NFC", str(form.get("part", "")))
        if not hasattr(upload, "read"):
            return _error(400, "Eine Zeichnungsdatei (PDF, PNG oder JPG) ist erforderlich (Formularfeld 'pdf')")
        if not PART_NAME.fullmatch(name):
            return _error(400, "Der Teilname darf nur Buchstaben, Ziffern, '_' und '-' enthalten")
        if model is not None and not isinstance(model, str):
            return _error(400, "Das Modell muss als Text angegeben werden (Formularfeld 'model')")
        content = await upload.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        return _error(413, TOO_LARGE)
    try:
        content = await run_in_threadpool(_as_pdf, content)
    except ValueError as e:
        return _error(400, str(e))
    settings = _agent_settings(str(model) if model else None)
    if settings["base_url"] and not settings["model"]:
        return _error(400, "Kein Modell angegeben: Modell auswählen oder D2C_LLM_MODEL setzen")
    target = data_root() / "uploads" / f"{name}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    run, run_id = Run(), uuid.uuid4().hex
    run.task = asyncio.create_task(run.execute(request.app.state.agent, pdf=f"uploads/{name}.pdf", part=name, **settings))
    request.app.state.runs[run_id] = run
    return JSONResponse({"run_id": run_id})


async def _stream(run: Run) -> AsyncIterator[str]:
    sent = 0
    while True:
        finished = run.finished  # read before the events: nothing can arrive after it
        while sent < len(run.events):
            yield f"data: {json.dumps(run.events[sent], ensure_ascii=False)}\n\n"
            sent += 1
        if finished:
            return
        await asyncio.sleep(SSE_POLL_SECONDS)


async def run_events(request: Request) -> Response:
    run = request.app.state.runs.get(request.path_params["run_id"])
    if run is None:
        return _error(404, "Lauf nicht gefunden")
    return StreamingResponse(_stream(run), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def create_app(agent: Agent | None = None) -> Starlette:
    """The app; `agent` replaces d2c.agent.run_agent (tests inject a fake)."""
    app = Starlette(routes=[
        Route("/", index),
        Route("/api/models", models),
        Route("/api/parts", parts),
        Route("/api/parts/{name}", part),
        Route("/api/parts/{name}/drawing.png", drawing_png),
        Route("/api/runs", start_run, methods=["POST"]),
        Route("/api/runs/{run_id}/events", run_events),
        Route("/files/{name}/{file}", file),
        Mount("/static", StaticFiles(directory=STATIC)),
    ])
    app.state.agent = agent or _default_agent
    app.state.runs = {}
    return app

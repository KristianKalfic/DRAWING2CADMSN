"""The web app: results of every part, drawing render, and agent runs streamed as server-sent events."""

import asyncio
import json
import mimetypes
import os
import unicodedata
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
LLM_ACCESS = ("D2C_LLM_BASE_URL", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")  # one of them lets a run start
NO_LLM = f"no LLM access configured: set {', '.join(LLM_ACCESS[:-1])} or {LLM_ACCESS[-1]} (see .env.example)"
TOO_LARGE = "the file is larger than 20 MB"

mimetypes.add_type("font/woff2", ".woff2")  # the platform tables lack it, StaticFiles would send text/plain

Agent = Callable[..., Awaitable[dict]]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _safe_name(name: str) -> bool:
    """A single path component: no separator, no "..", no NUL, not empty."""
    return bool(name) and not any(bad in name for bad in ("/", "\\", "\0")) and ".." not in name


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _eval_row(part: str) -> dict | None:
    rows = _read_json(results_root() / "EVAL.json")
    return next((r for r in rows if part in (r.get("Teil"), r.get("part"))), None) if isinstance(rows, list) else None


def _summary(folder: Path) -> dict:
    spec = _read_json(folder / "spec.json") or {}
    review = _read_json(folder / "review.json") or {}
    return {
        "name": folder.name,
        "title": spec.get("title", folder.name),
        "all_ok": review.get("all_ok"),
        "review_count": len(review.get("assumptions", [])),
        "issues": sorted({a["issue"] for a in review.get("assumptions", [])}),
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
    if not isinstance(spec, dict) or "drawing" not in spec:
        return _error(404, "Keine Spezifikation für dieses Teil vorhanden")
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


def _agent_settings(model: str | None) -> dict[str, object]:
    """What the agent gets from the environment; empty values count as unset. Without D2C_LLM_BASE_URL the
    agent talks to Anthropic with its own credentials and picks the model itself."""
    return {
        "model": model or os.environ.get("D2C_LLM_MODEL") or None,
        "base_url": os.environ.get("D2C_LLM_BASE_URL") or None,
        "api_key": os.environ.get("D2C_LLM_API_KEY") or None,
        "build_model": os.environ.get("D2C_LLM_BUILD_MODEL") or None,
        "server": os.environ.get("D2C_MCP_URL", "stdio"),
    }


def _as_pdf(content: bytes) -> bytes:
    """The upload as PDF bytes: a PDF as it is, a PNG or JPG (told by signature, not by extension) as a
    one-page PDF. ValueError with the reason for anything else."""
    if content.startswith(b"%PDF"):
        return content
    kind = next((kind for signature, kind in IMAGE_SIGNATURES.items() if content.startswith(signature)), None)
    if kind is None:
        raise ValueError("the uploaded file is not a PDF, PNG or JPG")
    try:
        with pymupdf.open(stream=content, filetype=kind) as image:
            return image.convert_to_pdf()
    except (RuntimeError, pymupdf.mupdf.FzErrorBase) as e:  # ponytail: no pixel limit, a PNG bomb within 20 MB can cost memory
        raise ValueError(f"the uploaded image could not be read: {e}") from e


async def start_run(request: Request) -> Response:
    declared = request.headers.get("content-length", "")
    # ponytail: a body without Content-Length is only checked after it is spooled
    if declared.isdigit() and int(declared) > MAX_UPLOAD + FORM_OVERHEAD:
        return _error(413, TOO_LARGE)
    async with request.form() as form:  # reads the whole body first: an answer sent before it ends can reset the connection
        if not any(os.environ.get(key) for key in LLM_ACCESS):
            return _error(503, NO_LLM)
        upload, model = form.get("pdf"), form.get("model") or None
        name = unicodedata.normalize("NFC", str(form.get("part", "")))
        if not hasattr(upload, "read"):
            return _error(400, "a drawing file (PDF, PNG or JPG) is required (form field 'pdf')")
        if not PART_NAME.fullmatch(name):
            return _error(400, "part name may only contain letters, digits, '_' and '-'")
        content = await upload.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        return _error(413, TOO_LARGE)
    try:
        content = await run_in_threadpool(_as_pdf, content)
    except ValueError as e:
        return _error(400, str(e))
    settings = _agent_settings(str(model) if model else None)
    if settings["base_url"] and not settings["model"]:
        return _error(400, "no model given: fill the model field or set D2C_LLM_MODEL")
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

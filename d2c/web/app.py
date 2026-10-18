"""Web-App mit Ergebnissen aller Bauteile, Zeichnungsansicht und Agentenläufen als Server-Sent Events."""

import asyncio
import json
import logging
import mimetypes
import os
import re
import struct
import subprocess
import sys
import time
import unicodedata
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

import pymupdf
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from d2c.agent import trace
from d2c.agent.backend import Backend
from d2c.config import VERSIONS, allowed_hosts, data_root, llm_settings, max_runs, mcp_url, ollama_url, results_root
from d2c.drawing.pages import render
from d2c.spec import PART_NAME

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
MEDIA_TYPES = {".glb": "model/gltf-binary", ".step": "model/step", ".stp": "model/step", ".md": "text/markdown"}
NO_MODEL = (
    "No language model available: start Ollama (`OLLAMA_CONTEXT_LENGTH=64000 ollama serve`), sign in to Claude Code "
    "or set `.env`. Without a model, `python -m d2c.demo` shows the flow."
)
SSE_POLL_SECONDS = 0.05  # Grenze: Polen einer Liste, Warteschlange je Lauf nur, wenn viele Betrachter wichtig sind
MAX_UPLOAD = 20 * 1024 * 1024
FORM_OVERHEAD = 64 * 1024  # was die übrigen Formularfelder und der Multipart-Rahmen zu einem größten Upload hinzufügen dürfen
IMAGE_SIGNATURES = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpeg"}
MAX_PIXELS = 50_000_000  # Bildpunkte eines hochgeladenen PNG oder JPG; größere Bilder werden vor dem Entpacken abgelehnt
MAX_NAME = 64  # Zeichen im Teilnamen (längere Namen scheitern sonst als Dateiname mit 500)
KEEP_FINISHED = 20  # beendete Läufe, die /api/runs und die Ereignisse im Speicher behalten
SCALE_WORDS = re.compile(r"ma(?:ß|ss)stab|scale", re.IGNORECASE)  # dieselbe Wortsuche wie app.js
CLAUDE_MODELS = (
    ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("claude-opus-5-5", "Claude Opus 5.5"),
    ("claude-haiku-5-5", "Claude Haiku 5.5"),
)
TOO_LARGE = "The file is larger than 20 MB"


class TooLarge(ValueError):
    """Der Upload überschreitet eine Grenze (Dateigröße oder Bildpunkte): Antwort 413 statt 400."""


mimetypes.add_type("font/woff2", ".woff2")  # die Tabellen der Plattform kennen es nicht, StaticFiles würde text/plain senden

Agent = Callable[..., Awaitable[dict]]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _safe_name(name: str) -> bool:
    """Ein einzelner Pfadbestandteil: kein Trennzeichen, kein "..", kein NUL und nicht leer."""
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


def _is_scale_conflict(entry: object) -> bool:
    """Ob ein Prüfpunkt ein Widerspruch zum Maßstab ist: `issue=conflict` und das Wort Maßstab oder scale im Text."""
    return (
        isinstance(entry, dict)
        and entry.get("issue") == "conflict"
        and bool(SCALE_WORDS.search(f"{entry.get('assumption', '')} {entry.get('reason', '')}"))
    )


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
        if isinstance(assumptions, list)
        else [],
        "scale_conflict": isinstance(assumptions, list) and any(_is_scale_conflict(a) for a in assumptions),
        "eval": _eval_row(folder.name),
        "files": sorted(p.name for p in folder.iterdir() if p.is_file()),
    }


def _part_folder(name: str) -> Path | None:
    folder = results_root() / name
    return folder if _safe_name(name) and folder.is_dir() else None


def _revisable(name: str) -> bool:
    """Ob das Teil ein gespeichertes Ergebnis mit Spezifikation und Vorgangsliste hat, das sich auf eine neue Revision umstellen lässt."""
    folder = _part_folder(name)
    return folder is not None and (folder / "spec.json").is_file() and (folder / "ops.json").is_file()


def _trace_lines(path: Path) -> list[dict]:
    """Die Zeilen eines Laufprotokolls; unlesbare und abgerissene Zeilen werden übersprungen."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            lines.append(event)
    return lines


def _versions(folder: Path) -> list[str]:
    """Die früheren Versionen eines Teils (Zeitstempel), neueste zuerst."""
    root = folder / VERSIONS
    return sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True) if root.is_dir() else []


def _run_entry(path: Path, active: frozenset[str] = frozenset()) -> dict:
    """Eine Zeile je Lauf aus seiner Protokolldatei: Kopfzeile (Beginn, Teil, Modell) und Abschlussergebnis.

    Ohne Abschlusszeile bleiben `ok` und `reason` null. `running` ist wahr, wenn dieser Server den Lauf noch ausführt
    (sonst brach der Server mitten im Lauf ab).
    """
    lines = _trace_lines(path)
    meta = next((line for line in lines if line.get("type") == "meta"), {})
    events = [line for line in lines if line.get("type") != "meta"]
    done = events[-1] if events and events[-1].get("type") == "done" else None
    return {
        "id": path.stem,
        "started": meta.get("started"),
        "part": meta.get("part"),
        "pdf": meta.get("pdf"),
        "model": meta.get("model"),
        "events": len(events),
        "running": meta.get("run_id") in active and done is None,
        "ok": done.get("ok") if done else None,
        "reason": done.get("reason") if done else None,
    }


def _runs(folder: Path, active: frozenset[str] = frozenset()) -> list[dict]:
    """Die Laufprotokolle eines Teils, neueste zuerst (die Dateinamen beginnen mit dem Zeitpunkt)."""
    return [_run_entry(path, active) for path in sorted((folder / trace.RUNS).glob("*.jsonl"), reverse=True)]


def _active_ids(request: Request) -> frozenset[str]:
    return frozenset(run_id for run_id, run in request.app.state.runs.items() if not run.finished)


async def index(request: Request) -> Response:
    return FileResponse(STATIC / "index.html")


async def parts(request: Request) -> Response:
    root = results_root()
    folders = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    return JSONResponse([_summary(f) for f in folders])


async def part(request: Request) -> Response:
    folder = _part_folder(request.path_params["name"])
    if folder is None:
        return _error(404, "Part not found")
    return JSONResponse(
        _summary(folder)
        | {"runs": _runs(folder, _active_ids(request)), "versions": _versions(folder)}
        | {key: _read_json(folder / f"{key}.json") for key in ("spec", "review", "report")}
    )


async def file(request: Request) -> Response:
    folder = _part_folder(request.path_params["name"])
    name = request.path_params["file"]
    if folder is None or not _safe_name(name):
        return _error(404, "File not found")
    path = (folder / name).resolve()  # ein Symlink darf nicht aus dem Teileordner hinausführen
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        return _error(404, "File not found")
    return FileResponse(path, media_type=MEDIA_TYPES.get(Path(name).suffix))


async def drawing_png(request: Request) -> Response:
    folder = _part_folder(request.path_params["name"])
    spec = _read_json(folder / "spec.json") if folder else None
    if not isinstance(spec, dict) or not isinstance(spec.get("drawing"), str) or not spec["drawing"]:
        return _error(404, "No valid drawing stored for this part")
    try:
        png = await run_in_threadpool(render, spec["drawing"], spec.get("page", 1), 100)
    except ValueError as e:
        return _error(404, str(e))
    return Response(png, media_type="image/png")


class Run:
    """Ein Agentenlauf: Ereignisse für die Ansicht und als Protokoll unter results/<teil>/runs/."""

    def __init__(self, folder: Path, meta: dict, release: Callable[[], None]) -> None:
        self.meta = meta
        self.events: list[dict] = []
        self.finished = False
        self.ended = 0.0  # time.monotonic() beim Ende des Laufs
        self.task: asyncio.Task | None = None
        self.release = release  # gibt den Platz unter den gleichzeitigen Läufen frei
        self.trace = trace.Trace(folder, self.meta)

    @property
    def status(self) -> str:
        """ "running", solange der Lauf läuft, danach "ok" oder "failed" nach seinem Abschlussereignis."""
        if not self.finished:
            return "running"
        done = self.events[-1] if self.events and self.events[-1].get("type") == "done" else {}
        return "ok" if done.get("ok") is True else "failed"

    def summary(self) -> dict:
        return {
            "run_id": self.meta.get("run_id"),
            "part": self.meta.get("part"),
            "started": self.meta.get("started"),
            "model": self.meta.get("model"),
            "pdf": self.meta.get("pdf"),
            "status": self.status,
            "events": len(self.events),
        }

    def event(self, event: dict) -> None:
        """Ein Ereignis übernehmen: für die Ansicht und mit Zeitversatz als Zeile ins Protokoll."""
        self.events.append(event)
        self.trace.event(event)

    async def execute(self, agent: Agent, **kwargs: object) -> None:
        try:
            result = await agent(**kwargs, on_event=self.event)
            if not self.events or self.events[-1].get("type") != "done":
                self.event({"type": "done"} | result)
        except Exception as e:  # Die Webansicht muss den Grund für einen abgebrochenen Lauf anzeigen.
            log.exception("Agentenlauf abgebrochen")
            self.event({"type": "done", "ok": False, "reason": f"{type(e).__name__}: {e}"})
        finally:
            self.finished = True
            self.ended = time.monotonic()
            self.trace.close()
            self.release()


def _default_agent(**kwargs: object) -> Awaitable[dict]:
    from d2c.agent.runner import (
        run_agent,  # Erst bei Bedarf importieren, damit die Webansicht auch ohne das Agentenmodul startet.
    )

    return run_agent(**kwargs)


def _ollama_models() -> list[str]:
    """Die Modelle des Ollama unter D2C_OLLAMA_URL, [] wenn er nicht innerhalb von 1,5 s antwortet."""
    try:
        with urllib.request.urlopen(ollama_url() + "/api/tags", timeout=1.5) as response:
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
        return (
            subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials"], capture_output=True, timeout=5
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def _model_choices() -> list[dict]:
    """Was das Modellfeld anbietet: Wert "<backend>/<modell>", Beschriftung, Gruppe und ob die Wahl gesperrt ist."""
    login = _claude_login()
    group = "Claude" if login else "Claude (not signed in)"
    choices = [{"value": f"claude/{m}", "label": label, "group": group, "disabled": not login} for m, label in CLAUDE_MODELS]
    llm = llm_settings()
    base_url, model = llm["base_url"], llm["model"]
    if base_url and model:
        choices.append({"value": f"custom/{model}", "label": model, "group": base_url})
    return choices + [{"value": f"ollama/{m}", "label": m, "group": "Ollama (local)"} for m in _ollama_models() if "embed" not in m]


async def models(request: Request) -> Response:
    return JSONResponse({"models": await run_in_threadpool(_model_choices)})


def _agent_backends(model: str | None) -> tuple[Backend, Backend]:
    """Das Lese- und das Baubackend eines Laufs. Die Wahl "claude/<m>" nutzt die Claude-Anmeldung oder den
    Anthropic-Schlüssel des Agents, "ollama/<m>" das Ollama unter D2C_OLLAMA_URL; alles andere (auch "custom/<m>" und ein
    freier Modellname) den Endpunkt von D2C_LLM_BASE_URL, sonst Anthropic, und baut mit D2C_LLM_BUILD_MODEL, wenn gesetzt.
    Leere Werte gelten als nicht gesetzt."""
    kind, _, name = (model or "").partition("/")
    if kind == "claude" and name:
        backend = Backend(name)
    elif kind == "ollama" and name:
        backend = Backend(name, ollama_url())
    else:
        llm = llm_settings()
        backend = Backend((name if kind == "custom" and name else model) or llm["model"], llm["base_url"], llm["api_key"])
        return backend, backend.for_build(llm["build_model"], None)
    return backend, backend


def _image_pixels(content: bytes, kind: str) -> int:
    """Breite mal Höhe eines PNG oder JPG aus dem Kopf der Datei, ohne das Bild zu entpacken. ValueError, wenn er fehlt."""
    if kind == "png":
        if len(content) >= 24 and content[12:16] == b"IHDR":
            width, height = struct.unpack(">II", content[16:24])
            return width * height
    else:
        at = 2
        while at + 9 <= len(content):
            if content[at] != 0xFF:
                break
            marker = content[at + 1]
            if marker == 0xFF:  # Füllbyte vor einem Marker
                at += 1
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # SOFn: Höhe und Breite
                height, width = struct.unpack(">HH", content[at + 5 : at + 9])
                return width * height
            at += 2 + struct.unpack(">H", content[at + 2 : at + 4])[0]
    raise ValueError("The uploaded image could not be read: the image size is missing from the file header")


def _as_pdf(content: bytes) -> bytes:
    """Den Upload als PDF-Bytes: eine PDF unverändert, ein PNG oder JPG (an der Signatur erkannt, nicht an der
    Endung) als einseitige PDF. ValueError mit dem Grund bei allem anderen, TooLarge bei mehr als MAX_PIXELS Bildpunkten.
    Eine PDF wird dazu geöffnet: beschädigte, verschlüsselte und leere Dateien werden schon hier abgelehnt."""
    if content.startswith(b"%PDF"):
        try:
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                if pdf.needs_pass:
                    raise ValueError("The PDF file is password protected")
                if pdf.page_count < 1:
                    raise ValueError("The PDF file contains no page")
        except (RuntimeError, pymupdf.mupdf.FzErrorBase) as e:  # FileDataError ist eine RuntimeError
            raise ValueError(f"The PDF file is damaged and could not be read: {e}") from e
        return content
    kind = next((kind for signature, kind in IMAGE_SIGNATURES.items() if content.startswith(signature)), None)
    if kind is None:
        raise ValueError("The uploaded file is not a PDF, PNG or JPG file")
    pixels = _image_pixels(content, kind)  # vor dem Entpacken: eine kleine Datei kann Gigabyte an Bildpunkten enthalten
    if pixels > MAX_PIXELS:
        raise TooLarge(
            f"The image has {pixels / 1e6:.0f} megapixels, {MAX_PIXELS // 1_000_000} are allowed: "
            "please save it at a lower resolution or upload it as a PDF"
        )
    try:
        with pymupdf.open(stream=content, filetype=kind) as image:
            return image.convert_to_pdf()
    except (RuntimeError, pymupdf.mupdf.FzErrorBase) as e:  # Grenze: keine Pixelgrenze, eine PNG-Bombe in 20 MB kann Speicher kosten
        raise ValueError(f"The uploaded image could not be read: {e}") from e


def _origin_allowed(request: Request) -> bool:
    """Schreibende Routen nehmen nur Anfragen der eigenen Seite an. Ein Browser sendet bei einem Formular von einer
    fremden Seite den Header Origin (oder Sec-Fetch-Site: cross-site); dessen Host muss der Host dieser Anfrage sein.
    Ohne beide Header (curl, Skripte, Tests) gibt es keinen Browserkontext, der missbraucht werden könnte."""
    origin = request.headers.get("origin")
    if origin is not None:
        return origin != "null" and urlsplit(origin).netloc == request.headers.get("host")
    return request.headers.get("sec-fetch-site", "same-origin") in ("same-origin", "none")


def _active_part(request: Request, name: str) -> bool:
    """Ob für dieses Teil (Groß- und Kleinschreibung zählt nicht) ein Lauf aktiv ist."""
    key = name.casefold()
    return any(not run.finished and str(run.meta.get("part", "")).casefold() == key for run in request.app.state.runs.values())


def _forget_old_runs(runs: dict[str, "Run"]) -> None:
    """Behält nur die letzten KEEP_FINISHED beendeten Läufe im Speicher (ihre Protokolle bleiben auf der Platte)."""
    finished = sorted((r.ended, run_id) for run_id, r in runs.items() if r.finished)
    for _, run_id in finished[:-KEEP_FINISHED]:
        del runs[run_id]


async def start_run(request: Request) -> Response:
    if not _origin_allowed(request):
        return _error(403, "Request from a foreign site rejected")
    declared = request.headers.get("content-length", "")
    # Grenze: ein Body ohne Content-Length wird erst geprüft, wenn er ausgespült ist
    if declared.isdigit() and int(declared) > MAX_UPLOAD + FORM_OVERHEAD:
        return _error(413, TOO_LARGE)
    slots: asyncio.Semaphore = request.app.state.slots
    async with request.form() as form:  # liest erst den ganzen Body: eine Antwort vor dem Ende kann die Verbindung zurücksetzen
        upload, model = form.get("pdf"), form.get("model") or None
        revise = form.get("revise") == "true"
        name = unicodedata.normalize("NFC", str(form.get("part", "")))
        if not hasattr(upload, "read"):
            return _error(400, "A drawing file (PDF, PNG or JPG) is required (form field 'pdf')")
        if not PART_NAME.fullmatch(name) or len(name) > MAX_NAME:
            return _error(400, f"The part name may only contain letters, digits, '_' and '-' (at most {MAX_NAME} characters)")
        if model is not None and not isinstance(model, str):
            return _error(400, "The model must be given as text (form field 'model')")
        content = await upload.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        return _error(413, TOO_LARGE)
    if model is not None:
        choice = next((c for c in await run_in_threadpool(_model_choices) if c["value"] == model), None)
        if choice is None:
            return _error(400, f"Unknown model {model!r}; allowed are the entries of /api/models")
        if choice.get("disabled"):
            return _error(400, f"The model {model!r} is not available: no Claude login found")
    if revise and not _revisable(name):
        return _error(
            400,
            f"Cannot update part {name!r} to a new revision: there is no stored result with spec and ops. Upload it without the revision option first",
        )
    busy = _busy(request, name)
    if busy:
        return busy
    try:
        content = await run_in_threadpool(_as_pdf, content)
    except TooLarge as e:
        return _error(413, str(e))
    except ValueError as e:
        return _error(400, str(e))
    backend, build = _agent_backends(str(model) if model else None)
    try:
        backend.check()
    except ValueError as e:
        return _error(400, str(e))
    if not backend.base_url and not await run_in_threadpool(_claude_login):  # ohne Endpunkt läuft der Agent über Claude
        return _error(400, NO_MODEL)
    busy = _busy(request, name)  # die Umwandlung hat gewartet: die Lage kann sich geändert haben
    if busy:
        return busy
    await slots.acquire()  # frei (gerade geprüft, kein Warten dazwischen): belegt den Platz bis zum Ende des Laufs
    rel_pdf = f"uploads/{name}.pdf"
    try:
        target = data_root() / rel_pdf
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        meta = trace.new_meta(name, rel_pdf, backend.model)
        run = Run(results_root() / name, meta, release=slots.release)
    except OSError as e:  # etwa ein schreibgeschützter oder fehlender Datenordner
        slots.release()
        return _error(500, f"The drawing could not be saved: {e.strerror or e}")
    except BaseException:
        slots.release()
        raise
    _forget_old_runs(request.app.state.runs)
    request.app.state.runs[meta["run_id"]] = run
    run.task = asyncio.create_task(
        run.execute(request.app.state.agent, pdf=rel_pdf, part=name, backend=backend, build=build, server=mcp_url(), revise=revise)
    )
    return JSONResponse({"run_id": meta["run_id"]})


def _busy(request: Request, name: str) -> Response | None:
    """Die Ablehnung, wenn für dieses Teil schon ein Lauf aktiv ist (409) oder alle Plätze belegt sind (429)."""
    if _active_part(request, name):
        return _error(409, f"A run is already in progress for part {name!r}; please wait until it has finished")
    if request.app.state.slots.locked():
        return _error(429, f"{request.app.state.max_runs} runs are already in progress; please wait until one has finished")
    return None


async def runs(request: Request) -> Response:
    """Die aktiven und zuletzt beendeten Läufe dieses Servers, neueste zuerst."""
    rows = [run.summary() for run in request.app.state.runs.values()]
    return JSONResponse(sorted(rows, key=lambda r: r["started"] or "", reverse=True))


async def _stream(run: Run) -> AsyncIterator[str]:
    sent = 0
    while True:
        finished = run.finished  # Zuerst lesen, dann Ereignisse senden: Danach kann nichts mehr eintreffen.
        while sent < len(run.events):
            yield f"data: {json.dumps(run.events[sent], ensure_ascii=False)}\n\n"
            sent += 1
        if finished:
            return
        await asyncio.sleep(SSE_POLL_SECONDS)


async def run_events(request: Request) -> Response:
    run = request.app.state.runs.get(request.path_params["run_id"])
    if run is None:
        return _error(404, "Run not found")
    return StreamingResponse(_stream(run), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


async def run_trace(request: Request) -> Response:
    """Die Ereignisse eines gespeicherten Laufs aus seinem Protokoll."""
    folder = _part_folder(request.path_params["name"])
    run_id = request.path_params["run_id"]
    path = folder / trace.RUNS / f"{run_id}.jsonl" if folder is not None and _safe_name(run_id) else None
    if path is None or not path.is_file():
        return _error(404, "No log for this run")
    return JSONResponse({"id": run_id, "events": [line for line in _trace_lines(path) if line.get("type") != "meta"]})


def create_app(agent: Agent | None = None) -> Starlette:
    """Die App; `agent` ersetzt d2c.agent.runner.run_agent (Tests setzen einen Ersatz ein)."""
    app = Starlette(
        routes=[
            Route("/", index),
            Route("/api/models", models),
            Route("/api/parts", parts),
            Route("/api/parts/{name}", part),
            Route("/api/parts/{name}/drawing.png", drawing_png),
            Route("/api/parts/{name}/runs/{run_id}", run_trace),
            Route("/api/runs", start_run, methods=["POST"]),
            Route("/api/runs", runs),
            Route("/api/runs/{run_id}/events", run_events),
            Route("/files/{name}/{file}", file),
            Mount("/static", StaticFiles(directory=STATIC)),
        ],
        # Host-Prüfung auch für GET: ohne sie liest eine fremde Seite per DNS-Rebinding die Ergebnisse und Zeichnungen.
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts(), www_redirect=False)],
    )
    app.state.agent = agent or _default_agent
    app.state.runs = {}
    app.state.max_runs = max_runs()
    app.state.slots = asyncio.Semaphore(app.state.max_runs)
    return app

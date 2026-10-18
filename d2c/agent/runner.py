"""Der Agentenlauf: steuert den MCP-Server mit einem Modell. Die Claude Agent SDK lässt Claude Code als Agentenschleife laufen.

Eine Sitzung sieht nur die MCP-Werkzeuge ihrer Phase und nichts sonst: keine eingebauten Werkzeuge, keine Einstellungen,
Einstellungs-Hooks, Plugins oder CLAUDE.md, keinen anderen MCP-Server. Einzig der PreToolUse-Hook des Laufs sperrt nach dem
Abschluss jedes Werkzeug (`_Run.guard`). Die CLI übergibt dem Modell die Serveranweisungen
(instructions.md) selbst, daher ergänzt der Systemprompt nur Rolle und Regeln. Das Backend (Modell, Endpunkt, Schlüssel)
steht in `backend.py`, die Kommandozeile in `__main__.py`.

Imports auf Modulebene nur Standardbibliothek und SDK, damit `python -m d2c.agent` auf einem Rechner ohne CadQuery läuft
(dann läuft der Server woanders, z. B. in einem Container).
"""

import asyncio
import json
from collections import Counter, deque
from collections.abc import Callable
from contextlib import aclosing

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    HookMatcher,
    ProcessError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from d2c.agent.backend import SESSION_ENV, Backend, backend_env, server_config
from d2c.config import max_minutes as minutes_limit
from d2c.config import max_usd as usd_limit
from d2c.config import results_root
from d2c.spec import PART_NAME

SERVER = "d2c"  # der Schlüssel, unter dem der Server registriert ist: seine Werkzeuge heißen mcp__d2c__<werkzeug>
PREFIX = f"mcp__{SERVER}__"
READ_TOOLS = ("render_drawing", "read_drawing_text", "measure_drawing", "check_spec", "submit_spec")
# Die kleinen Werkzeugsätze für lokale Modelle: je Sitzung etwa 60 % weniger Schemata im Kontext. Es fehlen edit_op, check_spec,
# run_script und move_op; Korrekturen laufen über delete_op und einen neuen Aufruf.
LITE_READ = ("render_drawing", "read_drawing_text", "measure_drawing", "submit_spec")
GATE_TOOLS = (
    "render_drawing",
    "measure_drawing",
)  # die Bausitzung mit vorgegebener Spezifikation braucht sie fürs Messgate von submit_spec
LITE_BUILD = ("extrude", "revolve", "add_holes", "edge_finish", "delete_op", "get_state", "export_step")
BUILD_TOOLS = (
    "extrude",
    "revolve",
    "add_holes",
    "edge_finish",
    "run_script",
    "edit_op",
    "delete_op",
    "move_op",
    "get_state",
    "export_step",
)
TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
MAX_TEXT = 500  # Zeichen eines Werkzeugergebnisses in seinem Ereignis
MAX_FAILS = 3  # so viele identische fehlgeschlagene Aufrufe mit gleicher Antwort beenden den Lauf
MAX_DENIED = 3  # so viele verweigerte Aufrufe nach dem Abschluss beenden die Sitzung doch (dann fehlen die Kosten)
MAX_SAME_CALL = 6  # so viele fehlgeschlagene Aufrufe mit gleichen Argumenten, auch wenn die Antwort wechselt
MAX_TURNS = 80  # Standard der Züge je Sitzung
STDERR_LINES = 20  # so viele letzte Zeilen der CLI-Fehlerausgabe behält ein Lauf
SIGTERM_EXIT = 143
MAX_BUFFER = 32 * 1024 * 1024  # Bytes je JSON-Nachricht der CLI: ein Bild von render_drawing mit 2000 px kann mehrere MB haben

RULES = (
    "You turn one technical drawing into a validated CAD model of a part. Act only through the tools of the d2c server: "
    "you have no shell, no files and no web.\n"
    "- Call the tools, do not describe what you would do. After every report, apply the rules of the server instructions.\n"
    f"- Tool names are {PREFIX}<name>: call mcp__d2c__extrude, not extrude. Always use the full name, exactly as listed.\n"
    "- Part name: {part}. Drawing: {pdf} (the path as the tools take it).\n"
)
# Zusatz für kleine lokale Modelle: die vollen Werkzeugnamen und eine Kurzprozedur je Phase.
LITE = {
    "read": "- Your tools: mcp__d2c__render_drawing, mcp__d2c__read_drawing_text, mcp__d2c__measure_drawing, mcp__d2c__submit_spec. "
    "There is no check_spec, edit_op or run_script: skip every step of the server instructions that needs them.\n"
    "- Procedure: 1. mcp__d2c__render_drawing (100 dpi) and mcp__d2c__read_drawing_text. 2. mcp__d2c__measure_drawing with "
    "printed_dims and the scale of the title block. 3. Write the spec: every printed dimension with its text, expected "
    'bbox and holes. 4. mcp__d2c__submit_spec. 5. Follow the "next" lines of every answer.\n',
    "revise": "- Your tools: mcp__d2c__render_drawing, mcp__d2c__read_drawing_text, mcp__d2c__measure_drawing, mcp__d2c__submit_spec, "
    "mcp__d2c__extrude, mcp__d2c__revolve, mcp__d2c__add_holes, mcp__d2c__edge_finish, mcp__d2c__delete_op, mcp__d2c__get_state, "
    "mcp__d2c__export_step. There is no edit_op, run_script or move_op: change an op with mcp__d2c__delete_op and a new call.\n",
    "build": "- Your tools: mcp__d2c__extrude, mcp__d2c__revolve, mcp__d2c__add_holes, mcp__d2c__edge_finish, mcp__d2c__delete_op, "
    "mcp__d2c__get_state, mcp__d2c__export_step. There is no edit_op, run_script or move_op: fix a wrong op with "
    "mcp__d2c__delete_op and a new call.\n"
    "- Procedure: 1. Base body with mcp__d2c__extrude or mcp__d2c__revolve. 2. Holes with mcp__d2c__add_holes (omit depth "
    'for a through hole). 3. Edges with mcp__d2c__edge_finish. 4. Read each report and follow its "next" lines. '
    "5. When all checks pass: mcp__d2c__export_step.\n",
}
ENDS = {
    "full": "- You are done only after export_step returns ok: true (and export_step with variant mid, if the server instructions ask "
    "for it). Then answer with one short sentence and call no further tool.",
    "read": '- This session only reads the drawing: call submit_spec with name "{part}". Once it returns ok: true, answer with one '
    "short sentence and call no further tool. Another session builds the part.",
    "revise": "- The part already exists from an earlier drawing revision: never rebuild it from scratch. You are done only after "
    "export_step returns ok: true and export_step with variant mid has run. Then answer with one short sentence and call no further tool.",
    "build": "- The spec is in the prompt: skip the reading steps of the server instructions. If submit_spec asks for a measurement, "
    "call measure_drawing as it says and submit the spec again with every value unchanged, assumed stays true. "
    "You are done only after export_step returns ok: true. Then answer with one short sentence and call no further tool.",
}


def _read(block: ToolResultBlock) -> tuple[bool, str, dict]:
    """(ok, Ereignistext, JSON-Objekt des Ergebnisses): ein Bild erscheint als "image", ok ist falsch bei einem Fehler oder "ok": false."""
    parts = block.content if isinstance(block.content, list) else [{"type": "text", "text": block.content or ""}]
    text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    data = data if isinstance(data, dict) else {}
    shown = "image" if any(p.get("type") == "image" for p in parts) else text
    return not block.is_error and data.get("ok") is not False, shown[:MAX_TEXT], data


def _why(result: ResultMessage) -> str:
    """Warum eine Fehlermeldung die Sitzung beendete."""
    if result.subtype == "error_max_turns":
        return "max_turns reached"
    return result.result or "; ".join(result.errors or []) or result.subtype


class _Run:
    """Ein Lauf über eine oder zwei SDK-Sitzungen: die Ereignisse, die gesehenen Fehlschläge, die Summen und was die Sitzungen erreichten."""

    def __init__(
        self, part: str, pdf: str, mcp: dict, max_turns: int, on_event: Callable[[dict], None] | None, max_usd: float | None = None
    ) -> None:
        self.part, self.pdf, self.mcp, self.max_turns, self.max_usd = part, pdf, mcp, max_turns, max_usd
        self.emit = on_event or (lambda event: None)
        self.limit: str | None = None  # "budget" oder "time", wenn ein Limit den Lauf beendete
        self.building = False
        self.fails: Counter = Counter()
        self.turns: set = set()  # Kennungen der gesehenen Modellantworten
        self.cost, self.usage = 0.0, dict.fromkeys(TOKENS, 0)
        self.priced = True  # falsch, sobald eine Sitzung ohne ResultMessage endete: ihre Kosten sind unbekannt
        self.priced_now, self.denied = False, 0  # kam die ResultMessage der laufenden Sitzung; verweigerte Aufrufe nach dem Abschluss
        self.spec: dict | None = None  # die Spezifikation des letzten erfolgreichen submit_spec
        self.files: dict | None = None  # die Dateien des erfolgreichen Nennexports
        self.changes: list[dict] | None = None  # die Änderungsliste des letzten submit_spec auf ein vorhandenes Teil
        self.mid_files: dict = {}  # step_mid und report_mid des Exports mit variant="mid"
        self.tail: deque[str] = deque(maxlen=STDERR_LINES)  # die letzten Zeilen der CLI-Fehlerausgabe der laufenden Sitzung
        self.calls: dict[str, tuple[str, dict]] = {}  # offene Werkzeugaufrufe der laufenden Sitzung: Kennung -> (Name, Argumente)
        self.finish, self.done, self.reason = "", False, None  # Abschlusswerkzeug, ob es gelang, Grund eines Scheiterns

    def budget_left(self) -> float | None:
        """Was vom Kostenlimit für die nächste Sitzung bleibt (die CLI zählt je Sitzung); None ohne Limit."""
        return None if self.max_usd is None else max(self.max_usd - self.cost, 0.01)

    def phase(self, name: str) -> None:
        self.building = name == "build"
        self.emit({"type": "phase", "phase": name})

    def options(self, kind: str, backend: Backend, tools: tuple[str, ...]) -> ClaudeAgentOptions:
        """Die SDK-Optionen einer Sitzung der Art `kind` (full, read, build) mit genau den Werkzeugen `tools`."""
        prompt = RULES + (LITE[kind] if backend.use_lite() else "") + ENDS[kind]
        return ClaudeAgentOptions(
            model=backend.model,
            system_prompt=prompt.format(part=self.part, pdf=self.pdf),
            max_turns=self.max_turns,
            max_budget_usd=self.budget_left(),
            tools=[],
            allowed_tools=[PREFIX + t for t in tools],
            disallowed_tools=[PREFIX + t for t in (*READ_TOOLS, *BUILD_TOOLS) if t not in tools],  # dem Modell verborgen
            mcp_servers={SERVER: self.mcp},
            strict_mcp_config=True,
            setting_sources=[],
            permission_mode="dontAsk",
            env=SESSION_ENV | backend_env(backend),
            stderr=self.tail.append,
            hooks={"PreToolUse": [HookMatcher(hooks=[self.guard])]},
            max_buffer_size=MAX_BUFFER,
            extra_args={"no-session-persistence": None},  # kein Verlauf der Zeichnungen im Claude-Konfigurationsordner
        )

    def _open(self, name: str, args: dict) -> bool:
        """Ob ein Werkzeug jetzt laufen darf: nach dem Abschluss nur noch die Toleranzmitte, nichts verändert das Teil."""
        return not self.done or (name == "export_step" and args.get("variant") == "mid")

    async def guard(self, input_data: dict, tool_use_id: str | None, context: object) -> dict:
        """PreToolUse-Hook: verweigert nach dem Abschluss jeden anderen Aufruf. Die Sitzung läuft weiter bis zur ResultMessage,
        damit Kosten und Usage stimmen; die CLI führt einen verweigerten Aufruf nie aus (anders als bei einem Abbruch des Prozesses)."""
        if self._open(input_data.get("tool_name", "").removeprefix(PREFIX), input_data.get("tool_input") or {}):
            return {}
        reason = "The part is already exported. Call no further tool and end with one short sentence."
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}

    async def session(self, prompt: str, options: ClaudeAgentOptions, finish: str) -> str | None:
        """Spiele eine Sitzung, bis sie endet oder gestoppt werden muss; None, wenn sie ihre Aufgabe erledigte (`finish`
        gelang) oder von selbst endete, sonst warum sie scheiterte. Ein Stopp schließt den Generator, was die CLI beendet."""
        self.calls, self.finish, self.done, self.reason = {}, finish, False, None
        self.priced_now, self.denied = False, 0
        self.tail.clear()
        if self.max_usd is not None and self.cost >= self.max_usd:  # die erste Sitzung hat schon alles verbraucht
            self.limit = "budget"
            return f"cost limit of {self.max_usd:g} USD reached"
        try:
            async with aclosing(query(prompt=prompt, options=options)) as stream:
                async for msg in stream:
                    if self._handle(msg):
                        break
        except ProcessError as e:  # die CLI endete mit Fehler; ihre letzten Zeilen sagen oft warum
            self.emit({"type": "stderr", "lines": list(self.tail)})
            self.reason = self.reason or (
                f"SIGTERM from outside (parent process terminated?), exit {SIGTERM_EXIT}"
                if e.exit_code == SIGTERM_EXIT
                else f"{type(e).__name__}: {e}"
            )
        except ClaudeSDKError as e:  # auf das Fehlerergebnis folgt die Ausnahme; den Grund nannte das Ergebnis schon
            self.reason = self.reason or f"{type(e).__name__}: {e}"
        self.priced = self.priced and self.priced_now
        return None if self.done else self.reason

    def _handle(self, msg: object) -> bool:
        """Verarbeite eine Nachricht der Sitzung; True, wenn die Sitzung gestoppt werden muss."""
        if isinstance(msg, ResultMessage):
            self._on_result(msg)
        elif isinstance(msg, SystemMessage) and msg.subtype == "init":
            return self._on_init(msg)
        elif isinstance(msg, AssistantMessage):
            return self._on_assistant(msg)
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            return any(
                self._on_tool_result(block)
                for block in msg.content
                if isinstance(block, ToolResultBlock) and block.tool_use_id in self.calls
            )
        return False

    def _on_result(self, msg: ResultMessage) -> None:
        self.priced_now = True
        self.cost += msg.total_cost_usd or 0
        for key in TOKENS:
            self.usage[key] += (msg.usage or {}).get(key) or 0
        if msg.is_error:
            self.reason = _why(msg)
        if msg.subtype == "error_max_budget_usd":  # die CLI hat die Sitzung wegen max_budget_usd beendet
            self.limit, self.reason = "budget", f"cost limit of {self.max_usd:g} USD reached"

    def _on_init(self, msg: SystemMessage) -> bool:
        status = {x.get("name"): x.get("status") for x in msg.data.get("mcp_servers", [])}.get(SERVER)
        if status in (None, "connected", "pending"):  # pending: verbindet sich noch
            return False
        self.reason = f"The MCP server {SERVER} is {status}: check the server settings"
        return True

    def _on_assistant(self, msg: AssistantMessage) -> bool:
        self.turns.add(msg.message_id or id(msg))  # eine Antwort kann als mehrere Nachrichten eintreffen
        for block in msg.content:
            if isinstance(block, TextBlock) and block.text.strip():
                self.emit({"type": "message", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                name = block.name.removeprefix(PREFIX)
                if not self._open(name, block.input):  # der Hook verweigert den Aufruf; ein Modell, das nicht aufhört, wird beendet
                    self.denied += 1
                    if self.denied >= MAX_DENIED:
                        return True
                    continue
                self.calls[block.id] = (name, block.input)
                if name in BUILD_TOOLS and not self.building:
                    self.phase("build")
                self.emit({"type": "tool_call", "name": name, "args": block.input})
        return False

    def _on_tool_result(self, block: ToolResultBlock) -> bool:
        """Melde das Ergebnis eines Werkzeugaufrufs; True, wenn derselbe Aufruf zu oft fehlschlug."""
        name, args = self.calls[block.tool_use_id]
        ok, text, data = _read(block)
        self.emit({"type": "tool_result", "name": name, "ok": ok, "text": text})
        # Ein Aufbau mit ok=false ohne error hat nur die Prüfungen noch nicht erfüllt, mitten im Aufbau normal:
        # kein Fehlschlag. Gezählt werden Werkzeugfehler und Ergebnisse mit error.
        if block.is_error or data.get("error"):
            call = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
            self.fails[call] += 1
            self.fails[(*call, text)] += 1  # gleiche Antwort: kein Fortschritt
            if self.fails[(*call, text)] >= MAX_FAILS or self.fails[call] >= MAX_SAME_CALL:
                self.reason = f"repeatedly failed call {name}"
                return True
        elif data.get("ok") is True:
            if name == "submit_spec" and isinstance(args.get("spec"), dict):
                self.spec = args["spec"]
                self.changes = data.get("changes", self.changes)
            elif name == "export_step":
                files = data.get("files", {})
                if args.get("variant") == "mid":
                    self.mid_files = {"step_mid": files.get("step"), "report_mid": files.get("report")}
                else:
                    self.files = files
            # der Abschluss ist der Nennexport; eine Toleranzmitte davor oder danach beendet nichts
            self.done = self.done or (name == self.finish and args.get("variant", "nominal") == "nominal")
        return False

    def result(self, reason: str | None) -> dict:
        total = {"turns": len(self.turns), "usage": self.usage, "cost_usd": round(self.cost, 6) if self.priced else None}
        if self.files is not None:
            changes = {} if self.changes is None else {"changes": self.changes}
            return {"ok": True, "part": self.part, "files": self.files | self.mid_files} | changes | total
        failed = {"ok": False, "part": self.part, "reason": reason or "the session ended without a successful export_step"}
        return failed | ({"limit": self.limit} if self.limit else {}) | total


def _json(spec: dict) -> str:
    return json.dumps(spec, ensure_ascii=False, separators=(",", ":"))


def _preload(part: str, spec: dict) -> bool:
    """Schreibe die Spezifikation und eine leere Vorgangsliste, wo der Server sie lädt (results/<teil>/); False, wenn das nicht geht.

    Das Modell muss die Spezifikation dann nicht wörtlich an submit_spec zurückschicken."""
    if not PART_NAME.fullmatch(part):
        return False
    folder = results_root() / part
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "spec.json").write_text(json.dumps(spec | {"name": part}, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "ops.json").write_text("[]", encoding="utf-8")
    except OSError:
        return False
    return True


async def _from_spec(run: _Run, spec: dict, build: Backend, tools: tuple[str, ...]) -> str | None:
    """Eine vorgegebene Spezifikation überspringt das Lesen: nur die Bausitzung läuft."""
    run.phase("build")
    named = _json(spec | {"name": run.part})
    if _preload(run.part, spec):
        prompt = (
            f'Build the part "{run.part}" and export it. Its spec is already loaded on the server. Only if a tool reports '
            f"the part as unknown, call submit_spec once with exactly this spec:\n{named}"
        )
    else:
        prompt = f'Build the part "{run.part}" and export it. First call submit_spec with this spec, unchanged:\n{named}'
    return await run.session(prompt, run.options("build", build, ("submit_spec", *GATE_TOOLS, *tools)), "export_step")


REVISE_PROMPT = (
    'The part "{part}" already exists from an earlier revision of the drawing {pdf}; update it to the new revision, do not '
    "build it again. 1. Call get_state to read the existing spec and ops. 2. Read the new drawing as the server instructions say "
    "and capture every dimension, tolerance, fit and form or position tolerance; keep the id of every dimension that is still on "
    "the drawing and give new dimensions new ids. 3. Call submit_spec with the new spec and "
    "keep_ops=true: the answer lists the changes against the earlier revision. 4. Change only what differs, with edit_op, "
    "add_holes or delete_op, and follow the next lines. 5. Call export_step, then export_step with variant mid."
)


def _stored(part: str) -> bool:
    """Ob results/<teil>/ eine Spezifikation und eine Vorgangsliste enthält, also ein Teil, das sich ändern lässt."""
    folder = results_root() / part
    return PART_NAME.fullmatch(part) is not None and (folder / "spec.json").is_file() and (folder / "ops.json").is_file()


async def _revision(run: _Run, backend: Backend, build_tools: tuple[str, ...]) -> str | None:
    """Ein vorhandenes Teil auf eine neue Zeichnungsrevision umstellen: eine Sitzung liest, ändert und exportiert."""
    run.phase("read")
    tools = (*(LITE_READ if backend.use_lite() else READ_TOOLS), *build_tools)
    prompt = REVISE_PROMPT.format(part=run.part, pdf=run.pdf)
    return await run.session(prompt, run.options("revise", backend, tools), "export_step")


async def _single_session(run: _Run, backend: Backend, build_tools: tuple[str, ...]) -> str | None:
    """Ein Modell liest und baut in einer Sitzung."""
    run.phase("read")
    return await run.session(
        f"Read the drawing {run.pdf} and build the part {run.part}.",
        run.options("full", backend, (*READ_TOOLS, *build_tools)),
        "export_step",
    )


async def _two_sessions(run: _Run, backend: Backend, build: Backend, build_tools: tuple[str, ...]) -> str | None:
    """Eine Lesesitzung übermittelt die Spezifikation, eine zweite baut das Teil daraus."""
    run.phase("read")
    reason = await run.session(
        f"Read the drawing {run.pdf} and submit the spec of the part {run.part}.",
        run.options("read", backend, LITE_READ if backend.use_lite() else READ_TOOLS),
        "submit_spec",
    )
    if run.spec is None:
        return reason or "the session ended without a successful submit_spec"
    run.part = run.spec.get("name", run.part)  # das Teil ist unter dem Namen seiner Spezifikation registriert
    run.phase("build")
    prompt = f'The spec of the part "{run.part}" is submitted. Build the part and export it.\n{_json(run.spec)}'
    return await run.session(prompt, run.options("build", build, build_tools), "export_step")


async def run_agent(
    pdf: str,
    part: str,
    *,
    backend: Backend,
    build: Backend | None = None,
    server: str = "stdio",
    spec: dict | None = None,
    revise: bool = False,
    allow_script: bool = True,
    max_turns: int = MAX_TURNS,
    max_usd: float | None = None,
    max_minutes: float | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> dict:
    """Lies die Zeichnung `pdf` (relativ zum Datenordner des Servers) und baue, prüfe und exportiere das Teil `part`.

    Ein Backend erledigt beide Phasen; mit einem anderen `build` liest eine erste Sitzung mit `backend` die Zeichnung
    und übermittelt die Spezifikation, eine zweite mit `build` baut das Teil daraus (und sieht nie ein Bild): so liest
    Sonnet über die Anmeldung und ein lokales Modell baut. Eine vorgegebene `spec` überspringt das Lesen; sie wird in den
    Ergebnisordner geschrieben, wo der Server sie lädt. `Backend.lite` wählt die kleinen Werkzeugsätze (LITE_READ,
    LITE_BUILD) und immer zwei Sitzungen; ohne Angabe gilt es je Sitzung für ein nicht-Claude-Modell an einem eigenen
    Endpunkt. Ein Backend mit base_url ohne Modell löst ValueError aus. Die Bausitzung einer vorgegebenen `spec` bekommt
    auch render_drawing und measure_drawing, damit sich das Messgate von submit_spec erfüllen lässt.
    Mit `revise` stellt eine einzige Sitzung das vorhandene Teil auf eine neue Zeichnungsrevision um (get_state,
    submit_spec mit keep_ops, nur die Unterschiede ändern, exportieren); results/<part>/spec.json und ops.json müssen
    existieren, sonst endet der Lauf mit ok false. Das done-Ereignis enthält dann `changes`, die Änderungsliste von submit_spec.
    `max_usd` (Standard 3, D2C_MAX_USD) und `max_minutes` (Standard 20, D2C_MAX_MINUTES) begrenzen die Kosten und die
    Gesamtdauer über alle Sitzungen, 0 hebt die Grenze auf; wird eine erreicht, endet der Lauf mit ok false, einem
    `reason` und `limit` "budget" oder "time".
    `on_event` erhält {"type": "phase"|"tool_call"|"tool_result"|"message"|"stderr"|"done", ...}, zuletzt das done-Ereignis
    mit dem Ergebnis: {"ok": True, "part", "turns", "files", "usage", "cost_usd"} nach erfolgreichem Nennexport (`files`
    enthält bei einem Export mit variant="mid" zusätzlich step_mid und report_mid), sonst {"ok": False, "part", "reason", ...}. "turns" zählt die Modellantworten; "usage" und "cost_usd" sind die Summen der CLI
    über die Sitzungen; endete eine ohne ResultMessage (abgebrochen oder abgestürzt), sind die Kosten unbekannt: `cost_usd` ist dann None.
    """
    build = build or backend
    backend.check()
    build.check()
    build_tools = LITE_BUILD if build.use_lite() else tuple(t for t in BUILD_TOOLS if allow_script or t != "run_script")
    max_usd, max_minutes = usd_limit(max_usd), minutes_limit(max_minutes)
    run = _Run(part, pdf, server_config(server), max_turns, on_event, max_usd)
    try:
        async with asyncio.timeout(max_minutes * 60 if max_minutes else None):
            if revise:
                reason = (
                    await _revision(run, backend, build_tools)
                    if _stored(part)
                    else f"Part {part!r} has no stored spec and ops in the results folder; run it once without revise first"
                )
            elif spec is not None:
                reason = await _from_spec(run, spec, build, build_tools)
            elif build == backend and not backend.use_lite():
                reason = await _single_session(run, backend, build_tools)
            else:
                reason = await _two_sessions(run, backend, build, build_tools)
    except TimeoutError:  # die laufende Sitzung wurde abgebrochen, ihr Generator geschlossen und damit die CLI beendet
        run.limit, run.priced, reason = "time", False, f"time limit of {max_minutes:g} min reached"
    result = run.result(reason)
    run.emit({"type": "done"} | result)
    return result

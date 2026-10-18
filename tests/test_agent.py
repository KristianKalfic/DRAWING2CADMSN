"""Der Agent mit geskripteten SDK-Sitzungen anstelle der CLI: Ereignisse, Phasen, Werkzeuglisten, Stopps, Backends, Kommandozeile."""

import asyncio
import dataclasses
import inspect
import json
import sys
import time
from itertools import count

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ProcessError,
    ResultError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from d2c.agent import __main__ as cli
from d2c.agent import backend as be
from d2c.agent import runner as agent
from d2c.agent.backend import Backend

P = "mcp__d2c__"
FILES = {"step": "/app/results/plate/plate.step", "review": "/app/results/plate/review.md"}
SPEC = {"name": "plate", "drawing": "A/plate.pdf", "title": "Plate", "scale": "1:1"}  # der Agent schaut nicht hinein
ids = count(1)


@pytest.fixture(autouse=True)
def isolated_results(monkeypatch, tmp_path):
    """Eine vorgegebene Spezifikation wird in den Ergebnisordner geschrieben: nie in den echten."""
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))
    for name in ("D2C_LLM_CONTEXT", "D2C_LLM_THINKING"):
        monkeypatch.delenv(name, raising=False)


class Scripted:
    """Springt für query() ein: spielt je Aufruf die nächste geskriptete Sitzung und behält die Optionen, mit denen er gerufen wurde."""

    def __init__(self, *sessions):
        self.sessions, self.calls, self.cut = list(sessions), [], 0

    async def __call__(self, *, prompt, options):
        self.calls.append((prompt, options))
        try:
            for item in self.sessions.pop(0):
                if isinstance(item, Exception):
                    raise item
                if callable(item):  # ein Ereignis der CLI neben dem Nachrichtenstrom, z. B. eine Zeile auf stderr
                    if inspect.isawaitable(late := item(options)):  # oder ein Hook-Aufruf der CLI
                        await late
                    continue
                yield item
        except GeneratorExit:  # der Verbraucher schloss die Sitzung, bevor das Skript endete
            self.cut += 1
            raise


def text(obj) -> list:
    """Ein MCP-Werkzeugergebnis, wie die CLI es meldet: eine Liste von Inhaltsblöcken."""
    return [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, indent=2)}]


def step(name, args, result, *, error=False) -> list:
    """Ein Werkzeugaufruf des Assistenten und das Ergebnis, das ihm folgt."""
    tid = f"toolu_{next(ids)}"
    return [
        AssistantMessage(content=[ToolUseBlock(tid, P + name, args)], model="m"),
        UserMessage(content=[ToolResultBlock(tid, result, is_error=True if error else None)]),
    ]


def say(words) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(words)], model="m")


def ended(**more) -> ResultMessage:
    fields = dict(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=3,
        session_id="s",
        total_cost_usd=0.5,
        usage={
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_input_tokens": 7,
            "cache_creation_input_tokens": 3,
            "service_tier": "standard",
        },
    )
    return ResultMessage(**(fields | more))


SUBMIT = step("submit_spec", {"spec": SPEC}, text({"ok": True, "part": "plate"}))
EXPORT = step("export_step", {"part": "plate"}, text({"ok": True, "files": FILES, "report": {}}))


def run(monkeypatch, *sessions, **kwargs):
    """run_agent auf geskripteten Sitzungen: (Ergebnis, Ereignisse, die Scripted-Abfrage)."""
    fake, events = Scripted(*sessions), []
    monkeypatch.setattr(agent, "query", fake)
    result = asyncio.run(agent.run_agent("A/plate.pdf", "plate", on_event=events.append, **{"backend": Backend()} | kwargs))
    return result, events, fake


def kinds(events) -> list:
    return [(e["type"], e.get("phase") or e.get("name")) for e in events]


def test_scripted_run_events_and_result(monkeypatch):
    session = [
        AssistantMessage(
            content=[TextBlock("Looking at the drawing."), ToolUseBlock("t0", P + "render_drawing", {"pdf": "A/plate.pdf"})], model="m"
        ),
        UserMessage(content=[ToolResultBlock("t0", text({"pages": 1, "dpi": 100}))]),
        *step("read_drawing_text", {"pdf": "A/plate.pdf"}, text("[]")),
        *SUBMIT,
        *step("extrude", {"part": "plate", "distance": 10}, text({"ok": False, "error": None})),
        *step("extrude", {"part": "plate", "distance": 11}, text({"ok": True, "error": None})),
        *EXPORT,
        say("Exported."),
        ended(),
    ]
    result, events, fake = run(monkeypatch, session)
    assert kinds(events) == [
        ("phase", "read"),
        ("message", None),
        ("tool_call", "render_drawing"),
        ("tool_result", "render_drawing"),
        ("tool_call", "read_drawing_text"),
        ("tool_result", "read_drawing_text"),
        ("tool_call", "submit_spec"),
        ("tool_result", "submit_spec"),
        ("phase", "build"),
        ("tool_call", "extrude"),
        ("tool_result", "extrude"),
        ("tool_call", "extrude"),
        ("tool_result", "extrude"),
        ("tool_call", "export_step"),
        ("tool_result", "export_step"),
        ("message", None),
        ("done", None),
    ]
    assert events[1] == {"type": "message", "text": "Looking at the drawing."}
    assert events[2] == {"type": "tool_call", "name": "render_drawing", "args": {"pdf": "A/plate.pdf"}}
    assert [e["ok"] for e in events if e["type"] == "tool_result"] == [True, True, True, False, True, True]
    assert result == {
        "ok": True,
        "part": "plate",
        "turns": 7,
        "files": FILES,
        "cost_usd": 0.5,
        "usage": {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 3},
    }
    assert events[-1] == {"type": "done"} | result
    assert fake.cut == 0 and len(fake.calls) == 1


def test_image_result_reported_as_image(monkeypatch):
    shown = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        {"type": "text", "text": '{"pages": 1}'},
    ]
    _, events, _ = run(monkeypatch, [*step("render_drawing", {"pdf": "A/plate.pdf"}, shown), ended()])
    assert [e for e in events if e["type"] == "tool_result"] == [
        {"type": "tool_result", "name": "render_drawing", "ok": True, "text": "image"}
    ]


def test_tool_result_ok_false_from_json(monkeypatch):
    session = [
        *step("get_state", {"part": "plate"}, text("x" * 600)),
        *step("extrude", {"part": "plate"}, text({"ok": False, "error": "no"})),
        *step("delete_op", {"part": "plate", "index": 9}, "Error executing tool delete_op: out of range", error=True),
        *step("move_op", {"part": "plate", "index": 0, "new_index": 1}, text({"ok": True})),
        ended(),
    ]
    _, events, _ = run(monkeypatch, session)
    results = [e for e in events if e["type"] == "tool_result"]
    assert [e["ok"] for e in results] == [True, False, False, True]
    assert results[0]["text"] == "x" * 500
    assert results[2]["text"] == "Error executing tool delete_op: out of range"


def test_two_phase_switches_model_and_tools(monkeypatch):
    read = [*step("render_drawing", {"pdf": "A/plate.pdf"}, text("{}")), *SUBMIT, say("Spec submitted."), ended()]
    build = [*step("extrude", {"part": "plate", "distance": 10}, text({"ok": True})), *EXPORT, say("Done."), ended()]
    result, events, fake = run(monkeypatch, read, build, backend=Backend("reader"), build=Backend("builder"))
    (_, first), (prompt, second) = fake.calls
    assert (first.model, second.model) == ("reader", "builder")
    assert first.allowed_tools == [P + t for t in agent.READ_TOOLS]
    assert second.allowed_tools == [P + t for t in agent.BUILD_TOOLS]
    assert P + "render_drawing" in second.disallowed_tools and P + "extrude" in first.disallowed_tools
    assert "plate" in prompt and json.dumps(SPEC, ensure_ascii=False, separators=(",", ":")) in prompt
    assert [e["phase"] for e in events if e["type"] == "phase"] == ["read", "build"]
    names = [e.get("name") for e in events]
    assert names.index("submit_spec") < kinds(events).index(("phase", "build")) < names.index("extrude")
    assert result["ok"] and result["turns"] == 6 and result["cost_usd"] == 1.0 and result["usage"]["input_tokens"] == 200
    assert fake.cut == 0


def test_two_phase_denies_a_reader_that_keeps_calling_tools(monkeypatch):
    read = [*SUBMIT, *step("render_drawing", {"pdf": "A/plate.pdf"}, text("{}")), ended()]
    build = [*EXPORT, say("Done."), ended()]
    result, events, fake = run(monkeypatch, read, build, backend=Backend("reader"), build=Backend("builder"))
    assert result["ok"] and fake.cut == 0 and len(fake.calls) == 2 and result["cost_usd"] == 1.0
    assert "render_drawing" not in [e.get("name") for e in events]


def test_build_session_uses_the_name_the_reader_submitted(monkeypatch):
    read = [*step("submit_spec", {"spec": SPEC | {"name": "other"}}, text({"ok": True})), say("Submitted."), ended()]
    build = [*step("export_step", {"part": "other"}, text({"ok": True, "files": FILES})), say("Done."), ended()]
    result, _, fake = run(monkeypatch, read, build, backend=Backend("reader"), build=Backend("builder"))
    assert 'part "other"' in fake.calls[1][0] and (result["ok"], result["part"]) == (True, "other")


def test_server_that_failed_to_connect_is_the_reason(monkeypatch):
    def init(status):
        return SystemMessage(subtype="init", data={"mcp_servers": [{"name": "d2c", "status": status, "source": "dynamic"}]})

    result, events, fake = run(monkeypatch, [init("failed"), say("TOOL-MISSING"), ended()])
    assert result["ok"] is False and "d2c" in result["reason"] and "failed" in result["reason"] and fake.cut == 1
    assert [e["type"] for e in events] == ["phase", "done"]
    result, _, fake = run(
        monkeypatch, [init("pending"), init("connected"), *EXPORT, say("Done."), ended()]
    )  # verbindet sich noch: kein Fehler
    assert result["ok"] and fake.cut == 0


def test_reader_without_spec_stops_the_run(monkeypatch):
    result, events, fake = run(monkeypatch, [say("I cannot read this."), ended()], backend=Backend("reader"), build=Backend("builder"))
    assert result["ok"] is False and "submit_spec" in result["reason"] and len(fake.calls) == 1
    assert events[-1] == {"type": "done"} | result


def test_spec_skips_reading_phase(monkeypatch):
    session = [*SUBMIT, *step("extrude", {"part": "plate", "distance": 10}, text({"ok": True})), *EXPORT, say("Done."), ended()]
    result, events, fake = run(monkeypatch, session, spec=SPEC | {"name": "other"}, backend=Backend("m1"))
    ((prompt, options),) = fake.calls
    assert options.model == "m1"
    # das Messgate von submit_spec braucht measure_drawing; render_drawing gehört dazu, read_drawing_text nicht
    assert set(options.allowed_tools) == {P + t for t in (*agent.BUILD_TOOLS, "submit_spec", "measure_drawing", "render_drawing")}
    assert P + "read_drawing_text" in options.disallowed_tools and P + "check_spec" in options.disallowed_tools
    assert json.dumps(SPEC | {"name": "plate"}, ensure_ascii=False, separators=(",", ":")) in prompt
    assert kinds(events)[0] == ("phase", "build") and ("phase", "read") not in kinds(events)
    assert result["ok"]


def test_spec_build_session_can_satisfy_the_measure_gate(monkeypatch):
    """Die Bausitzung misst, wenn submit_spec es verlangt, und reicht die Spezifikation dann unverändert erneut ein."""
    gate = "The dimensions ['d7'] are assumed, but measure_drawing did not run on page 1"
    session = [
        *step("submit_spec", {"spec": SPEC}, gate, error=True),
        *step("measure_drawing", {"pdf": "A/plate.pdf", "region": [0, 0, 1, 1]}, text({"extent_mm": [80, 50]})),
        *SUBMIT,
        *EXPORT,
        say("Done."),
        ended(),
    ]
    result, events, fake = run(monkeypatch, session, spec=SPEC, backend=Backend("m1"))
    assert result["ok"] and "measure_drawing" in [e.get("name") for e in events]
    assert fake.cut == 0 and "assumed stays true" in fake.calls[0][1].system_prompt


def test_system_prompt_names_the_prefixed_tools(monkeypatch):
    _, _, fake = run(monkeypatch, [ended()])
    assert "mcp__d2c__<name>" in fake.calls[0][1].system_prompt
    assert agent.PREFIX == "mcp__d2c__"


def test_no_script_hides_run_script(monkeypatch):
    _, _, fake = run(monkeypatch, [ended()], allow_script=False)
    options = fake.calls[0][1]
    assert P + "run_script" not in options.allowed_tools and P + "run_script" in options.disallowed_tools
    _, _, fake = run(monkeypatch, [ended()])
    assert P + "run_script" in fake.calls[0][1].allowed_tools


def test_unmet_checks_are_not_a_failure(monkeypatch):
    """Ein Aufbau, dessen Prüfungen noch nicht erfüllt sind (ok false, error null), zählt nicht als Fehlschlag."""
    pending = text({"ok": False, "error": None, "solid": {"ok": True}, "dims": []})
    op = {"part": "plate", "distance": 1, "mode": "add"}
    session = [
        *step("extrude", op, pending),
        *step("extrude", op, pending),
        *step("extrude", op, pending),
        *step("extrude", op, pending),
        *step("export_step", {"part": "plate"}, text({"ok": True, "files": {"step": "plate.step"}})),
        ended(),
    ]
    result, events, _ = run(monkeypatch, session)
    assert result["ok"] is True and [e["ok"] for e in events if e["type"] == "tool_result"] == [False] * 4 + [True]


def test_repeated_failing_call_stops(monkeypatch):
    bad = text({"ok": False, "error": "no"})
    session = [
        *step("extrude", {"part": "plate", "distance": 1, "mode": "add"}, bad),
        *step("extrude", {"mode": "add", "distance": 1, "part": "plate"}, bad),
        *step("extrude", {"part": "plate", "mode": "add", "distance": 1}, bad),
        *step("get_state", {"part": "plate"}, text({"ok": True})),
        ended(),
    ]
    result, events, fake = run(monkeypatch, session)
    assert result["ok"] is False and result["reason"] == "repeatedly failed call extrude" and result["turns"] == 3
    assert fake.cut == 1 and [e["type"] for e in events].count("tool_result") == 3
    assert events[-1] == {"type": "done"} | result


def test_other_arguments_do_not_count_as_repeated(monkeypatch):
    bad = text({"ok": False, "error": "no"})
    session = [*(s for d in (1, 2, 3, 4) for s in step("extrude", {"part": "plate", "distance": d}, bad)), ended()]
    result, _, fake = run(monkeypatch, session)
    assert "wiederholt" not in result["reason"] and fake.cut == 0


def test_same_arguments_with_changing_answers_stop_later(monkeypatch):
    session = [
        *(s for i in range(7) for s in step("add_holes", {"part": "plate", "d": 4}, text({"ok": False, "error": f"anderer Fehler {i}"}))),
        ended(),
    ]
    result, events, _ = run(monkeypatch, session)
    assert result["reason"] == "repeatedly failed call add_holes" and kinds(events).count(("tool_result", "add_holes")) == 6


def test_max_turns_reason(monkeypatch):
    data = {"subtype": "error_max_turns", "errors": ["Reached maximum number of turns (2)"], "terminal_reason": "max_turns"}
    stop = ended(subtype="error_max_turns", is_error=True, num_turns=3, errors=data["errors"], terminal_reason="max_turns")
    session = [
        *step("get_state", {"part": "plate"}, text({"ok": True})),
        stop,
        ResultError("Claude Code returned an error result: Reached maximum number of turns (2)", data=data, exit_code=1),
    ]
    result, _, fake = run(monkeypatch, session, max_turns=2)
    assert result["ok"] is False and result["reason"] == "max_turns reached" and result["turns"] == 1
    assert fake.calls[0][1].max_turns == 2


def test_session_error_gives_its_text_as_reason(monkeypatch):
    failed = ended(is_error=True, result="Invalid API key · Please run /login", num_turns=1, total_cost_usd=0)
    result, _, _ = run(monkeypatch, [failed, ResultError("Claude Code returned an error result", data={"subtype": "success"})])
    assert result["ok"] is False and result["reason"] == "Invalid API key · Please run /login"


def test_no_export_is_a_failure(monkeypatch):
    session = [*step("export_step", {"part": "plate"}, text({"ok": False, "error": "validation failed"})), say("Giving up."), ended()]
    result, _, _ = run(monkeypatch, session)
    assert result["ok"] is False and "export_step" in result["reason"] and result["part"] == "plate"


def test_tool_call_after_export_is_denied_and_the_cost_arrives(monkeypatch):
    """Der Hook verweigert den Aufruf, der Strom läuft bis zur ResultMessage weiter: Kosten und Usage stimmen."""
    session = [*EXPORT, *step("edit_op", {"part": "plate", "index": 0, "op": {}}, text({"ok": True})), say("Done."), ended()]
    result, events, fake = run(monkeypatch, session)
    assert result["ok"] and result["files"] == FILES and fake.cut == 0 and result["turns"] == 3
    assert result["cost_usd"] == 0.5 and result["usage"]["input_tokens"] == 100
    assert "edit_op" not in [e.get("name") for e in events]


def test_hook_denies_after_the_export_but_allows_the_mid_export(monkeypatch):
    decisions = []

    def ask(name, args=None):
        async def call(options):
            hook = options.hooks["PreToolUse"][0].hooks[0]
            out = await hook({"tool_name": P + name, "tool_input": args or {}}, "t", {"signal": None})
            decisions.append((name, (out.get("hookSpecificOutput") or {}).get("permissionDecision"), out))

        return call

    mid = {"part": "plate", "variant": "mid"}
    session = [
        ask("edit_op"),
        *EXPORT,
        ask("edit_op"),
        ask("export_step", mid),
        ask("export_step", {"part": "plate"}),
        say("Done."),
        ended(),
    ]
    run(monkeypatch, session)
    assert [d[:2] for d in decisions] == [
        ("edit_op", None),  # vor dem Export ist alles erlaubt
        ("edit_op", "deny"),
        ("export_step", None),
        ("export_step", "deny"),
    ]
    out = decisions[1][2]["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and "exported" in out["permissionDecisionReason"]


def test_model_that_ignores_the_denials_is_cut_with_unknown_cost(monkeypatch):
    calls = [step("edit_op", {"part": "plate", "index": i, "op": {}}, text({"ok": True})) for i in range(3)]
    result, _, fake = run(monkeypatch, [*EXPORT, *calls[0], *calls[1], *calls[2], ended()])
    assert result["ok"] and result["files"] == FILES and fake.cut == 1 and result["cost_usd"] is None
    result, _, fake = run(monkeypatch, [*EXPORT, *calls[0], *calls[1], say("Done."), ended()])
    assert fake.cut == 0 and result["cost_usd"] == 0.5


def test_backend_env_login():
    assert be.backend_env(Backend()) == {}
    assert be.backend_env(Backend("sonnet")) == {}


def test_backend_env_api_key():
    assert be.backend_env(Backend("sonnet", api_key="sk-test")) == {"ANTHROPIC_API_KEY": "sk-test"}


def test_backend_env_openrouter():
    env = be.backend_env(Backend("anthropic/claude-sonnet-5.5", "https://openrouter.ai/api", "or-test"))
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "or-test" and env["ANTHROPIC_API_KEY"] == ""
    assert {env[name] for name in be.MODEL_VARS} == {"anthropic/claude-sonnet-5.5"}
    assert env["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0" and env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "1"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_backend_env_ollama():
    env = be.backend_env(Backend("qwen3.8:27b-mlx", "http://localhost:11434"))
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "ollama" and env["ANTHROPIC_API_KEY"] == ""
    assert {env[name] for name in be.MODEL_VARS} == {"qwen3.8:27b-mlx"}
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "64000"  # eine Modellkennung, die die CLI nicht kennt, hat kein eigenes Fenster
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in be.backend_env(Backend("claude-sonnet-5-5", "http://localhost:11434"))
    assert not any(name in be.backend_env(Backend(base_url="http://localhost:11434")) for name in be.MODEL_VARS)


def test_server_config_stdio_http_command(monkeypatch):
    for name, value in {
        "D2C_DATA": "/drawings",
        "D2C_TIMEOUT": "5",
        "D2C_LLM_API_KEY": "key-123",
        "D2C_LLM_MODEL": "m",
        "ANTHROPIC_AUTH_TOKEN": "tok-123",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-123",
    }.items():
        monkeypatch.setenv(name, value)
    stdio = be.server_config("stdio")
    assert (stdio["type"], stdio["command"], stdio["args"]) == ("stdio", sys.executable, ["-m", "d2c.server"])
    assert stdio["env"]["D2C_DATA"] == "/drawings" and stdio["env"]["D2C_TIMEOUT"] == "5"
    # Die CLI reicht ihre eigene Umgebung an einen stdio-Server weiter: eine Zugangsnummer wird mit Leerwert überschrieben, nicht nur ausgelassen.
    assert all(
        stdio["env"][name] == "" for name in ("D2C_LLM_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
    )
    assert "D2C_LLM_MODEL" not in stdio["env"]
    assert be.server_config("http://mcp:8765/mcp") == {"type": "http", "url": "http://mcp:8765/mcp"}
    assert be.server_config("https://mcp.example/mcp")["type"] == "http"
    command = be.server_config('docker run -i --rm --network none -v "/my drawings:/data:ro" d2c')
    assert (command["type"], command["command"]) == ("stdio", "docker")
    assert command["args"] == ["run", "-i", "--rm", "--network", "none", "-v", "/my drawings:/data:ro", "d2c"]
    assert "key-123" not in json.dumps(command) and "oauth-123" not in json.dumps(command) and command["env"]["D2C_DATA"] == "/drawings"


def test_session_is_isolated(monkeypatch):
    _, _, fake = run(monkeypatch, [ended()], backend=Backend("m"))
    ((prompt, options),) = fake.calls
    assert options.tools == [] and options.setting_sources == [] and options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk" and options.plugins == [] and options.agents is None
    assert list(options.mcp_servers) == ["d2c"] and options.mcp_servers["d2c"] == be.server_config("stdio")
    assert all(tool.startswith(P) for tool in options.allowed_tools + options.disallowed_tools)
    assert set(options.allowed_tools) | set(options.disallowed_tools) == {P + t for t in (*agent.READ_TOOLS, *agent.BUILD_TOOLS)}
    assert "no-session-persistence" in options.extra_args
    assert options.system_prompt and "plate" in options.system_prompt and "A/plate.pdf" in options.system_prompt
    assert "export_step" in options.system_prompt and "ok: true" in options.system_prompt


def test_session_buffer_holds_a_full_drawing_render(monkeypatch):
    """Ein Ausschnitt mit 2000 px Kantenlänge ergibt als PNG mehrere MB Base64; der SDK-Standard von 1 MB brach den Lauf ab."""
    _, _, fake = run(monkeypatch, [ended()], backend=Backend("m"))
    ((_, options),) = fake.calls
    assert options.max_buffer_size >= 16 * 1024 * 1024


def test_backend_env_reaches_the_session(monkeypatch):
    _, _, fake = run(monkeypatch, [ended()], backend=Backend("qwen", "http://localhost:11434", "local-test"))
    options = fake.calls[0][1]
    assert options.model == "qwen" and options.env == be.SESSION_ENV | be.backend_env(
        Backend("qwen", "http://localhost:11434", "local-test")
    )
    assert int(options.env["CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH"]) >= 16000  # die Serveranweisungen werden nie gekürzt
    assert options.env["ENABLE_TOOL_SEARCH"] == "false"  # alle Werkzeuge im ersten Request: das Modell hat nichts, womit es suchen könnte
    assert int(options.env["MCP_TIMEOUT"]) >= 60000  # der erste Serverstart importiert CadQuery kalt
    _, _, fake = run(monkeypatch, [ended()])
    assert fake.calls[0][1].model is None and fake.calls[0][1].env == be.SESSION_ENV


def test_base_url_needs_a_model(monkeypatch):
    monkeypatch.setattr(agent, "query", Scripted([ended()]))
    with pytest.raises(ValueError, match="model"):
        asyncio.run(agent.run_agent("A/plate.pdf", "plate", backend=Backend(base_url="http://localhost:11434")))


def test_cli_result_and_exit_code(monkeypatch, capsys, tmp_path):
    seen = {}

    async def fake_agent(pdf, part, **kwargs):
        seen.update(pdf=pdf, part=part, **kwargs)
        kwargs["on_event"]({"type": "phase", "phase": "read"})
        result = {"ok": kwargs["max_turns"] != 1, "part": part, "reason": "boom"}
        kwargs["on_event"]({"type": "done"} | result)
        return result

    monkeypatch.setattr(cli, "run_agent", fake_agent)
    monkeypatch.setenv("D2C_LLM_API_KEY", "key-from-env")
    monkeypatch.setenv("D2C_LLM_BASE_URL", "http://llm.local:1")
    monkeypatch.setenv("D2C_LLM_MODEL", "env-model")
    monkeypatch.setenv("D2C_LLM_BUILD_MODEL", "env-build")
    monkeypatch.setenv("D2C_MCP_URL", "http://mcp:8765/mcp")
    assert cli.main(["A/plate.pdf", "--part", "plate", "--events", "-"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0] == {"type": "phase", "phase": "read"} and lines[1]["type"] == "done"
    assert lines[-1] == {"ok": True, "part": "plate", "reason": "boom"}
    assert (seen["pdf"], seen["part"], seen["server"]) == ("A/plate.pdf", "plate", "http://mcp:8765/mcp")
    assert seen["backend"] == Backend("env-model", "http://llm.local:1", "key-from-env")
    assert seen["build"] == Backend("env-build", "http://llm.local:1", "key-from-env")
    assert (seen["spec"], seen["allow_script"], seen["max_turns"]) == (None, True, 80)

    spec_file, events_file = tmp_path / "spec.json", tmp_path / "events.jsonl"
    spec_file.write_text(json.dumps(SPEC))
    args = [
        "A/plate.pdf",
        "--part",
        "plate",
        "--model",
        "cli-model",
        "--server",
        "stdio",
        "--spec",
        str(spec_file),
        "--no-script",
        "--max-turns",
        "1",
        "--events",
        str(events_file),
    ]
    assert cli.main(args) == 1
    out = capsys.readouterr().out
    assert json.loads(out.splitlines()[-1])["ok"] is False and "key-from-env" not in out
    assert (seen["backend"].model, seen["server"], seen["spec"], seen["allow_script"], seen["max_turns"]) == (
        "cli-model",
        "stdio",
        SPEC,
        False,
        1,
    )
    assert [json.loads(line)["type"] for line in events_file.read_text().splitlines()] == ["phase", "done"]
    with pytest.raises(SystemExit):  # der Schlüssel kommt nur aus D2C_LLM_API_KEY
        cli.main(["A/plate.pdf", "--part", "plate", "--api-key", "x"])


def test_cli_writes_a_run_trace(monkeypatch, tmp_path):
    """Ein Kommandozeilenlauf hinterlässt dasselbe Protokoll unter results/<teil>/runs/ wie ein Web-Lauf."""
    monkeypatch.setenv("D2C_RESULTS", str(tmp_path / "results"))

    async def fake_agent(pdf, part, **kwargs):
        kwargs["on_event"]({"type": "phase", "phase": "read"})
        result = {"ok": True, "part": part, "files": FILES}
        kwargs["on_event"]({"type": "done"} | result)
        return result

    monkeypatch.setattr(cli, "run_agent", fake_agent)
    assert cli.main(["A/plate.pdf", "--part", "plate", "--model", "m"]) == 0
    (path,) = (tmp_path / "results" / "plate" / "runs").glob("*.jsonl")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert (lines[0]["type"], lines[0]["part"], lines[0]["pdf"], lines[0]["model"]) == ("meta", "plate", "A/plate.pdf", "m")
    assert lines[0]["started"] and path.name.endswith(f"-{lines[0]['run_id'][:8]}.jsonl")
    assert [line["type"] for line in lines[1:]] == ["phase", "done"]
    assert all(isinstance(line["at"], (int, float)) for line in lines[1:])


LOCAL = Backend("qwen3.5:4b-mlx", "http://localhost:11435")


def test_local_model_gets_two_lite_sessions(monkeypatch):
    read = [*step("render_drawing", {"pdf": "A/plate.pdf"}, text("{}")), *SUBMIT, say("Spec submitted."), ended()]
    build = [*EXPORT, say("Done."), ended()]
    result, _, fake = run(monkeypatch, read, build, backend=LOCAL)
    (_, first), (_, second) = fake.calls
    assert first.model == second.model == "qwen3.5:4b-mlx"
    assert first.allowed_tools == [P + t for t in agent.LITE_READ]
    assert second.allowed_tools == [P + t for t in agent.LITE_BUILD]
    assert result["ok"] and fake.cut == 0


def test_lite_tool_names_exist_and_are_small():
    assert set(agent.LITE_READ) <= set(agent.READ_TOOLS) and set(agent.LITE_BUILD) <= set(agent.BUILD_TOOLS) | set(agent.READ_TOOLS)
    assert not {"edit_op", "check_spec", "run_script", "move_op"} & {*agent.LITE_READ, *agent.LITE_BUILD}


def test_lite_override_and_claude_model_on_base_url(monkeypatch):
    _, _, fake = run(monkeypatch, [ended()], backend=Backend("claude-sonnet-5-5", "https://gw.example"))
    assert len(fake.calls) == 1 and P + "edit_op" in fake.calls[0][1].allowed_tools  # ein Claude-Modell bleibt voll
    _, _, fake = run(monkeypatch, [ended()], backend=Backend("qwen", "http://localhost:11435", lite=False))
    assert len(fake.calls) == 1 and P + "edit_op" in fake.calls[0][1].allowed_tools
    _, _, fake = run(monkeypatch, [*SUBMIT, ended()], [ended()], backend=Backend("sonnet", lite=True))
    assert [c[1].allowed_tools for c in fake.calls] == [[P + t for t in agent.LITE_READ], [P + t for t in agent.LITE_BUILD]]


def test_sonnet_without_base_url_stays_one_full_session(monkeypatch):
    _, _, fake = run(monkeypatch, [*EXPORT, ended()], backend=Backend("sonnet"))
    ((_, options),) = fake.calls
    assert set(options.allowed_tools) == {P + t for t in (*agent.READ_TOOLS, *agent.BUILD_TOOLS)}


def test_build_base_url_only_reaches_the_build_session(monkeypatch):
    read = [*SUBMIT, say("Submitted."), ended()]
    build = [*EXPORT, say("Done."), ended()]
    _, _, fake = run(monkeypatch, read, build, backend=Backend("sonnet"), build=Backend("qwen3.5:4b-mlx", "http://localhost:11435"))
    (_, first), (_, second) = fake.calls
    assert "ANTHROPIC_BASE_URL" not in first.env and first.env == be.SESSION_ENV
    assert second.env["ANTHROPIC_BASE_URL"] == "http://localhost:11435" and second.model == "qwen3.5:4b-mlx"
    assert second.allowed_tools == [P + t for t in agent.LITE_BUILD]  # das lokale Modell baut mit den kleinen Werkzeugen
    assert first.allowed_tools == [P + t for t in agent.READ_TOOLS]  # Sonnet liest mit allen


def test_build_base_url_needs_a_model(monkeypatch):
    monkeypatch.setattr(agent, "query", Scripted([ended()]))
    with pytest.raises(ValueError, match="model"):
        asyncio.run(agent.run_agent("A/plate.pdf", "plate", backend=Backend(), build=Backend(base_url="http://localhost:11435")))


def test_local_environment(monkeypatch):
    env = be.backend_env(Backend("qwen3.5:4b-mlx", "http://localhost:11435"))
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "64000" and env["API_TIMEOUT_MS"] == "1800000"
    assert "MAX_THINKING_TOKENS" not in env
    monkeypatch.setenv("D2C_LLM_CONTEXT", "32000")
    monkeypatch.setenv("D2C_LLM_THINKING", "2000")
    env = be.backend_env(Backend("qwen3.5:4b-mlx", "http://localhost:11435"))
    assert (env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], env["MAX_THINKING_TOKENS"]) == ("32000", "2000")
    claude = be.backend_env(Backend("claude-sonnet-5-5", "http://localhost:11435"))
    assert not {"CLAUDE_CODE_MAX_CONTEXT_TOKENS", "API_TIMEOUT_MS", "MAX_THINKING_TOKENS"} & claude.keys()


def test_given_spec_is_written_to_the_results_folder(monkeypatch, tmp_path):
    _, _, fake = run(monkeypatch, [*EXPORT, ended()], spec=SPEC | {"name": "other"}, backend=Backend("m1"))
    folder = tmp_path / "results" / "plate"
    assert json.loads((folder / "spec.json").read_text(encoding="utf-8")) == SPEC | {"name": "plate"}
    assert json.loads((folder / "ops.json").read_text(encoding="utf-8")) == []
    prompt = fake.calls[0][0]
    assert "unchanged" not in prompt and "First call submit_spec" not in prompt
    assert "submit_spec" in prompt and json.dumps(SPEC | {"name": "plate"}, ensure_ascii=False, separators=(",", ":")) in prompt


def test_given_spec_resets_the_ops_of_an_earlier_run(monkeypatch, tmp_path):
    folder = tmp_path / "results" / "plate"
    folder.mkdir(parents=True)
    (folder / "ops.json").write_text('[{"type": "extrude"}]')
    run(monkeypatch, [ended()], spec=SPEC, backend=Backend("m1"))
    assert (folder / "ops.json").read_text() == "[]"


def test_unwritable_results_folder_falls_back_to_submit_spec(monkeypatch, tmp_path):
    blocker = tmp_path / "results"
    blocker.write_text("eine Datei, kein Ordner")
    _, _, fake = run(monkeypatch, [ended()], spec=SPEC, backend=Backend("m1"))
    assert "First call submit_spec" in fake.calls[0][0]


def test_part_name_cannot_leave_the_results_folder(monkeypatch, tmp_path):
    async def go():
        return await agent.run_agent("A/x.pdf", "../evil", backend=Backend(), spec=SPEC)

    monkeypatch.setattr(agent, "query", Scripted([ended()]))
    asyncio.run(go())
    assert not (tmp_path / "evil").exists() and not (tmp_path / "results" / ".." / "evil" / "spec.json").exists()


def test_prompts_name_the_tools_with_prefix(monkeypatch):
    _, _, fake = run(monkeypatch, [ended()], backend=Backend("sonnet"))
    assert "mcp__d2c__" in fake.calls[0][1].system_prompt
    _, _, fake = run(monkeypatch, [*SUBMIT, ended()], [ended()], backend=LOCAL)
    read, build = (c[1].system_prompt for c in fake.calls)
    assert "mcp__d2c__submit_spec" in read and "mcp__d2c__render_drawing" in read and "next" in read
    assert "mcp__d2c__add_holes" in build and "mcp__d2c__export_step" in build and "next" in build
    assert "{part}" not in build and "{pdf}" not in read  # kein offenes Platzhalterfeld


def test_stderr_callback_keeps_the_last_20_lines(monkeypatch):
    lines = [f"zeile {i}" for i in range(30)]
    failure = ProcessError("Command failed", exit_code=1, stderr="Check stderr output for details")
    result, events, _ = run(monkeypatch, [lambda options: [options.stderr(x) for x in lines], failure], backend=Backend("m"))
    (seen,) = [e for e in events if e["type"] == "stderr"]
    assert seen["lines"] == lines[-20:]
    assert result["ok"] is False and "ProcessError" in result["reason"]
    assert events.index(seen) < events.index(events[-1]) and events[-1]["type"] == "done"


def test_exit_143_is_reported_as_sigterm_from_outside(monkeypatch):
    failure = ProcessError("Command failed with exit code 143", exit_code=143, stderr="Check stderr output for details")
    result, events, _ = run(monkeypatch, [lambda options: options.stderr("terminated"), failure], backend=Backend("m"))
    assert "SIGTERM from outside" in result["reason"] and "143" in result["reason"]
    assert {"type": "stderr", "lines": ["terminated"]} in events and events[-1] == {"type": "done"} | result


def test_stderr_lines_do_not_leak_into_the_next_session(monkeypatch):
    read = [lambda options: options.stderr("alt"), *SUBMIT, say("ok"), ended()]
    build = [ProcessError("x", exit_code=143)]
    _, events, _ = run(monkeypatch, read, build, backend=LOCAL)
    assert [e["lines"] for e in events if e["type"] == "stderr"] == [[]]


MID = step(
    "export_step",
    {"part": "plate", "variant": "mid"},
    text({"ok": True, "files": {"step": "/r/plate/plate_mid.step", "report": "/r/plate/report_mid.json"}, "report": {}}),
)
MID_FILES = {"step_mid": "/r/plate/plate_mid.step", "report_mid": "/r/plate/report_mid.json"}


def test_mid_export_after_the_nominal_one_is_kept_and_merged(monkeypatch):
    """Ein Modell, das die Toleranzmitte nacheinander exportiert: der Aufruf wird nicht verworfen, `files` bleibt der Nennexport."""
    result, events, fake = run(monkeypatch, [*EXPORT, *MID, say("Done."), ended()])
    assert result["ok"] and fake.cut == 0 and result["turns"] == 3
    assert result["files"] == FILES | MID_FILES and result["files"]["step"] == FILES["step"]
    assert [e["name"] for e in events if e["type"] == "tool_call"] == ["export_step", "export_step"]


def test_mid_export_before_the_nominal_one_does_not_finish(monkeypatch):
    session = [*MID, *EXPORT, say("Done."), ended()]
    result, _, fake = run(monkeypatch, session)
    assert result["ok"] and result["files"] == FILES | MID_FILES and fake.cut == 0
    result, _, _ = run(monkeypatch, [*MID, say("Only mid."), ended()])
    assert result["ok"] is False and "export_step" in result["reason"]  # die Toleranzmitte allein ist kein Ergebnis


def test_other_tool_after_the_mid_export_is_still_denied(monkeypatch):
    session = [*EXPORT, *MID, *step("edit_op", {"part": "plate", "index": 0, "op": {}}, text({"ok": True})), ended()]
    result, events, fake = run(monkeypatch, session)
    assert result["ok"] and fake.cut == 0 and result["cost_usd"] == 0.5 and "edit_op" not in [e.get("name") for e in events]


def test_limit_defaults_and_environment(monkeypatch):
    monkeypatch.delenv("D2C_MAX_USD", raising=False)
    monkeypatch.delenv("D2C_MAX_MINUTES", raising=False)
    _, _, fake = run(monkeypatch, [ended()])
    assert fake.calls[0][1].max_budget_usd == 3.0
    monkeypatch.setenv("D2C_MAX_USD", "1.5")
    _, _, fake = run(monkeypatch, [ended()])
    assert fake.calls[0][1].max_budget_usd == 1.5
    _, _, fake = run(monkeypatch, [ended()], max_usd=0)  # 0 hebt das Limit auf
    assert fake.calls[0][1].max_budget_usd is None
    monkeypatch.setenv("D2C_MAX_MINUTES", "abc")
    with pytest.raises(ValueError, match="D2C_MAX_MINUTES"):
        run(monkeypatch, [ended()])
    monkeypatch.setenv("D2C_MAX_MINUTES", "-1")
    with pytest.raises(ValueError, match="negativ"):
        run(monkeypatch, [ended()])


def test_budget_limit_ends_the_run_with_a_reason(monkeypatch):
    stop = ended(subtype="error_max_budget_usd", is_error=True, result=None, total_cost_usd=3.2, num_turns=4)
    session = [
        *step("get_state", {"part": "plate"}, text({"ok": True})),
        stop,
        ResultError("Claude Code returned an error result", data={"subtype": "error_max_budget_usd"}, exit_code=1),
    ]
    result, events, _ = run(monkeypatch, session, max_usd=3)
    assert result["ok"] is False and result["limit"] == "budget" and result["reason"] == "cost limit of 3 USD reached"
    assert result["cost_usd"] == 3.2 and events[-1] == {"type": "done"} | result


def test_second_session_gets_only_the_remaining_budget(monkeypatch):
    read = [*SUBMIT, say("Spec submitted."), ended(total_cost_usd=2.0)]
    build = [*EXPORT, say("Done."), ended(total_cost_usd=0.25)]
    result, _, fake = run(monkeypatch, read, build, backend=Backend("reader"), build=Backend("builder"), max_usd=3)
    assert [c[1].max_budget_usd for c in fake.calls] == [3, 1.0] and result["ok"] and result["cost_usd"] == 2.25
    read = [*SUBMIT, say("Spec submitted."), ended(total_cost_usd=3.5)]  # schon überschritten: keine zweite Sitzung
    result, _, fake = run(monkeypatch, read, backend=Backend("reader"), build=Backend("builder"), max_usd=3)
    assert len(fake.calls) == 1 and result["limit"] == "budget" and result["ok"] is False


def test_time_limit_ends_the_run_with_a_reason(monkeypatch):
    closed = []

    async def hang(*, prompt, options):
        try:
            yield say("Working.")
            await asyncio.sleep(30)
        finally:
            closed.append(True)  # der Generator wurde geschlossen, die CLI also beendet

    monkeypatch.setattr(agent, "query", hang)
    events = []
    started = time.monotonic()
    result = asyncio.run(agent.run_agent("A/plate.pdf", "plate", backend=Backend(), max_minutes=0.01, on_event=events.append))
    assert time.monotonic() - started < 5 and closed == [True]
    assert result["ok"] is False and result["limit"] == "time" and result["reason"] == "time limit of 0.01 min reached"
    assert events[-1] == {"type": "done"} | result


def test_cli_passes_build_base_url_and_lite(monkeypatch, capsys):
    seen = {}

    async def fake_agent(pdf, part, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "part": part}

    monkeypatch.setattr(cli, "run_agent", fake_agent)
    monkeypatch.setenv("D2C_LLM_BUILD_BASE_URL", "http://env-build:1")
    cli.main(["A/plate.pdf", "--part", "plate", "--model", "m"])
    assert seen["build"].base_url == "http://env-build:1" and seen["backend"].lite is None
    cli.main(["A/plate.pdf", "--part", "plate", "--model", "m", "--build-base-url", "http://cli:2", "--lite"])
    assert seen["build"].base_url == "http://cli:2" and seen["backend"].lite is True
    cli.main(["A/plate.pdf", "--part", "plate", "--model", "m", "--no-lite"])
    assert seen["backend"].lite is False


def test_cli_passes_the_limits(monkeypatch):
    seen = {}

    async def fake_agent(pdf, part, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "part": part}

    monkeypatch.setattr(cli, "run_agent", fake_agent)
    assert cli.main(["A/plate.pdf", "--part", "plate"]) == 0
    assert (seen["max_usd"], seen["max_minutes"]) == (None, None)  # run_agent löst Standard und Umgebung auf
    assert cli.main(["A/plate.pdf", "--part", "plate", "--max-usd", "1.5", "--max-minutes", "5"]) == 0
    assert (seen["max_usd"], seen["max_minutes"]) == (1.5, 5)


def test_backend_defaults():
    backend = Backend()
    assert (backend.model, backend.base_url, backend.api_key, backend.lite) == (None, None, None, None)
    assert not backend.is_local() and not backend.use_lite()
    assert Backend("qwen", "http://localhost:11434").use_lite()  # lokales Modell: kleine Werkzeugsätze
    assert not Backend("claude-sonnet-5-5", "https://gw.example").use_lite()
    assert Backend("sonnet", lite=True).use_lite() and not Backend("qwen", "http://x", lite=False).use_lite()
    assert Backend("a", "http://x", "k").for_build("b", None) == Backend("b", "http://x", "k")
    assert Backend("a").for_build(None, None) == Backend("a")
    with pytest.raises(dataclasses.FrozenInstanceError):
        backend.model = "x"  # type: ignore[misc]


def test_run_agent_rejects_conflicting_backend(monkeypatch):
    """Eine base_url ohne Modell scheitert vor dem Start, für Lese- wie Bausitzung, mit derselben Meldung."""
    fake = Scripted([ended()])
    monkeypatch.setattr(agent, "query", fake)
    for kwargs in (
        {"backend": Backend(base_url="http://localhost:11434")},
        {"backend": Backend("m"), "build": Backend(base_url="http://localhost:11435")},
    ):
        with pytest.raises(ValueError, match="A base_url requires a model"):
            asyncio.run(agent.run_agent("A/plate.pdf", "plate", **kwargs))
    assert fake.calls == []


def test_cli_help_english(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert stop.value.code == 0
    assert "drawing PDF" in out and "name of the part" in out and "Options" in out
    assert "Zeichnungs-PDF" not in out and "Name des Teils" not in out
    for flag in (
        "--part",
        "--model",
        "--build-model",
        "--base-url",
        "--build-base-url",
        "--lite",
        "--no-lite",
        "--server",
        "--spec",
        "--revise",
        "--no-script",
        "--max-turns",
        "--events",
    ):
        assert flag in out


def stored_part(tmp_path):
    folder = tmp_path / "results" / "plate"
    folder.mkdir(parents=True)
    (folder / "spec.json").write_text(json.dumps(SPEC), encoding="utf-8")
    (folder / "ops.json").write_text("[]", encoding="utf-8")


def test_revise_without_a_stored_part_fails_with_a_reason(monkeypatch):
    result, events, fake = run(monkeypatch, [ended()], revise=True)
    assert result["ok"] is False and "no stored spec and ops" in result["reason"]
    assert fake.calls == [] and events[-1]["type"] == "done"


def test_revise_runs_one_session_with_read_and_build_tools(monkeypatch, tmp_path):
    stored_part(tmp_path)
    changes = [{"area": "dimension", "change": "changed", "feature": "length d1", "before": "80", "after": "90"}]
    session = [
        *step("get_state", {"part": "plate"}, text({"ops": []})),
        *step("submit_spec", {"spec": SPEC, "keep_ops": True}, text({"ok": True, "part": "plate", "changes": changes})),
        *step("edit_op", {"part": "plate", "index": 0}, text({"ok": True})),
        *EXPORT,
        *step("export_step", {"part": "plate", "variant": "mid"}, text({"ok": True, "files": {"step": "m.step", "report": "m.json"}})),
        ended(),
    ]
    result, events, fake = run(monkeypatch, session, revise=True)
    ((prompt, options),) = fake.calls
    assert set(options.allowed_tools) == {P + t for t in (*agent.READ_TOOLS, *agent.BUILD_TOOLS)}
    assert "already exists" in prompt and "keep_ops=true" in prompt and "get_state" in prompt and "variant mid" in prompt
    assert "never rebuild it from scratch" in options.system_prompt and "{part}" not in options.system_prompt
    assert result["ok"] and result["changes"] == changes and result["files"]["step_mid"] == "m.step"
    assert events[-1]["changes"] == changes


def test_revise_without_changes_leaves_them_out_of_the_result(monkeypatch, tmp_path):
    stored_part(tmp_path)
    result, _, _ = run(monkeypatch, [*EXPORT, ended()], revise=True)
    assert result["ok"] and "changes" not in result


def test_revise_respects_no_script_and_lite(monkeypatch, tmp_path):
    stored_part(tmp_path)
    _, _, fake = run(monkeypatch, [ended()], revise=True, allow_script=False)
    assert P + "run_script" not in fake.calls[0][1].allowed_tools
    _, _, fake = run(monkeypatch, [ended()], revise=True, backend=LOCAL)
    options = fake.calls[0][1]
    assert set(options.allowed_tools) == {P + t for t in (*agent.LITE_READ, *agent.LITE_BUILD)}
    assert "mcp__d2c__delete_op" in options.system_prompt and "{part}" not in options.system_prompt


def test_cli_passes_revise(monkeypatch):
    seen = {}

    async def fake_agent(pdf, part, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "part": part}

    monkeypatch.setattr(cli, "run_agent", fake_agent)
    assert cli.main(["A/plate.pdf", "--part", "plate", "--revise"]) == 0
    assert seen["revise"] is True
    assert cli.main(["A/plate.pdf", "--part", "plate"]) == 0
    assert seen["revise"] is False

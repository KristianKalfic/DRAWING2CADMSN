"""The agent with scripted SDK sessions in place of the CLI: events, phases, tool lists, stops, backends, command line."""

import asyncio
import json
import sys
from itertools import count

import pytest
from claude_agent_sdk import AssistantMessage, ResultError, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

from d2c import agent

P = "mcp__d2c__"
FILES = {"step": "/app/results/plate/plate.step", "review": "/app/results/plate/review.md"}
SPEC = {"name": "plate", "drawing": "A/plate.pdf", "title": "Plate", "scale": "1:1"}  # the agent does not look inside
ids = count(1)


class Scripted:
    """Stands in for query(): plays the next scripted session per call and keeps the options it was called with."""

    def __init__(self, *sessions):
        self.sessions, self.calls, self.cut = list(sessions), [], 0

    async def __call__(self, *, prompt, options):
        self.calls.append((prompt, options))
        try:
            for item in self.sessions.pop(0):
                if isinstance(item, Exception):
                    raise item
                yield item
        except GeneratorExit:  # the consumer closed the session before the script ended
            self.cut += 1
            raise


def text(obj) -> list:
    """An MCP tool result as the CLI reports it: a list of content blocks."""
    return [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, indent=2)}]


def step(name, args, result, *, error=False) -> list:
    """An assistant tool call and the result that follows it."""
    tid = f"toolu_{next(ids)}"
    return [AssistantMessage(content=[ToolUseBlock(tid, P + name, args)], model="m"),
            UserMessage(content=[ToolResultBlock(tid, result, is_error=True if error else None)])]


def say(words) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(words)], model="m")


def ended(**more) -> ResultMessage:
    fields = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=3, session_id="s",
                  total_cost_usd=0.5, usage={"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 7,
                                             "cache_creation_input_tokens": 3, "service_tier": "standard"})
    return ResultMessage(**(fields | more))


SUBMIT = step("submit_spec", {"spec": SPEC}, text({"ok": True, "part": "plate"}))
EXPORT = step("export_step", {"part": "plate"}, text({"ok": True, "files": FILES, "report": {}}))


def run(monkeypatch, *sessions, **kwargs):
    """run_agent on scripted sessions: (result, events, the Scripted query)."""
    fake, events = Scripted(*sessions), []
    monkeypatch.setattr(agent, "query", fake)
    result = asyncio.run(agent.run_agent("A/plate.pdf", "plate", on_event=events.append, **kwargs))
    return result, events, fake


def kinds(events) -> list:
    return [(e["type"], e.get("phase") or e.get("name")) for e in events]


def test_scripted_run_events_and_result(monkeypatch):
    session = [
        AssistantMessage(content=[TextBlock("Looking at the drawing."), ToolUseBlock("t0", P + "render_drawing", {"pdf": "A/plate.pdf"})], model="m"),
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
        ("phase", "read"), ("message", None), ("tool_call", "render_drawing"), ("tool_result", "render_drawing"),
        ("tool_call", "read_drawing_text"), ("tool_result", "read_drawing_text"), ("tool_call", "submit_spec"),
        ("tool_result", "submit_spec"), ("phase", "build"), ("tool_call", "extrude"), ("tool_result", "extrude"),
        ("tool_call", "extrude"), ("tool_result", "extrude"), ("tool_call", "export_step"), ("tool_result", "export_step"),
        ("message", None), ("done", None)]
    assert events[1] == {"type": "message", "text": "Looking at the drawing."}
    assert events[2] == {"type": "tool_call", "name": "render_drawing", "args": {"pdf": "A/plate.pdf"}}
    assert [e["ok"] for e in events if e["type"] == "tool_result"] == [True, True, True, False, True, True]
    assert result == {"ok": True, "part": "plate", "turns": 7, "files": FILES, "cost_usd": 0.5,
                      "usage": {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 3}}
    assert events[-1] == {"type": "done"} | result
    assert fake.cut == 0 and len(fake.calls) == 1


def test_image_result_reported_as_image(monkeypatch):
    shown = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
             {"type": "text", "text": '{"pages": 1}'}]
    _, events, _ = run(monkeypatch, [*step("render_drawing", {"pdf": "A/plate.pdf"}, shown), ended()])
    assert [e for e in events if e["type"] == "tool_result"] == [
        {"type": "tool_result", "name": "render_drawing", "ok": True, "text": "image"}]


def test_tool_result_ok_false_from_json(monkeypatch):
    session = [
        *step("get_state", {"part": "plate"}, text("x" * 600)),
        *step("extrude", {"part": "plate"}, text({"ok": False, "error": "no"})),
        *step("delete_op", {"part": "plate", "index": 9}, "Error executing tool delete_op: out of range", error=True),
        *step("move_op", {"part": "plate", "index": 0, "new_index": 1}, text({"ok": True})),
        ended()]
    _, events, _ = run(monkeypatch, session)
    results = [e for e in events if e["type"] == "tool_result"]
    assert [e["ok"] for e in results] == [True, False, False, True]
    assert results[0]["text"] == "x" * 500
    assert results[2]["text"] == "Error executing tool delete_op: out of range"


def test_two_phase_switches_model_and_tools(monkeypatch):
    read = [*step("render_drawing", {"pdf": "A/plate.pdf"}, text("{}")), *SUBMIT, say("Spec submitted."), ended()]
    build = [*step("extrude", {"part": "plate", "distance": 10}, text({"ok": True})), *EXPORT, say("Done."), ended()]
    result, events, fake = run(monkeypatch, read, build, model="reader", build_model="builder")
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


def test_two_phase_cuts_a_reader_that_keeps_calling_tools(monkeypatch):
    read = [*SUBMIT, *step("render_drawing", {"pdf": "A/plate.pdf"}, text("{}")), ended()]
    build = [*EXPORT, say("Done."), ended()]
    result, events, fake = run(monkeypatch, read, build, model="reader", build_model="builder")
    assert result["ok"] and fake.cut == 1 and len(fake.calls) == 2
    assert "render_drawing" not in [e.get("name") for e in events]


def test_build_session_uses_the_name_the_reader_submitted(monkeypatch):
    read = [*step("submit_spec", {"spec": SPEC | {"name": "other"}}, text({"ok": True})), say("Submitted."), ended()]
    build = [*step("export_step", {"part": "other"}, text({"ok": True, "files": FILES})), say("Done."), ended()]
    result, _, fake = run(monkeypatch, read, build, model="reader", build_model="builder")
    assert 'part "other"' in fake.calls[1][0] and (result["ok"], result["part"]) == (True, "other")


def test_reader_without_spec_stops_the_run(monkeypatch):
    result, events, fake = run(monkeypatch, [say("I cannot read this."), ended()], model="reader", build_model="builder")
    assert result["ok"] is False and "submit_spec" in result["reason"] and len(fake.calls) == 1
    assert events[-1] == {"type": "done"} | result


def test_spec_skips_reading_phase(monkeypatch):
    session = [*SUBMIT, *step("extrude", {"part": "plate", "distance": 10}, text({"ok": True})), *EXPORT, say("Done."), ended()]
    result, events, fake = run(monkeypatch, session, spec=SPEC | {"name": "other"}, model="m1")
    ((prompt, options),) = fake.calls
    assert options.model == "m1"
    assert set(options.allowed_tools) == {P + t for t in (*agent.BUILD_TOOLS, "submit_spec")}
    assert P + "render_drawing" in options.disallowed_tools and P + "read_drawing_text" in options.disallowed_tools
    assert json.dumps(SPEC | {"name": "plate"}, ensure_ascii=False, separators=(",", ":")) in prompt
    assert kinds(events)[0] == ("phase", "build") and ("phase", "read") not in kinds(events)
    assert result["ok"]


def test_no_script_hides_run_script(monkeypatch):
    _, _, fake = run(monkeypatch, [ended()], allow_script=False)
    options = fake.calls[0][1]
    assert P + "run_script" not in options.allowed_tools and P + "run_script" in options.disallowed_tools
    _, _, fake = run(monkeypatch, [ended()])
    assert P + "run_script" in fake.calls[0][1].allowed_tools


def test_repeated_failing_call_stops(monkeypatch):
    bad = text({"ok": False, "error": "no"})
    session = [*step("extrude", {"part": "plate", "distance": 1, "mode": "add"}, bad),
               *step("extrude", {"mode": "add", "distance": 1, "part": "plate"}, bad),
               *step("extrude", {"part": "plate", "mode": "add", "distance": 1}, bad),
               *step("get_state", {"part": "plate"}, text({"ok": True})), ended()]
    result, events, fake = run(monkeypatch, session)
    assert result["ok"] is False and result["reason"] == "repeated failing call extrude" and result["turns"] == 3
    assert fake.cut == 1 and [e["type"] for e in events].count("tool_result") == 3
    assert events[-1] == {"type": "done"} | result


def test_other_arguments_do_not_count_as_repeated(monkeypatch):
    bad = text({"ok": False, "error": "no"})
    session = [*(s for d in (1, 2, 3, 4) for s in step("extrude", {"part": "plate", "distance": d}, bad)), ended()]
    result, _, fake = run(monkeypatch, session)
    assert "repeated" not in result["reason"] and fake.cut == 0


def test_max_turns_reason(monkeypatch):
    data = {"subtype": "error_max_turns", "errors": ["Reached maximum number of turns (2)"], "terminal_reason": "max_turns"}
    stop = ended(subtype="error_max_turns", is_error=True, num_turns=3, errors=data["errors"], terminal_reason="max_turns")
    session = [*step("get_state", {"part": "plate"}, text({"ok": True})), stop,
               ResultError("Claude Code returned an error result: Reached maximum number of turns (2)", data=data, exit_code=1)]
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


def test_tool_call_after_export_is_cut_but_keeps_the_export(monkeypatch):
    session = [*EXPORT, *step("edit_op", {"part": "plate", "index": 0, "op": {}}, text({"ok": True})), ended()]
    result, events, fake = run(monkeypatch, session)
    assert result["ok"] and result["files"] == FILES and fake.cut == 1 and result["turns"] == 2
    assert "edit_op" not in [e.get("name") for e in events]


def test_backend_env_login():
    assert agent.backend_env(None, None, None) == {}
    assert agent.backend_env(None, None, "sonnet") == {}


def test_backend_env_api_key():
    assert agent.backend_env(None, "sk-test", "sonnet") == {"ANTHROPIC_API_KEY": "sk-test"}


def test_backend_env_openrouter():
    env = agent.backend_env("https://openrouter.ai/api", "or-test", "anthropic/claude-sonnet-5.5")
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "or-test" and env["ANTHROPIC_API_KEY"] == ""
    assert {env[name] for name in agent.MODEL_VARS} == {"anthropic/claude-sonnet-5.5"}
    assert env["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0" and env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "1"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_backend_env_ollama():
    env = agent.backend_env("http://localhost:11434", None, "qwen3.8:27b-mlx")
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "ollama" and env["ANTHROPIC_API_KEY"] == ""
    assert {env[name] for name in agent.MODEL_VARS} == {"qwen3.8:27b-mlx"}
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "65536"  # a model id the CLI does not know has no window of its own
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in agent.backend_env("http://localhost:11434", None, "claude-sonnet-5-5")
    assert not any(name in agent.backend_env("http://localhost:11434", None, None) for name in agent.MODEL_VARS)


def test_server_config_stdio_http_command(monkeypatch):
    for name, value in {"D2C_DATA": "/drawings", "D2C_TIMEOUT": "5", "D2C_LLM_API_KEY": "key-123", "D2C_LLM_MODEL": "m",
                        "ANTHROPIC_AUTH_TOKEN": "tok-123", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-123"}.items():
        monkeypatch.setenv(name, value)
    stdio = agent.server_config("stdio")
    assert (stdio["type"], stdio["command"], stdio["args"]) == ("stdio", sys.executable, ["-m", "d2c.server"])
    assert stdio["env"]["D2C_DATA"] == "/drawings" and stdio["env"]["D2C_TIMEOUT"] == "5"
    # The CLI hands its own environment on to a stdio server: a credential is overridden with an empty value, not just left out.
    assert all(stdio["env"][name] == "" for name in ("D2C_LLM_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"))
    assert "D2C_LLM_MODEL" not in stdio["env"]
    assert agent.server_config("http://mcp:8765/mcp") == {"type": "http", "url": "http://mcp:8765/mcp"}
    assert agent.server_config("https://mcp.example/mcp")["type"] == "http"
    command = agent.server_config('docker run -i --rm --network none -v "/my drawings:/data:ro" d2c')
    assert (command["type"], command["command"]) == ("stdio", "docker")
    assert command["args"] == ["run", "-i", "--rm", "--network", "none", "-v", "/my drawings:/data:ro", "d2c"]
    assert "key-123" not in json.dumps(command) and "oauth-123" not in json.dumps(command) and command["env"]["D2C_DATA"] == "/drawings"


def test_session_is_isolated(monkeypatch):
    _, _, fake = run(monkeypatch, [ended()], model="m")
    (prompt, options), = fake.calls
    assert options.tools == [] and options.setting_sources == [] and options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk" and options.plugins == [] and options.agents is None
    assert list(options.mcp_servers) == ["d2c"] and options.mcp_servers["d2c"] == agent.server_config("stdio")
    assert all(tool.startswith(P) for tool in options.allowed_tools + options.disallowed_tools)
    assert set(options.allowed_tools) | set(options.disallowed_tools) == {P + t for t in (*agent.READ_TOOLS, *agent.BUILD_TOOLS)}
    assert "no-session-persistence" in options.extra_args
    assert options.system_prompt and "plate" in options.system_prompt and "A/plate.pdf" in options.system_prompt
    assert "export_step" in options.system_prompt and "ok: true" in options.system_prompt


def test_backend_env_reaches_the_session(monkeypatch):
    _, _, fake = run(monkeypatch, [ended()], model="qwen", base_url="http://localhost:11434", api_key="local-test")
    options = fake.calls[0][1]
    assert options.model == "qwen" and options.env == agent.SESSION_ENV | agent.backend_env("http://localhost:11434", "local-test", "qwen")
    assert int(options.env["CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH"]) >= 16000  # the server instructions are never cut
    _, _, fake = run(monkeypatch, [ended()])
    assert fake.calls[0][1].model is None and fake.calls[0][1].env == agent.SESSION_ENV


def test_base_url_needs_a_model(monkeypatch):
    monkeypatch.setattr(agent, "query", Scripted([ended()]))
    with pytest.raises(ValueError, match="model"):
        asyncio.run(agent.run_agent("A/plate.pdf", "plate", base_url="http://localhost:11434"))


def test_cli_result_and_exit_code(monkeypatch, capsys, tmp_path):
    seen = {}

    async def fake_agent(pdf, part, **kwargs):
        seen.update(pdf=pdf, part=part, **kwargs)
        kwargs["on_event"]({"type": "phase", "phase": "read"})
        result = {"ok": kwargs["max_turns"] != 1, "part": part, "reason": "boom"}
        kwargs["on_event"]({"type": "done"} | result)
        return result

    monkeypatch.setattr(agent, "run_agent", fake_agent)
    monkeypatch.setenv("D2C_LLM_API_KEY", "key-from-env")
    monkeypatch.setenv("D2C_LLM_BASE_URL", "http://llm.local:1")
    monkeypatch.setenv("D2C_LLM_MODEL", "env-model")
    monkeypatch.setenv("D2C_LLM_BUILD_MODEL", "env-build")
    monkeypatch.setenv("D2C_MCP_URL", "http://mcp:8765/mcp")
    assert agent.main(["A/plate.pdf", "--part", "plate", "--events", "-"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0] == {"type": "phase", "phase": "read"} and lines[1]["type"] == "done"
    assert lines[-1] == {"ok": True, "part": "plate", "reason": "boom"}
    assert (seen["pdf"], seen["part"], seen["model"], seen["build_model"]) == ("A/plate.pdf", "plate", "env-model", "env-build")
    assert (seen["base_url"], seen["api_key"], seen["server"]) == ("http://llm.local:1", "key-from-env", "http://mcp:8765/mcp")
    assert (seen["spec"], seen["allow_script"], seen["max_turns"]) == (None, True, 80)

    spec_file, events_file = tmp_path / "spec.json", tmp_path / "events.jsonl"
    spec_file.write_text(json.dumps(SPEC))
    args = ["A/plate.pdf", "--part", "plate", "--model", "cli-model", "--server", "stdio", "--spec", str(spec_file),
            "--no-script", "--max-turns", "1", "--events", str(events_file)]
    assert agent.main(args) == 1
    out = capsys.readouterr().out
    assert json.loads(out.splitlines()[-1])["ok"] is False and "key-from-env" not in out
    assert (seen["model"], seen["server"], seen["spec"], seen["allow_script"], seen["max_turns"]) == ("cli-model", "stdio", SPEC, False, 1)
    assert [json.loads(line)["type"] for line in events_file.read_text().splitlines()] == ["phase", "done"]
    with pytest.raises(SystemExit):  # the key is read from D2C_LLM_API_KEY only
        agent.main(["A/plate.pdf", "--part", "plate", "--api-key", "x"])

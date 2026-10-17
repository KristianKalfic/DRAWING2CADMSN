"""The agent: drives the MCP server with a model. The Claude Agent SDK runs Claude Code as the agent loop.

Every backend speaks the Anthropic Messages API and needs no code path of its own: the Claude login or an Anthropic key
(no base_url), and OpenRouter or Ollama (base_url, see backend_env). A session sees only the MCP tools of its phase and
nothing else: no built-in tools, no settings, hooks, plugins or CLAUDE.md, no other MCP server. The CLI hands the server
instructions (instructions.md) to the model itself, so the system prompt only adds the role and the rules.

Module-level imports are the standard library and the SDK, so `python -m d2c.agent` runs on a host without CadQuery
(the server then runs elsewhere, e.g. in a container).
"""

import argparse
import asyncio
import json
import os
import shlex
import sys
from collections import Counter
from collections.abc import Callable
from contextlib import aclosing
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKError, ResultMessage, SystemMessage, TextBlock, ToolResultBlock,
    ToolUseBlock, UserMessage, query,
)

SERVER = "d2c"  # the key the server is registered under: its tools are called mcp__d2c__<tool>
PREFIX = f"mcp__{SERVER}__"
READ_TOOLS = ("render_drawing", "read_drawing_text", "measure_drawing", "check_spec", "submit_spec")
BUILD_TOOLS = ("extrude", "revolve", "add_holes", "edge_finish", "run_script", "edit_op", "delete_op", "move_op", "get_state", "export_step")
# So that no request carries an Anthropic model name: the aliases and the background and subagent models.
MODEL_VARS = ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")
SESSION_ENV = {
    "CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH": "16000",  # the CLI cuts server instructions at 4096 characters
    # Every tool in the first request. With tool search (on by default for the Anthropic API) the model would first have to
    # find the tools, and without built-in tools it has nothing to search with.
    "ENABLE_TOOL_SEARCH": "false",
}
SECRETS = ("D2C_LLM_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
MAX_TEXT = 500  # characters of a tool result in its event
MAX_FAILS = 3  # the same failing call this many times ends the run

RULES = (
    "You turn one technical drawing into a validated CAD model of a part. Act only through the tools of the d2c server: "
    "you have no shell, no files and no web.\n"
    "- Call the tools, do not describe what you would do. After every report, apply the rules of the server instructions.\n"
    "- Part name: {part}. Drawing: {pdf} (the path as the tools take it).\n"
)
ENDS = {
    "full": "- You are done only after export_step returns ok: true. Then answer with one short sentence and call no further tool.",
    "read": '- This session only reads the drawing: call submit_spec with name "{part}". Once it returns ok: true, answer with one '
            "short sentence and call no further tool. Another session builds the part.",
    "build": "- The spec is in the prompt: skip the reading steps of the server instructions. You are done only after export_step "
             "returns ok: true. Then answer with one short sentence and call no further tool.",
}


def backend_env(base_url: str | None, api_key: str | None, model: str | None) -> dict[str, str]:
    """The environment variables that point the CLI at its backend.

    Without base_url the CLI uses the Claude login (nothing to set) or an Anthropic key. With one it talks to that
    Anthropic-compatible endpoint (OpenRouter, Ollama, a gateway) with a bearer token; the Anthropic key must be an empty
    string, not unset, or the CLI falls back to an Anthropic login.
    """
    if not base_url:
        return {"ANTHROPIC_API_KEY": api_key} if api_key else {}
    env = {
        "ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_AUTH_TOKEN": api_key or "ollama", "ANTHROPIC_API_KEY": "",
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    if model:
        env |= dict.fromkeys(MODEL_VARS, model)
        if not model.startswith("claude"):
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "65536"  # the CLI assumes 200k for a model id it does not know
    return env


def server_config(server: str) -> dict:
    """The MCP server as the CLI is told to reach it.

    "stdio" starts this interpreter with `-m d2c.server`, an http(s) URL connects to a running server, any other value is
    a command line (e.g. `docker run -i --rm --network none -v ...:/data:ro d2c`). The CLI hands its own environment on to
    a stdio server, so the D2C_ settings are passed explicitly, without D2C_LLM_*, and every credential is blanked out.
    """
    if server.startswith(("http://", "https://")):
        return {"type": "http", "url": server}
    env = {k: v for k, v in os.environ.items() if k.startswith("D2C_") and not k.startswith("D2C_LLM_")}
    command = [sys.executable, "-m", "d2c.server"] if server in ("stdio", "") else shlex.split(server)
    return {"type": "stdio", "command": command[0], "args": command[1:], "env": env | dict.fromkeys(SECRETS, "")}


def _read(block: ToolResultBlock) -> tuple[bool, str, dict]:
    """(ok, text for the event, JSON object of the result): an image shows as "image", ok is false for an error or "ok": false."""
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
    """Why an error result ended its session."""
    if result.subtype == "error_max_turns":
        return "max_turns reached"
    return result.result or "; ".join(result.errors or []) or result.subtype


class _Run:
    """One run over one or two SDK sessions: the events, the failures seen, the totals and what the sessions achieved."""

    def __init__(self, part: str, on_event: Callable[[dict], None] | None) -> None:
        self.part, self.emit = part, on_event or (lambda event: None)
        self.building = False
        self.fails: Counter = Counter()
        self.turns: set = set()  # ids of the model responses seen
        self.cost, self.usage = 0.0, dict.fromkeys(TOKENS, 0)
        self.spec: dict | None = None  # the spec of the last successful submit_spec
        self.files: dict | None = None  # the files of the successful export_step

    def phase(self, name: str) -> None:
        self.building = name == "build"
        self.emit({"type": "phase", "phase": name})

    async def session(self, prompt: str, options: ClaudeAgentOptions, finish: str) -> str | None:
        """Play one session until it ends or has to be stopped; None if it did its job (`finish` succeeded) or ended on its own,
        else why it failed. Stopping closes the generator, which ends the CLI."""
        calls: dict[str, tuple[str, dict]] = {}
        done, reason = False, None
        try:
            async with aclosing(query(prompt=prompt, options=options)) as stream:
                async for msg in stream:
                    if isinstance(msg, ResultMessage):
                        self.cost += msg.total_cost_usd or 0
                        for key in TOKENS:
                            self.usage[key] += (msg.usage or {}).get(key) or 0
                        reason = _why(msg) if msg.is_error else reason
                    elif isinstance(msg, SystemMessage) and msg.subtype == "init":
                        status = {x.get("name"): x.get("status") for x in msg.data.get("mcp_servers", [])}.get(SERVER)
                        if status not in (None, "connected", "pending"):  # pending: still connecting
                            return f"the MCP server {SERVER} is {status}: check the server setting"
                    elif isinstance(msg, AssistantMessage):
                        self.turns.add(msg.message_id or id(msg))  # one response can arrive as several messages
                        for block in msg.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                self.emit({"type": "message", "text": block.text})
                            elif isinstance(block, ToolUseBlock):
                                if done:  # nothing may change the part after the finishing tool
                                    return None
                                name = block.name.removeprefix(PREFIX)
                                calls[block.id] = (name, block.input)
                                if name in BUILD_TOOLS and not self.building:
                                    self.phase("build")
                                self.emit({"type": "tool_call", "name": name, "args": block.input})
                    elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                        for block in msg.content:
                            if not isinstance(block, ToolResultBlock) or block.tool_use_id not in calls:
                                continue
                            name, args = calls[block.tool_use_id]
                            ok, text, data = _read(block)
                            self.emit({"type": "tool_result", "name": name, "ok": ok, "text": text})
                            if not ok:
                                key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                                self.fails[key] += 1
                                if self.fails[key] >= MAX_FAILS:
                                    return f"repeated failing call {name}"
                            elif data.get("ok") is True:
                                if name == "submit_spec" and isinstance(args.get("spec"), dict):
                                    self.spec = args["spec"]
                                elif name == "export_step":
                                    self.files = data.get("files", {})
                                done = done or name == finish
        except ClaudeSDKError as e:  # a ResultError follows the error result that already gave the reason
            reason = reason or f"{type(e).__name__}: {e}"
        return None if done else reason

    def result(self, reason: str | None) -> dict:
        total = {"turns": len(self.turns), "usage": self.usage, "cost_usd": round(self.cost, 6)}
        if self.files is not None:
            return {"ok": True, "part": self.part, "files": self.files} | total
        return {"ok": False, "part": self.part, "reason": reason or "the session ended without a successful export_step"} | total


def _json(spec: dict) -> str:
    return json.dumps(spec, ensure_ascii=False, separators=(",", ":"))


async def run_agent(pdf: str, part: str, *, model: str | None = None, base_url: str | None = None,
                    api_key: str | None = None, build_model: str | None = None, server: str = "stdio",
                    spec: dict | None = None, allow_script: bool = True, max_turns: int = 80,
                    on_event: Callable[[dict], None] | None = None) -> dict:
    """Read the drawing `pdf` (relative to the data folder of the server) and build, check and export the part `part`.

    One model does both phases; with a different `build_model` a first session with `model` reads the drawing and submits the
    spec, a second one with `build_model` builds the part from it (and never sees an image). A given `spec` skips the reading.
    `on_event` gets {"type": "phase"|"tool_call"|"tool_result"|"message"|"done", ...}, last the done event with the result:
    {"ok": True, "part", "turns", "files", "usage", "cost_usd"} after a successful export_step, else {"ok": False, "part",
    "reason", ...}. "turns" counts the model responses; "usage" and "cost_usd" are the CLI's totals of the sessions that ended on
    their own, a session that had to be stopped is not in them.
    """
    if base_url and not model:
        raise ValueError("a model is required with a base_url: the CLI would ask the endpoint for an Anthropic model name")
    build_model = build_model or model
    build = tuple(tool for tool in BUILD_TOOLS if allow_script or tool != "run_script")
    mcp, run = server_config(server), _Run(part, on_event)

    def options(kind: str, session_model: str | None, tools: tuple[str, ...]) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            model=session_model, system_prompt=(RULES + ENDS[kind]).format(part=part, pdf=pdf), max_turns=max_turns,
            tools=[], allowed_tools=[PREFIX + t for t in tools],
            disallowed_tools=[PREFIX + t for t in (*READ_TOOLS, *BUILD_TOOLS) if t not in tools],  # hidden from the model
            mcp_servers={SERVER: mcp}, strict_mcp_config=True, setting_sources=[], permission_mode="dontAsk",
            env=SESSION_ENV | backend_env(base_url, api_key, session_model),
            extra_args={"no-session-persistence": None},  # no transcript of the drawings in the Claude config folder
        )

    if spec is not None:
        run.phase("build")
        prompt = f'Build the part "{part}" and export it. First call submit_spec with this spec, unchanged:\n{_json(spec | {"name": part})}'
        reason = await run.session(prompt, options("build", build_model, ("submit_spec", *build)), "export_step")
    elif build_model == model:
        run.phase("read")
        reason = await run.session(f"Read the drawing {pdf} and build the part {part}.",
                                   options("full", model, (*READ_TOOLS, *build)), "export_step")
    else:
        run.phase("read")
        reason = await run.session(f"Read the drawing {pdf} and submit the spec of the part {part}.",
                                   options("read", model, READ_TOOLS), "submit_spec")
        if run.spec is None:
            reason = reason or "the session ended without a successful submit_spec"
        else:
            run.part = run.spec.get("name", part)  # the part is registered under the name of its spec
            run.phase("build")
            prompt = f'The spec of the part "{run.part}" is submitted. Build the part and export it.\n{_json(run.spec)}'
            reason = await run.session(prompt, options("build", build_model, build), "export_step")
    result = run.result(reason)
    run.emit({"type": "done"} | result)
    return result


def main(argv: list[str] | None = None) -> int:
    """The command line: prints the result as JSON, the events as JSON lines if asked, exit code 0 only after an export."""
    env = os.environ
    cli = argparse.ArgumentParser(prog="python -m d2c.agent", description="Build a part from a drawing PDF with a model.",
                                  epilog="The API key is read from D2C_LLM_API_KEY only.")
    cli.add_argument("pdf", help="drawing PDF, relative to the data folder of the server")
    cli.add_argument("--part", required=True, help="name of the part (results/<part>/)")
    cli.add_argument("--model", default=env.get("D2C_LLM_MODEL") or None, help="model of the run, or of the reading phase (D2C_LLM_MODEL)")
    cli.add_argument("--build-model", default=env.get("D2C_LLM_BUILD_MODEL") or None, help="model of the building phase (D2C_LLM_BUILD_MODEL)")
    cli.add_argument("--base-url", default=env.get("D2C_LLM_BASE_URL") or None, help="Anthropic-compatible endpoint (D2C_LLM_BASE_URL)")
    cli.add_argument("--server", default=env.get("D2C_MCP_URL") or "stdio", help='"stdio", an http(s) URL or a command line (D2C_MCP_URL)')
    cli.add_argument("--spec", metavar="FILE", help="JSON file with the spec: skips the reading phase")
    cli.add_argument("--no-script", action="store_true", help="hide the run_script tool")
    cli.add_argument("--max-turns", type=int, default=80, help="turns per session (default 80)")
    cli.add_argument("--events", metavar="FILE|-", help="write the events as JSON lines to a file or to stdout")
    args = cli.parse_args(argv)
    events = sys.stdout if args.events == "-" else open(args.events, "w", encoding="utf-8") if args.events else None
    try:
        result = asyncio.run(run_agent(
            args.pdf, args.part, model=args.model, base_url=args.base_url, api_key=env.get("D2C_LLM_API_KEY") or None,
            build_model=args.build_model, server=args.server,
            spec=json.loads(Path(args.spec).read_text(encoding="utf-8")) if args.spec else None,
            allow_script=not args.no_script, max_turns=args.max_turns,
            on_event=(lambda event: print(json.dumps(event, ensure_ascii=False), file=events, flush=True)) if events else None))
    except ValueError as e:
        cli.error(str(e))
    finally:
        if events not in (None, sys.stdout):
            events.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""python -m d2c.agent: einen Agentenlauf für eine Zeichnung über die Kommandozeile starten."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from d2c.agent.backend import Backend
from d2c.agent.runner import MAX_TURNS, run_agent
from d2c.agent.trace import Trace, new_meta
from d2c.config import DEFAULT_MAX_MINUTES, DEFAULT_MAX_USD, llm_settings, mcp_url, results_root


def _parser() -> argparse.ArgumentParser:
    """Die Argumente; die Vorgaben stammen aus den D2C_*-Umgebungsvariablen."""
    llm = llm_settings()
    cli = argparse.ArgumentParser(
        prog="python -m d2c.agent",
        add_help=False,
        usage=argparse.SUPPRESS,
        description="Usage: python -m d2c.agent PDF --part PART [options]\n\nBuilds a part from a drawing PDF with a model.",
        epilog="The API key is read only from D2C_LLM_API_KEY.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    args = cli.add_argument_group("Arguments")
    args.add_argument("pdf", help="drawing PDF, relative to the data folder of the server")
    options = cli.add_argument_group("Options")
    options.add_argument("-h", "--help", action="help", help="Show this help and exit")
    options.add_argument("--part", required=True, help="name of the part (results/<part>/)")
    options.add_argument("--model", default=llm["model"], help="model of the run or of the reading phase (D2C_LLM_MODEL)")
    options.add_argument("--build-model", default=llm["build_model"], help="model of the build phase (D2C_LLM_BUILD_MODEL)")
    options.add_argument("--base-url", default=llm["base_url"], help="Anthropic-compatible endpoint (D2C_LLM_BASE_URL)")
    options.add_argument(
        "--build-base-url", default=llm["build_base_url"], help="endpoint of the build phase if it differs (D2C_LLM_BUILD_BASE_URL)"
    )
    options.add_argument(
        "--lite",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="small tool sets and two sessions (default: on for a non-Claude model with --base-url)",
    )
    options.add_argument("--server", default=mcp_url(), help='"stdio", an http(s) URL or a command line (D2C_MCP_URL)')
    options.add_argument("--spec", metavar="FILE", help="JSON file with the specification: skips the reading phase")
    options.add_argument(
        "--revise",
        action="store_true",
        help="update the existing part (results/<part>/) to a new drawing revision and change only the differences",
    )
    options.add_argument("--no-script", action="store_true", help="hide the run_script tool")
    options.add_argument("--max-turns", type=int, default=MAX_TURNS, help="turns per session (default %(default)s)")
    options.add_argument("--max-usd", type=float, help=f"cost limit of the run in USD, 0 = none (D2C_MAX_USD, default {DEFAULT_MAX_USD:g})")
    options.add_argument(
        "--max-minutes",
        type=float,
        help=f"time limit of the run in minutes, 0 = none (D2C_MAX_MINUTES, default {DEFAULT_MAX_MINUTES:g})",
    )
    options.add_argument("--events", metavar="FILE|-", help="write the events as JSON lines to a file or to stdout")
    return cli


def main(argv: list[str] | None = None) -> int:
    """Die Kommandozeile: gibt das Ergebnis als JSON aus, die Ereignisse auf Wunsch als JSON-Zeilen, Rückkehrcode 0 erst nach einem Export.

    Jeder Lauf legt sein Protokoll unter results/<part>/runs/ ab (dasselbe Format wie die Web-Oberfläche).
    """
    cli = _parser()
    args = cli.parse_args(argv)
    backend = Backend(args.model, args.base_url, llm_settings()["api_key"], args.lite)
    events = sys.stdout if args.events == "-" else open(args.events, "w", encoding="utf-8") if args.events else None
    tracer = Trace(results_root() / args.part, new_meta(args.part, args.pdf, args.model))

    def emit(event: dict) -> None:
        """Jedes Ereignis in das Laufprotokoll und, auf Wunsch, als Zeile nach --events."""
        tracer.event(event)
        if events:
            print(json.dumps(event, ensure_ascii=False), file=events, flush=True)

    try:
        result = asyncio.run(
            run_agent(
                args.pdf,
                args.part,
                backend=backend,
                build=backend.for_build(args.build_model, args.build_base_url),
                server=args.server,
                spec=json.loads(Path(args.spec).read_text(encoding="utf-8")) if args.spec else None,
                revise=args.revise,
                allow_script=not args.no_script,
                max_turns=args.max_turns,
                max_usd=args.max_usd,
                max_minutes=args.max_minutes,
                on_event=emit,
            )
        )
    except ValueError as e:
        cli.error(str(e))
    finally:
        tracer.close()
        if events not in (None, sys.stdout):
            events.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

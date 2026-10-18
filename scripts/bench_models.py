"""Benchmark series: models x parts x mode, a fresh results folder per run, output as JSONL and a Markdown table.

The agent runs on the host (`python -m d2c.agent`), the MCP server per run as a Docker container without network with
the code of this branch as a mount. Modes: `full` (the model reads and builds), `build` (the specification comes from
--results, the model only builds) and `hybrid` (--read-model reads, the local model builds). A model without claude,
sonnet, opus or haiku in its name runs via --ollama-url. Before runs with a local model the script checks via
/api/ps that its context is at least --context (D2C_LLM_CONTEXT, default 64000); otherwise it aborts.

Usage: python scripts/bench_models.py --models sonnet qwen3.8:27b-mlx --parts demo part_a --mode full \
    --drawings drawings --results results --out folder
"""

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "d2c" / "demo"
CLAUDE_NAMES = ("claude", "sonnet", "opus", "haiku")
SCHEMA_ERROR = re.compile(r"\((missing|\w+_type|\w+_parsing|extra_forbidden|literal_error|greater_than\w*|value_error)\)|validation error")
HEADER = ["Model", "Part", "Mode", "ok", "Turns", "Calls", "Schema errors", "Coerced", "Seconds", "Cost"]
MODES = ("full", "build", "hybrid")


def is_local(model: str) -> bool:
    """Ob das Modell über Ollama läuft (kein Claude-Name)."""
    return not any(name in model.lower() for name in CLAUDE_NAMES)


def summarize(events: list[dict]) -> dict:
    """Die Kennzahlen eines Laufs aus seinen Ereignissen. Ohne done-Ereignis (Absturz, Zeitlimit) gilt der Lauf als gescheitert."""
    results = [e for e in events if e.get("type") == "tool_result"]
    done = next((e for e in reversed(events) if e.get("type") == "done"), None)
    return {
        "ok": bool(done and done.get("ok")),
        "turns": (done or {}).get("turns", 0),
        "calls": sum(e.get("type") == "tool_call" for e in events),
        "schema_errors": sum(bool(SCHEMA_ERROR.search(e.get("text", ""))) for e in results),
        "coerced": sum('"coerced"' in e.get("text", "") for e in results),
        "cost_usd": (done or {}).get("cost_usd", 0),
        "reason": (done or {}).get("reason") if done else "no done event (crash or timeout)",
    }


def row(model: str, part: str, mode: str, s: dict) -> str:
    """Die Tabellenzeile eines Laufs."""
    cells = [
        model,
        part,
        mode,
        "yes" if s["ok"] else "no",
        s["turns"],
        s["calls"],
        s["schema_errors"],
        s["coerced"],
        f"{s.get('seconds', 0):.0f}",
        "n/a" if s["cost_usd"] is None else f"${s['cost_usd']:g}",  # None: Lauf ohne ResultMessage
    ]
    return "| " + " | ".join(map(str, cells)) + " |"


def table(rows: list[str]) -> str:
    return "\n".join(["| " + " | ".join(HEADER) + " |", "|" + "---|" * len(HEADER), *rows])


def context_problem(ps: dict, model: str, need: int) -> str | None:
    """Warum der geladene Kontext von `model` nicht reicht (Antwort von /api/ps), oder None."""
    loaded = next((m for m in ps.get("models", []) if model in (m.get("name"), m.get("model"))), None)
    if loaded is None:
        return f"{model} is not loaded in /api/ps after warm-up"
    have = loaded.get("context_length")
    if have is None or have < need:
        return f"{model} runs with context_length={have}, at least {need} is required: start Ollama with OLLAMA_CONTEXT_LENGTH={need}"
    return None


def check_context(ollama_url: str, model: str, need: int) -> None:
    """Lädt das Modell (leere Anfrage) und bricht mit einer Meldung ab, wenn Ollama es mit zu kleinem Kontext hält."""

    def fetch(path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            ollama_url.rstrip("/") + path, data=json.dumps(body).encode() if body else None, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.load(r)

    try:
        fetch("/api/generate", {"model": model, "prompt": "", "stream": False, "keep_alive": "60m"})
        problem = context_problem(fetch("/api/ps"), model, need)
    except OSError as e:
        sys.exit(f"Ollama at {ollama_url} is not reachable: {e}")
    if problem:
        sys.exit(problem)


def agent_command(
    model: str, part: str, mode: str, *, pdf: str, server: str, events: str, ollama_url: str, read_model: str, spec: str | None
) -> list[str]:
    """Die Argumente nach dem Interpreter für `python -m d2c.agent`. In hybrid liest read_model, `model` baut."""
    cmd = ["-m", "d2c.agent", pdf, "--part", part, "--server", server, "--events", events]
    if mode == "hybrid":
        cmd += ["--model", read_model, "--build-model", model, "--build-base-url", ollama_url]
    else:
        cmd += ["--model", model] + (["--base-url", ollama_url] if is_local(model) else [])
    return cmd + (["--spec", spec] if spec else [])


def locate(part: str, drawings: Path) -> tuple[Path, str]:
    """(Datenordner, PDF-Pfad relativ dazu): `demo` ist die Demozeichnung, sonst das erste PDF im Ordner `<teil>*` unter --drawings."""
    if part == "demo":
        return FIXTURES, "demo_drawing.pdf"
    pdfs = sorted(p for d in drawings.glob(f"{part}*") if d.is_dir() for p in d.rglob("*.pdf"))
    if not pdfs:
        sys.exit(f"No drawing for {part!r} under {drawings}")
    return drawings, pdfs[0].relative_to(drawings).as_posix()


def server_command(name: str, data: Path, results: Path, tables: Path | None, image: str) -> str:
    """Der MCP-Server als Docker-Befehl: ohne Netz, Zeichnungen und Tabellen schreibgeschützt, Code dieses Zweigs als Mount."""
    mounts = [(data, "/data:ro")] + ([(tables, "/tables:ro")] if tables else []) + [(results, "/app/results"), (ROOT, "/app")]
    return " ".join(
        [
            "docker run -i --rm --network none --read-only --tmpfs /tmp --name",
            name,
            *(f"-v {shlex.quote(str(src))}:{dst}" for src, dst in mounts),
            image,
            "python -m d2c.server",
        ]
    )


def run_once(args: argparse.Namespace, model: str, part: str, mode: str) -> dict:
    """Ein Lauf in frischem Ergebnisordner; gibt die Kennzahlen samt Sekunden zurück."""
    run = Path(args.out).resolve() / re.sub(r"\W+", "_", model) / f"{part}-{mode}"
    shutil.rmtree(run, ignore_errors=True)
    results = run / "results"
    results.mkdir(parents=True)
    data, pdf = locate(part, args.drawings)
    spec = None
    if mode == "build":
        spec = FIXTURES / "demo_spec.json" if part == "demo" else args.results / part / "spec.json"
        if not spec.is_file():
            sys.exit(f"No specification for {part!r}: {spec}")
    name = f"d2c-bench-{os.getpid()}"
    cmd = [
        args.python,
        *agent_command(
            model,
            part,
            mode,
            pdf=pdf,
            server=server_command(name, data, results, args.tables, args.image),
            events=str(run / "events.jsonl"),
            ollama_url=args.ollama_url,
            read_model=args.read_model,
            spec=str(spec) if spec else None,
        ),
    ]
    env = {k: v for k, v in os.environ.items() if not k.startswith("D2C_LLM_")}
    env |= {"D2C_RESULTS": str(results), "PYTHONPATH": str(ROOT), "D2C_LLM_CONTEXT": str(args.context)}
    if args.thinking:
        env["D2C_LLM_THINKING"] = args.thinking
    print(f"[{time.strftime('%H:%M:%S')}] {model} × {part} ({mode}) ...", flush=True)
    started = time.monotonic()
    with open(run / "agent.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    seconds = time.monotonic() - started
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)  # falls der Container den Agenten überlebte
    events_file = run / "events.jsonl"
    events = (
        [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line.strip()] if events_file.is_file() else []
    )
    summary = summarize(events) | {"seconds": seconds}
    if not any(e.get("type") == "done" for e in events):
        summary["reason"] = f"timeout {args.timeout}s or crash, see {run / 'agent.log'}"
    return summary


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    cli.add_argument("--models", nargs="+", required=True)
    cli.add_argument("--parts", nargs="+", required=True, help="demo or part names of the drawings under --drawings")
    cli.add_argument("--mode", choices=MODES, default="full")
    cli.add_argument("--drawings", type=Path, required=True, help="folder with the drawings (one subfolder per part)")
    cli.add_argument(
        "--results", type=Path, required=True, help="results folder with <part>/spec.json (source of the specification in mode build)"
    )
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--timeout", type=int, default=1800, help="seconds per run")
    cli.add_argument(
        "--tables",
        type=Path,
        default=Path(os.environ["D2C_TABLES"]) if os.environ.get("D2C_TABLES") else None,
        help="folder with the tolerance tables (default D2C_TABLES, otherwise none)",
    )
    cli.add_argument("--ollama-url", default=os.environ.get("D2C_OLLAMA_URL") or "http://localhost:11434")
    cli.add_argument("--context", type=int, default=int(os.environ.get("D2C_LLM_CONTEXT") or 64000))
    cli.add_argument("--thinking", default=os.environ.get("D2C_LLM_THINKING"), help="MAX_THINKING_TOKENS of the local runs")
    cli.add_argument("--read-model", default="sonnet", help="reading model in mode hybrid")
    cli.add_argument("--python", default=str(ROOT / ".venv/bin/python") if (ROOT / ".venv/bin/python").exists() else sys.executable)
    cli.add_argument("--image", default="d2c:latest")
    args = cli.parse_args(argv)

    runs = [(m, p) for m in args.models for p in args.parts if not (args.mode == "hybrid" and not is_local(m))]
    if args.mode == "hybrid" and len(runs) < len(args.models) * len(args.parts):
        print("hybrid: Claude models in --models are skipped (they already read).", file=sys.stderr)
    for part in args.parts:  # alles Fehlende vor dem ersten, langen Lauf melden
        locate(part, args.drawings)
    for model in dict.fromkeys(m for m, _ in runs if is_local(m)):
        check_context(args.ollama_url, model, args.context)
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for model, part in runs:
        summary = run_once(args, model, part, args.mode)
        record = {"model": model, "part": part, "mode": args.mode} | summary
        with open(args.out / "results.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        rows.append(row(model, part, args.mode, summary))
        (args.out / "table.md").write_text(table(rows) + "\n", encoding="utf-8")
        print(rows[-1] + ("" if summary["ok"] else f"   # {summary['reason']}"), flush=True)
    print("\n" + table(rows))
    return 0 if all("| yes |" in r for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

"""Stapellauf: alle Zeichnungs-PDFs eines Ordners über den Agenten, mehrere Teile gleichzeitig.

    python -m d2c.agent.batch ORDNER --jobs 3 [--model ...] [--out results] [--force]

Jede PDF unter ORDNER (rekursiv) ist ein Teil: Liegt sie allein in einem Unterordner, heißt das Teil wie der Ordner
(Ordner mit mehreren PDFs: <Ordner>_<Dateiname>), eine PDF direkt in ORDNER heißt wie die Datei. Jeder Lauf schreibt sein
Protokoll unter <out>/<teil>/runs/ (wie ein Einzel- oder Weblauf), dazu entstehen <out>/batch.csv und <out>/batch.json
mit einer Zeile je Teil. Ein Teil mit gültigem <out>/<teil>/report.json ("ok": true) gilt als fertig und wird ohne
--force übersprungen; so setzt ein abgebrochener Stapel dort fort, wo er stand.

Imports auf Modulebene nur Standardbibliothek und der Agent, damit der Stapel ohne CadQuery läuft.
"""

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from d2c.agent.backend import Backend
from d2c.agent.runner import MAX_TURNS, run_agent
from d2c.agent.trace import Trace, new_meta
from d2c.config import DEFAULT_MAX_MINUTES, DEFAULT_MAX_USD, data_root, llm_settings, mcp_url, results_root

COLUMNS = ("part", "status", "duration_s", "cost_usd", "turns", "export_ok", "review_items", "reason")


def discover(root: Path, skip: Path | None = None) -> list[tuple[str, str]]:
    """(Teilname, PDF-Pfad relativ zu `root`) aller PDFs unter `root`, sortiert; Dateien unter `skip` und in versteckten Ordnern fehlen."""
    pdfs = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() == ".pdf"
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
        and not (skip and p.resolve().is_relative_to(skip.resolve()))
    )
    per_folder = Counter(p.parent for p in pdfs)
    found, used = [], Counter()
    for pdf in pdfs:
        rel = pdf.relative_to(root)
        parts = rel.parent.parts if per_folder[pdf.parent] == 1 and rel.parent.parts else (*rel.parent.parts, pdf.stem)
        name = re.sub(r"[^\w-]", "_", unicodedata.normalize("NFC", "_".join(parts)))  # die Regel von PartSpec.name
        used[name] += 1
        found.append((name if used[name] == 1 else f"{name}_{used[name]}", rel.as_posix()))
    return found


def _finished(part: str) -> bool:
    """True, wenn <out>/<teil>/report.json vorliegt und ok ist."""
    try:
        return json.loads((results_root() / part / "report.json").read_text(encoding="utf-8")).get("ok") is True
    except (OSError, ValueError, AttributeError):
        return False


def _review_items(part: str) -> int | None:
    try:
        return len(json.loads((results_root() / part / "spec.json").read_text(encoding="utf-8"))["review"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write(out: Path, rows: list[dict]) -> None:
    """Die Übersicht als batch.json und batch.csv (UTF-8, Komma als Trenner)."""
    (out / "batch.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out / "batch.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _previous(out: Path) -> dict[str, dict]:
    try:
        return {row["part"]: row for row in json.loads((out / "batch.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


async def run_batch(
    root: Path, out: Path, *, jobs: int = 1, force: bool = False, agent: Callable = run_agent, log: Callable[[str], None] = print, **options
) -> list[dict]:
    """Läuft alle Teile unter `root` mit höchstens `jobs` gleichzeitig; `options` gehen unverändert an `agent`
    (= run_agent). Gibt die Zeilen der Übersicht zurück, in der Reihenfolge der Teile."""
    out.mkdir(parents=True, exist_ok=True)
    os.environ["D2C_RESULTS"] = str(out.absolute())  # Protokolle, Ergebnisse und der Server (stdio) schreiben hierher
    parts = discover(root, skip=out)
    # Das Datenverzeichnis des Servers: gesetzt und passend, sonst ORDNER selbst.
    data = data_root().absolute()
    if not root.absolute().is_relative_to(data):
        os.environ["D2C_DATA"], data = str(root.absolute()), root.absolute()
    prefix = root.absolute().relative_to(data)
    previous, rows = _previous(out), {}
    semaphore = asyncio.Semaphore(max(jobs, 1))

    def save() -> None:
        _write(out, [rows[part] for part, _ in parts if part in rows])

    async def one(part: str, pdf: str) -> None:
        if not force and _finished(part):
            rows[part] = (
                {"review_items": _review_items(part), "reason": "already finished"}
                | previous.get(part, {})
                | {"part": part, "status": "ok", "export_ok": True}
            )
            log(f"{part}: skipped (finished)")
            return
        async with semaphore:
            tracer = Trace(results_root() / part, new_meta(part, pdf, options["backend"].model))
            started, row = time.monotonic(), {"part": part}
            log(f"{part}: started")
            try:
                result = await agent(str(prefix / pdf), part, on_event=tracer.event, **options)
                row |= {
                    "status": "ok" if result.get("ok") else "failed",
                    "turns": result.get("turns"),
                    "cost_usd": result.get("cost_usd"),
                    "export_ok": bool(result.get("ok")),
                    "reason": result.get("reason"),
                }
            except Exception as e:  # ein Teil darf den Stapel nicht beenden
                tracer.event({"type": "done", "ok": False, "part": part, "reason": f"{type(e).__name__}: {e}"})
                row |= {"status": "error", "export_ok": False, "reason": f"{type(e).__name__}: {e}"}
            finally:
                tracer.close()
            row |= {"duration_s": round(time.monotonic() - started, 1), "review_items": _review_items(part)}
            rows[part] = row
            save()
            log(f"{part}: {row['status']} in {row['duration_s']:g} s" + (f" ({row['reason']})" if row.get("reason") else ""))

    try:
        await asyncio.gather(*(one(part, pdf) for part, pdf in parts))
    finally:
        save()  # auch nach Abbruch bleibt stehen, was fertig wurde
    return [rows[part] for part, _ in parts if part in rows]


def main(argv: list[str] | None = None) -> int:
    """Die Kommandozeile: Rückkehrcode 0, wenn jedes Teil fertig ist (neu oder übersprungen), sonst 1."""
    llm = llm_settings()
    cli = argparse.ArgumentParser(
        prog="python -m d2c.agent.batch",
        description="Builds every drawing PDF in a folder with the agent.",
        epilog="The API key is read only from D2C_LLM_API_KEY.",
    )
    cli.add_argument(
        "folder", help="folder with the drawing PDFs (recursive); a PDF alone in a subfolder becomes the part named after that folder"
    )
    cli.add_argument("--jobs", type=int, default=1, help="parts built in parallel (default 1)")
    cli.add_argument("--out", default="results", help="results folder: run logs, part folders and batch.csv/batch.json (default results)")
    cli.add_argument("--force", action="store_true", help="rebuild parts that already have a valid report.json")
    cli.add_argument("--model", default=llm["model"], help="model of the run or of the reading phase (D2C_LLM_MODEL)")
    cli.add_argument("--build-model", default=llm["build_model"], help="model of the build phase (D2C_LLM_BUILD_MODEL)")
    cli.add_argument("--base-url", default=llm["base_url"], help="Anthropic-compatible endpoint (D2C_LLM_BASE_URL)")
    cli.add_argument("--server", default=mcp_url(), help='"stdio", an http(s) URL or a command line (D2C_MCP_URL)')
    cli.add_argument("--no-script", action="store_true", help="hide the run_script tool")
    cli.add_argument("--max-turns", type=int, default=MAX_TURNS, help="turns per session (default %(default)s)")
    cli.add_argument("--max-usd", type=float, help=f"cost limit per part in USD, 0 = none (D2C_MAX_USD, default {DEFAULT_MAX_USD:g})")
    cli.add_argument(
        "--max-minutes", type=float, help=f"time limit per part in minutes, 0 = none (D2C_MAX_MINUTES, default {DEFAULT_MAX_MINUTES:g})"
    )
    args = cli.parse_args(argv)
    root = Path(args.folder)
    if not root.is_dir():
        cli.error(f"{args.folder!r} is not a folder")
    if args.jobs < 1:
        cli.error("--jobs must be at least 1")
    backend = Backend(args.model, args.base_url, llm["api_key"])
    rows = asyncio.run(
        run_batch(
            root,
            Path(args.out),
            jobs=args.jobs,
            force=args.force,
            backend=backend,
            build=backend.for_build(args.build_model, None),
            server=args.server,
            allow_script=not args.no_script,
            max_turns=args.max_turns,
            max_usd=args.max_usd,
            max_minutes=args.max_minutes,
        )
    )
    done = sum(row["export_ok"] for row in rows)
    cost = sum(row.get("cost_usd") or 0 for row in rows)
    unknown = sum(row.get("cost_usd") is None for row in rows)  # Teile ohne ResultMessage: ihre Kosten fehlen in der Summe
    note = f" (without {unknown} parts of unknown cost)" if unknown else ""
    print(f"{done} of {len(rows)} parts finished, cost per summary {cost:.2f} USD{note}; summary: {Path(args.out) / 'batch.csv'}")
    return 0 if rows and done == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())

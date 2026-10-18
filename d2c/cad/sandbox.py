"""Startet den Aufbau-Worker in einem isolierten Prozess und prüft Skripte vor ihrer Ausführung.

Die eigentliche Sicherheitsgrenze bilden der Container (ohne Netzwerk, als Nutzer ohne Rechte, mit schreibgeschütztem
Dateisystem) und der Worker-Prozess: Zeitlimit, Ressourcenbeschränkungen und ein temporäres Verzeichnis. Die Skriptprüfung
liefert vorab verständliche Fehlermeldungen.
"""

import ast
import functools
import json
import logging
import math
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from d2c.config import worker_timeout
from d2c.errors import error_report

log = logging.getLogger(__name__)

MEMORY_LIMIT = 4 * 2**30  # Adressraum in Byte.
FILE_LIMIT = 200 * 2**20  # Maximale Größe jeder vom Worker geschriebenen Datei in Byte.
STDERR_FILE = "stderr.txt"  # Temporäre Datei; das Kind kann sie höchstens bis FILE_LIMIT füllen.
STDERR_TAIL = 500  # Für eine Absturzmeldung behaltene Bytes aus stderr des Workers.
# Positivliste statt Negativliste: Das Skript bekommt keinen Zugriff auf das Modul cq, sondern einen schmalen Namensraum
# (d2c.cad.ops.SCRIPT_CQ). Zusätzlich lehnt die Prüfung Namen und Attribute ab, über die man aus diesem Namensraum
# herauskäme: Dateien, Prozesse, Module, Code-Ausführung und die Rahmen laufender Generatoren und Ausnahmen.
# Auch Namen und Attribute mit führendem "_" sind verboten.
FORBIDDEN_NAMES = frozenset(
    "open exec eval compile import __import__ globals locals vars getattr setattr delattr input breakpoint "
    "os sys subprocess importlib builtins tempfile io pathlib shutil socket ctypes "
    "system popen exporters importers occ_impl "
    "gi_frame gi_code gi_yieldfrom cr_frame cr_code cr_await ag_frame ag_code f_back f_builtins f_globals f_locals "
    "f_code tb_frame tb_next mro format format_map".split()
)
# Methoden mit diesen Präfixen lesen oder schreiben Dateien (exportStep, exportStl, importBrep, importDXF ...).
FORBIDDEN_PREFIXES = ("export", "import")
# Zusätzliche Sperre (Tiefenverteidigung) mit eigener Meldung: Module und Objekte, über die ein Skript Betriebssystemfunktionen
# erreicht (z. B. `cq.exporters.os.system(...)`). Gesperrt wird jeder Name und jedes Attribut mit diesem Namen, auch wenn der
# Zugriff über ein erlaubtes Objekt führt.
OS_MODULES = frozenset(
    "os sys subprocess shutil socket ctypes importlib builtins pathlib io pickle marshal runpy multiprocessing "
    "signal tempfile glob posix nt inspect gc types codecs logging warnings".split()
)
# Attribute, die Prozesse starten, Dateien verändern oder Code laden.
OS_ATTRIBUTES = frozenset("system popen fork remove unlink rmdir rename open chmod chown kill".split())
OS_ATTRIBUTE_PREFIXES = ("exec", "spawn")
# Attribute, über die sich die Klassenhierarchie durchsuchen oder Code deserialisieren lässt.
INTERNALS = frozenset("mro subclasses load loads".split())
# Schlüsselwörter, die einen Dateipfad annehmen (kleingeschrieben verglichen). `Workplane.text(fontPath=...)` lädt die Datei
# als Schrift und verrät über Erfolg oder Fehler, ob sie existiert (Leseorakel). `path` fehlt absichtlich: bei `sweep(path=...)`
# ist es eine Kurve, kein Dateipfad.
FILE_KEYWORDS = frozenset("fontpath filename fname".split())
# Positionsindex von fontPath: Workplane.text(txt, fontsize, distance, combine, clean, font, fontPath, ...) und
# Compound.makeText(text, size, height, font, fontPath, ...). Weitere Positionsargumente erreichen den Pfad ebenfalls.
TEXT_PATH_ARITY = {"text": 7, "makeText": 5}


def _lookups(node: ast.AST) -> list[str]:
    """Attribut- und Variablennamen, auf die dieser Knoten zugreift."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.MatchClass):  # Auch `case Point(__class__=c)` greift auf ein Attribut zu.
        return node.kwd_attrs
    return []


def check_script(code: str) -> None:
    """Löst ValueError aus, wenn ein Skript importiert, einen verbotenen Namen oder ein verbotenes Attribut nutzt.

    Verboten sind alle Namen und Attribute aus FORBIDDEN_NAMES (auch als Attribut, etwa `cq.exporters.os`), Methoden
    mit den Präfixen export und import sowie jeder Name mit führendem Unterstrich. Zusätzlich sperren OS_MODULES,
    OS_ATTRIBUTES, OS_ATTRIBUTE_PREFIXES und INTERNALS den Weg zu Betriebssystem und Interpreter, mit eigener Meldung.
    Das ist die Vorprüfung mit verständlicher Fehlermeldung; die Grenze bildet der schmale Namensraum, den
    `ScriptOp.apply` dem Skript gibt.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, RecursionError, MemoryError):
        return  # Es kann nichts ausgeführt werden; der Worker meldet den Fehler (auch zu tiefe Verschachtelung) mit dem Vorgangsindex.
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    pending = None  # Erster Fund eines gesperrten Attributs; ein gesperrtes Modul im selben Ausdruck (`os` in `x.os.system`) wird bevorzugt gemeldet.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("Import statements are not allowed in scripts (cq and math are already available).")
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg is None or kw.arg.lower() in FILE_KEYWORDS:
                    raise ValueError(f"File paths are not allowed in run_script: keyword argument {kw.arg or '**'!r}.")
            if isinstance(node.func, ast.Attribute) and node.func.attr in TEXT_PATH_ARITY:
                if any(isinstance(a, ast.Starred) for a in node.args) or len(node.args) >= TEXT_PATH_ARITY[node.func.attr]:
                    raise ValueError("File paths are not allowed in run_script: the font file argument of text().")
        elif isinstance(node, ast.Attribute) and node.attr in TEXT_PATH_ARITY and id(node) not in called:
            raise ValueError(f"`{node.attr}` is not allowed except as a direct call in run_script (no file paths).")
        for name in _lookups(node):
            if name.startswith("_"):
                raise ValueError(f"Names and attributes with a leading underscore are not allowed: {name!r}.")
            if name in OS_MODULES:
                raise ValueError(f"Access to operating system functions is not allowed in run_script: `{name}`")
            if name in OS_ATTRIBUTES or name.startswith(OS_ATTRIBUTE_PREFIXES):
                pending = pending or f"Access to operating system functions is not allowed in run_script: `{name}`"
            elif name in INTERNALS:
                pending = pending or f"Access to interpreter internals is not allowed in run_script: `{name}`"
            elif name in FORBIDDEN_NAMES or (isinstance(node, ast.Attribute) and name.lower().startswith(FORBIDDEN_PREFIXES)):
                pending = pending or f"The name {name!r} is not allowed in scripts."
    if pending:
        raise ValueError(pending)


def _limits(cpu_seconds: int) -> None:
    """Wird im Kindprozess vor der Ausführung aufgerufen und begrenzt Adressraum, Dateigröße und CPU-Zeit.

    Nur auf macOS wird eine Grenze übersprungen, die das Betriebssystem nicht setzen kann (es lehnt RLIMIT_AS in
    dieser Größe ab). Auf Linux, dem Betrieb im Container, wird der Fehler weitergereicht, damit der Worker nie
    unbegrenzt läuft.
    """
    for kind, limit in ((resource.RLIMIT_AS, MEMORY_LIMIT), (resource.RLIMIT_FSIZE, FILE_LIMIT), (resource.RLIMIT_CPU, cpu_seconds)):
        try:
            resource.setrlimit(kind, (limit, limit))
        except (OSError, ValueError):
            if sys.platform != "darwin":
                raise


def run_worker(payload: dict, timeout: float | None = None) -> dict:
    """Baut und prüft die ops der Nutzlast `payload` in einem frischen Worker-Prozess und gibt dessen Bericht zurück.

    Die Nutzlast ist {"ops": [...], "expected": {...} | None, "marks": [...], "export_path": str | None} und
    optional "pmi_spec", "glb_path" und "tol" (siehe d2c.cad.worker).
    timeout ist das Zeitlimit in Sekunden, standardmäßig D2C_TIMEOUT (60). Das Ergebnis enthält immer die
    Schlüssel ok, error, solid, dims, build_notes, roundtrip und dims_found; eine Zeitüberschreitung, ein Absturz oder
    ein abgelehntes Skript ist ein Bericht mit gesetztem `error`.
    """
    limit = timeout if timeout is not None else worker_timeout()
    for index, op in enumerate(payload["ops"]):
        code = op.get("code")
        if op.get("op") == "script" and isinstance(code, str):  # Andere Typen meldet die Validierung des Workers.
            try:
                check_script(code)
            except ValueError as e:
                return error_report(f"ValueError: op {index} (script): {e}")
    # CPU-Zeit ist nur die Ersatzgrenze: Schon der Import von cadquery benötigt etwa 2 s CPU in 1,2 s Laufzeit.
    cpu_seconds = 4 * math.ceil(limit) + 10
    started = time.monotonic()
    log.debug("Worker: %d operations, time limit %gs", len(payload["ops"]), limit)
    with tempfile.TemporaryDirectory() as scratch, open(Path(scratch) / STDERR_FILE, "w+b") as stderr:
        # Eigene Prozessgruppe, damit ein Timeout alle vom Worker gestarteten Prozesse beendet; stderr landet in einer Datei.
        worker = subprocess.Popen(
            [sys.executable, "-I", "-m", "d2c.cad.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            preexec_fn=functools.partial(_limits, cpu_seconds),
            # HOME auf den Arbeitsordner: ezdxf (über cadquery) braucht ein Heimatverzeichnis, auch bei fremder uid (`docker run --user`).
            env={"PATH": os.environ["PATH"], "HOME": scratch},
            cwd=scratch,
            start_new_session=True,
        )
        try:
            stdout, _ = worker.communicate(json.dumps(payload).encode(), timeout=limit)
        except subprocess.TimeoutExpired:
            os.killpg(worker.pid, signal.SIGKILL)
            worker.communicate()
            log.debug("Worker terminated after %gs (time limit)", time.monotonic() - started)
            return error_report(f"Timeout after {limit:g}s")
        stderr.seek(max(0, stderr.seek(0, os.SEEK_END) - STDERR_TAIL))
        crash = stderr.read().decode(errors="replace")
    log.debug("Worker finished: return code %s after %gs", worker.returncode, time.monotonic() - started)
    try:
        return json.loads(stdout)  # Ein fertiger Bericht zählt auch dann, wenn der Prozess beim Beenden abstürzt.
    except ValueError:
        pass
    if worker.returncode in (-signal.SIGKILL, -signal.SIGXCPU):
        return error_report(f"Timeout after {limit:g}s (CPU limit)")
    return error_report("Worker crashed: " + crash)

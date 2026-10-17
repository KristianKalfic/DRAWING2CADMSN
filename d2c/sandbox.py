"""Startet den Aufbau-Worker in einem isolierten Prozess und prüft Skripte vor ihrer Ausführung.

Die eigentliche Sicherheitsgrenze bilden der Container (ohne Netzwerk) und der Worker-Prozess:
Zeitlimit, Ressourcenbeschränkungen und ein temporäres Verzeichnis. Die Skriptprüfung liefert vorab
verständliche Fehlermeldungen.
"""
import ast
import functools
import json
import math
import os
import resource
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

DEFAULT_TIMEOUT = 60.0  # Sekunden Laufzeit; D2C_TIMEOUT überschreibt diesen Wert.
MEMORY_LIMIT = 4 * 2**30  # Adressraum in Byte.
FILE_LIMIT = 200 * 2**20  # Maximale Größe jeder vom Worker geschriebenen Datei in Byte.
STDERR_FILE = "stderr.txt"  # Temporäre Datei; das Kind kann sie höchstens bis FILE_LIMIT füllen.
STDERR_TAIL = 500  # Für eine Absturzmeldung behaltene Bytes aus stderr des Workers.
# Namen zum Öffnen von Dateien, Ausführen von Code oder Zugriff auf Objekte; auch Namen und Attribute mit führendem "_" sind verboten.
FORBIDDEN_NAMES = frozenset(
    "open exec eval compile __import__ globals locals vars getattr setattr delattr input breakpoint".split())


def error_report(error: str) -> dict:
    """Worker-Bericht ohne erzeugtes Modell: `error` ist gesetzt, alle übrigen Felder sind leer."""
    return {"ok": False, "error": error, "solid": None, "dims": [], "build_notes": [], "roundtrip": None}


def validation_error_message(error: ValidationError) -> str:
    """Gibt Pydantic-Feldfehler auf Deutsch aus und erhält dabei Feldpfade sowie Fehlertypen."""
    messages = {
        "missing": "fehlt",
        "extra_forbidden": "ist nicht zulässig",
        "greater_than": "muss größer als {gt} sein",
        "greater_than_equal": "muss mindestens {ge} sein",
        "less_than": "muss kleiner als {lt} sein",
        "less_than_equal": "darf höchstens {le} sein",
        "string_pattern_mismatch": "muss dem Muster {pattern!r} entsprechen",
        "string_too_short": "Textlänge muss mindestens {min_length} Zeichen betragen",
        "string_too_long": "Textlänge darf höchstens {max_length} Zeichen betragen",
        "too_short": "Anzahl der Einträge muss mindestens {min_length} betragen",
        "too_long": "Anzahl der Einträge darf höchstens {max_length} betragen",
        "union_tag_invalid": "enthält für {discriminator} den unbekannten Typ {tag!r}; zulässig sind: {expected_tags}",
        "union_tag_not_found": "benötigt die Typangabe {discriminator}",
        "literal_error": "muss einen der zulässigen Werte annehmen: {expected}",
        "int_parsing": "muss eine Ganzzahl sein",
        "int_type": "muss eine Ganzzahl sein",
        "int_from_float": "muss eine Ganzzahl ohne Nachkommastellen sein",
        "float_parsing": "muss eine Zahl sein",
        "float_type": "muss eine Zahl sein",
        "string_type": "muss Text sein",
        "list_type": "muss eine Liste sein",
        "tuple_type": "muss ein Tupel sein",
        "dict_type": "muss ein Objekt sein",
        "bool_type": "muss ein Wahrheitswert sein (true oder false)",
        "bool_parsing": "muss ein Wahrheitswert sein (true oder false)",
        "finite_number": "muss endlich sein",
    }
    rows = []
    for item in error.errors(include_url=False):
        kind, context = item["type"], item.get("ctx") or {}
        if kind == "literal_error":
            context["expected"] = context["expected"].replace(" or ", ", ")
        message = (str(context["error"]) if kind == "value_error" and "error" in context
                   else messages.get(kind, "ist ungültig").format(**context))
        location = ".".join(map(str, item["loc"])) or "Eingabe"
        rows.append(f"{location}: {message} ({kind})")
    return "; ".join(rows)


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
    """Löst ValueError aus, wenn ein Skript importiert, einen verbotenen Namen nutzt oder auf ein privates Attribut zugreift."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return  # Es kann nichts ausgeführt werden; der Worker meldet den Syntaxfehler mit dem Vorgangsindex.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("Importanweisungen sind in Skripten nicht zulässig (cq und math sind bereits verfügbar).")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise ValueError(f"Der Name {node.id!r} ist in Skripten nicht zulässig.")
        for name in _lookups(node):
            if name.startswith("_"):
                raise ValueError(f"Namen und Attribute mit führendem Unterstrich sind nicht zulässig: {name!r}.")


def _limits(cpu_seconds: int) -> None:
    """Wird im Kindprozess vor der Ausführung aufgerufen und begrenzt Adressraum, Dateigröße und CPU-Zeit."""
    for kind, limit in ((resource.RLIMIT_AS, MEMORY_LIMIT), (resource.RLIMIT_FSIZE, FILE_LIMIT),
                        (resource.RLIMIT_CPU, cpu_seconds)):
        resource.setrlimit(kind, (limit, limit))


def run_worker(payload: dict, timeout: float | None = None) -> dict:
    """Baut und prüft die ops der Nutzlast `payload` in einem frischen Worker-Prozess und gibt dessen Bericht zurück.

    The payload is {"ops": [...], "expected": {...} | None, "marks": [...], "export_path": str | None} and
    optionally "pmi_spec", "glb_path" and "tol" (see d2c.worker).
    timeout is the wall clock limit in seconds, by default D2C_TIMEOUT (60). The result always has the
    keys ok, error, solid, dims, build_notes and roundtrip; a timeout, a crash or a rejected script
    is a report with `error` set.
    """
    limit = timeout if timeout is not None else float(os.environ.get("D2C_TIMEOUT", DEFAULT_TIMEOUT))
    for index, op in enumerate(payload["ops"]):
        code = op.get("code")
        if op.get("op") == "script" and isinstance(code, str):  # Andere Typen meldet die Validierung des Workers.
            try:
                check_script(code)
            except ValueError as e:
                return error_report(f"ValueError: op {index} (script): {e}")
    # CPU-Zeit ist nur die Ersatzgrenze: Schon der Import von cadquery benötigt etwa 2 s CPU in 1,2 s Laufzeit.
    cpu_seconds = 4 * math.ceil(limit) + 10
    with tempfile.TemporaryDirectory() as scratch, open(Path(scratch) / STDERR_FILE, "w+b") as stderr:
        # Eigene Prozessgruppe, damit ein Timeout alle vom Worker gestarteten Prozesse beendet; stderr landet in einer Datei.
        worker = subprocess.Popen(
            [sys.executable, "-I", "-m", "d2c.worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
            preexec_fn=functools.partial(_limits, cpu_seconds), env={"PATH": os.environ["PATH"]}, cwd=scratch,
            start_new_session=True)
        try:
            stdout, _ = worker.communicate(json.dumps(payload).encode(), timeout=limit)
        except subprocess.TimeoutExpired:
            os.killpg(worker.pid, signal.SIGKILL)
            worker.communicate()
            return error_report(f"Zeitüberschreitung nach {limit:g}s")
        stderr.seek(max(0, stderr.seek(0, os.SEEK_END) - STDERR_TAIL))
        crash = stderr.read().decode(errors="replace")
    try:
        return json.loads(stdout)  # Ein fertiger Bericht zählt auch dann, wenn der Prozess beim Beenden abstürzt.
    except ValueError:
        pass
    if worker.returncode in (-signal.SIGKILL, -signal.SIGXCPU):
        return error_report(f"Zeitüberschreitung nach {limit:g}s (CPU-Limit)")
    return error_report("Worker abgestürzt: " + crash)
